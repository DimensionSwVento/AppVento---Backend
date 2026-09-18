"""
Motor de ingesta por CLASES (docs/spec/ingesta-clases-conciliacion.md).

FASE A (aquí): inspecciona la CABECERA de cada .parquet de un bundle (sin leer
datos) y arma un PLAN clasificando cada columna contra el registro
`parquet_column`. Lo desconocido queda PENDIENTE de conciliación — nunca adivina.

Conciliación: `list_columns` / `upsert_column` mantienen el registro.

FASE C (ingesta real) y la UI viven aparte; esto es el esqueleto que valida el
esquema de los parquets y expone qué se hará y dónde.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from infrastructure.db.models import ParquetColumn, DataSource
from infrastructure.data.variable_catalog import base_code

# kind (clase) → DÓNDE aterriza en hexagon (para el preview del plan).
DESTINO: dict[str, str] = {
    "clave": "hexagon.h3_index (clave)",
    "variable_modelo": "hexagon.variables[code] (puntúa)",
    "variable_informativa": "hexagon.variables[code] (informativa)",
    "alerta": "flag de alerta por hex (marca −2, no bloquea)",
    "excluyente": "hexagon.tipo_celda='excluida' (oculta)",
    "criterio": "hexagon.scores[criterio]",
    "subcriterio": "hexagon.scores[subcriterio]",
    "global": "hexagon.score_general",
    "bandera": "flag por hex",
    "campo": "atributo de otra entidad (no valor por hex)",
    "ignorar": "— (no se consume)",
}


def _schema_columns(path: str) -> tuple[list[tuple[str, str]], int]:
    """(columnas [(name, tipo)], nº filas) leyendo SOLO el footer. Import perezoso
    de pyarrow para no exigirlo donde no hay parquets."""
    import pyarrow.parquet as pq  # noqa: PLC0415 — lazy: solo donde hay bundle
    schema = pq.read_schema(path)
    n_rows = pq.ParquetFile(path).metadata.num_rows
    return [(f.name, str(f.type)) for f in schema], n_rows


def _walk_parquets(root: str) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for f in sorted(filenames):
            if f.endswith(".parquet"):
                out.append(os.path.join(dirpath, f))
    return out


def _classify(db: Session, name: str) -> dict:
    """Clasifica UNA columna contra el registro. Nunca adivina: exact → ok;
    base_code → propuesto; nada → pendiente."""
    col = db.get(ParquetColumn, name)
    if col is not None:
        return {"kind": col.kind, "estado": "ok", "match": "exact",
                "destino": DESTINO.get(col.kind, "?"), "usos": col.usos}
    bc = base_code(name)
    proposal = db.scalars(
        select(ParquetColumn).where(ParquetColumn.base_code == bc)
    ).first()
    if proposal is not None:
        return {"kind": proposal.kind, "estado": "propuesto", "match": "base_code",
                "destino": DESTINO.get(proposal.kind, "?"), "usos": proposal.usos}
    return {"kind": None, "estado": "pendiente", "match": None, "destino": None, "usos": ""}


def plan(db: Session, root: str) -> dict:
    """FASE A: PLAN de qué se hará con cada columna del bundle en `root`."""
    if not os.path.isdir(root):
        return {"error": f"no es una carpeta: {root}"}
    files: list[dict] = []
    columns: list[dict] = []
    seen: set[str] = set()
    for path in _walk_parquets(root):
        try:
            cols, n_rows = _schema_columns(path)
        except Exception as exc:  # noqa: BLE001
            files.append({"path": os.path.relpath(path, root), "error": str(exc)})
            continue
        files.append({"path": os.path.relpath(path, root), "rows": n_rows, "columns": len(cols)})
        for name, tipo in cols:
            if name in seen:
                continue
            seen.add(name)
            c = _classify(db, name)
            columns.append({"name": name, "tipo_dato": tipo, **c})
    summary = {
        "total": len(columns),
        "conocidas": sum(1 for c in columns if c["estado"] == "ok"),
        "propuestas": sum(1 for c in columns if c["estado"] == "propuesto"),
        "pendientes": sum(1 for c in columns if c["estado"] == "pendiente"),
    }
    return {"root": root, "files": files, "columns": columns, "summary": summary}


# ── Registro / conciliación ───────────────────────────────────────────────────

def _to_dict(c: ParquetColumn) -> dict:
    return {
        "name": c.name, "baseCode": c.base_code, "kind": c.kind,
        "targetRef": c.target_ref, "usos": c.usos, "tipoDato": c.tipo_dato,
        "estado": c.estado, "nota": c.nota,
    }


def list_columns(db: Session, estado: str | None = None) -> list[dict]:
    stmt = select(ParquetColumn).order_by(ParquetColumn.name)
    if estado:
        stmt = stmt.where(ParquetColumn.estado == estado)
    return [_to_dict(c) for c in db.scalars(stmt)]


_FIELDS = {"baseCode": "base_code", "kind": "kind", "targetRef": "target_ref",
           "usos": "usos", "tipoDato": "tipo_dato", "estado": "estado", "nota": "nota"}


def upsert_column(db: Session, name: str, body: dict) -> dict:
    """Concilia/crea una columna del registro. `estado` por defecto 'ok'."""
    col = db.get(ParquetColumn, name)
    if col is None:
        col = ParquetColumn(name=name, base_code=base_code(name), estado="ok")
        db.add(col)
    for camel, attr in _FIELDS.items():
        if camel in body and body[camel] is not None:
            setattr(col, attr, body[camel])
    db.commit()
    db.refresh(col)
    return _to_dict(col)


# ── FASE C — APLICAR (ingesta real del bundle → hexagon) ──────────────────────

# Archivos COMBINADOS canónicos que cubren todo (evita leer los per-criterio
# redundantes). Se buscan por basename en el árbol del bundle.
_CANON_RAW = ("variables.h3.parquet",)          # crudo + excluido/alerta
_CANON_SCORES = ("criterios.h3.parquet", "subcriterios.h3.parquet", "mcda_agregado.h3.parquet")

# Clave arbitraria del advisory lock de ingesta (single-flight: una a la vez).
_INGEST_LOCK_KEY = 811001


def _find(root: str, basenames: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f in basenames:
                found.append(os.path.join(dirpath, f))
    return found


def apply(db: Session, root: str, emplazamiento: str = "onshore",
          files: list[str] | None = None, mode: str = "upsert") -> dict:
    """SEGURO de concurrencia: UNA sola ingesta a la vez (advisory lock de
    Postgres). Dos ingestas simultáneas duplican la memoria (cada una carga
    parquets en RAM) → OOM que tumba TODO el backend (mapa/scores incluidos). Si
    ya hay una corriendo, se rechaza; el lock se libera al terminar o si el worker
    muere (se cae la conexión y Postgres lo suelta solo)."""
    got = db.scalar(text("SELECT pg_try_advisory_lock(:k)").bindparams(k=_INGEST_LOCK_KEY))
    if not got:
        return {"error": "Ya hay una ingesta en curso. Espera a que termine antes de subir otra máster."}
    try:
        return _apply_impl(db, root, emplazamiento, files, mode)
    finally:
        db.execute(text("SELECT pg_advisory_unlock(:k)").bindparams(k=_INGEST_LOCK_KEY))


def _apply_impl(db: Session, root: str, emplazamiento: str = "onshore",
                files: list[str] | None = None, mode: str = "upsert") -> dict:
    """
    FASE C: ingesta real del bundle → `hexagon`. Enruta cada columna por su clase.
    Solo corre si NO hay columnas pendientes de conciliación. Registra en
    `data_source`. Dos modos:
    - `upsert` (default, lo que dispara la INTERFAZ): actualiza y añade, SIN borrar
      → no frena los informes.
    - `destructive` (solo por CONSOLA en prod): reemplazo total con backup + swap
      atómico (blue-green); la app lee `hexagon` durante toda la carga.
    """
    if not os.path.isdir(root):
        return {"error": f"no es una carpeta: {root}"}

    pend = db.scalar(
        select(func.count()).select_from(ParquetColumn).where(ParquetColumn.estado == "pendiente")
    ) or 0
    if pend:
        return {"error": f"{pend} columnas pendientes de conciliación — resuélvelas antes de aplicar"}

    # Import perezoso: pandas/h3 solo donde hay bundle real.
    import gc  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    import h3  # noqa: PLC0415
    from functools import reduce
    from domain.query_engine.scoring import is_in_colombia
    from infrastructure.db import hexagon_repository as hexrepo

    # Selección de archivos: los dados, o los combinados canónicos encontrados.
    if files:
        paths = [os.path.join(root, f) for f in files]
    else:
        paths = _find(root, _CANON_RAW) + _find(root, _CANON_SCORES)
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        return {"error": "no se encontraron parquets canónicos (variables/criterios/subcriterios)"}

    # Registro columna→clase (incluye match por base_code).
    reg = {c.name: c.kind for c in db.scalars(select(ParquetColumn))}
    reg_bc = {c.base_code: c.kind for c in db.scalars(select(ParquetColumn)) if c.base_code}

    def kind_of(name: str) -> str | None:
        return reg.get(name) or reg_bc.get(base_code(name))

    # Merge por h3_index (outer). El merge se materializa una vez, pero la
    # ITERACIÓN es perezosa (itertuples) → NO se construye una lista de dicts de
    # todo el dataset (eso era el pico de varios GB que mataba al worker por OOM).
    # El upsert consume este generador por lotes (`batch_size`), así la memoria se
    # mantiene acotada al lote, no al dataset completo.
    dfs = [pd.read_parquet(p) for p in paths]
    merged = reduce(lambda a, b: a.merge(b, on="h3_index", how="outer"), dfs)
    del dfs            # libera los parquets fuente antes de iterar/upsertear
    gc.collect()

    columns = list(merged.columns)
    h3_pos = columns.index("h3_index")

    def rows():
        # itertuples(index=False, name=None) → tuplas planas perezosas (bajo costo
        # de memoria), en vez de to_dict(orient="records") que materializa todo.
        for tup in merged.itertuples(index=False, name=None):
            h3i = tup[h3_pos]
            if not h3i or (isinstance(h3i, float) and pd.isna(h3i)):
                continue
            h3i = str(h3i)
            lat, lng = h3.cell_to_latlng(h3i)
            if not is_in_colombia(lat, lng):
                continue
            variables: dict = {}
            scores: dict = {}
            score_general = 0.0
            tipo = "disponible"
            for name, val in zip(columns, tup):
                if name == "h3_index":
                    continue
                if isinstance(val, float) and pd.isna(val):
                    val = None
                k = kind_of(name)
                if k == "global":
                    score_general = float(val) if val is not None else 0.0
                elif k in ("criterio", "subcriterio"):
                    scores[name] = val
                elif k in ("variable_modelo", "variable_informativa", "alerta", "bandera"):
                    variables[name] = val
                elif k == "excluyente":
                    if val is True or val == 1:
                        tipo = "excluida"
                # clave / ignorar / campo → no van a hexagon aquí
            yield {
                "h3_index": h3i, "score_general": score_general, "tipo_celda": tipo,
                "lat": lat, "lng": lng, "variables": variables, "scores": scores,
                "emplazamiento": emplazamiento,
            }

    if mode == "destructive":
        n = hexrepo.blue_green_replace(db, rows())   # consola/prod: backup + swap
    else:
        n = hexrepo.upsert_all(db, rows())           # interfaz: upsert, no frena informes

    # Registrar la fuente (dónde vive el bundle + resultado).
    ds = db.get(DataSource, "bundle")
    if ds is None:
        ds = DataSource(id="bundle")
        db.add(ds)
    ds.label = f"Bundle del modelo ({emplazamiento})"
    ds.kind = "parquet"
    ds.location = root
    ds.rows = n
    ds.status = "ok"
    ds.ingested_at = datetime.now(timezone.utc)
    ds.notes = f"archivos: {', '.join(os.path.relpath(p, root) for p in paths)}"
    db.commit()

    return {"rows": n, "emplazamiento": emplazamiento,
            "files": [os.path.relpath(p, root) for p in paths]}
