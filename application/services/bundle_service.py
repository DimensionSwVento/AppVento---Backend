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

from infrastructure.db.models import ParquetColumn, DataSource, Hexagon, HexagonMeta
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


def rename_column(db: Session, old_name: str, new_name: str) -> dict | None:
    """Renombra una columna del registro (la fuente cambió el nombre de columna).
    Copia el mapeo (kind/target_ref/usos/…) al nuevo nombre y borra el viejo, para
    que la app siga consumiéndola. `None` si el viejo no existe; lanza ValueError
    si el destino ya existe (no se pisa silenciosamente)."""
    old = db.get(ParquetColumn, old_name)
    if old is None:
        return None
    new_name = new_name.strip()
    if not new_name or new_name == old_name:
        return _to_dict(old)
    if db.get(ParquetColumn, new_name) is not None:
        raise ValueError(f"Ya existe una columna '{new_name}'")
    nuevo = ParquetColumn(
        name=new_name, base_code=base_code(new_name), kind=old.kind,
        target_ref=old.target_ref, usos=old.usos, tipo_dato=old.tipo_dato,
        estado=old.estado, nota=old.nota,
    )
    db.add(nuevo)
    db.delete(old)
    db.commit()
    db.refresh(nuevo)
    return _to_dict(nuevo)


# ── FASE C — APLICAR (ingesta real del bundle → hexagon) ──────────────────────

# Archivos COMBINADOS canónicos que cubren todo (evita leer los per-criterio
# redundantes). Se buscan por basename en el árbol del bundle.
_CANON_RAW = ("variables.h3.parquet",)          # crudo + excluido/alerta
_CANON_SCORES = ("criterios.h3.parquet", "subcriterios.h3.parquet", "mcda_agregado.h3.parquet")
_CANON_PON = ("variables_pon.h3.parquet",)      # ponderadas → hexagon_variables_pon (tabla propia)

# Clave arbitraria del advisory lock de ingesta (single-flight: una a la vez).
_INGEST_LOCK_KEY = 811001


def _select_paths(root: str, files: list[str] | None) -> list[str]:
    """Archivos a ingerir: los dados explícitos, o los canónicos, o —si no hay
    canónicos (subida modular de parquets sueltos)— todos los del bundle."""
    if files:
        return [os.path.join(root, f) for f in files]
    # Incluye variables_pon: en el flujo por chunks (interfaz "subir master") hay
    # que ingerirla también → va a su tabla propia (ver apply_chunk).
    canon = _find(root, _CANON_RAW) + _find(root, _CANON_SCORES) + _find(root, _CANON_PON)
    return canon if canon else _walk_parquets(root)


def _pendientes(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(ParquetColumn).where(ParquetColumn.estado == "pendiente")
    ) or 0


def _kind_of_fn(db: Session):
    """Clasificador columna→clase desde el registro conciliado (exact o base_code)."""
    reg = {c.name: c.kind for c in db.scalars(select(ParquetColumn))}
    reg_bc = {c.base_code: c.kind for c in db.scalars(select(ParquetColumn)) if c.base_code}
    def kind_of(name: str) -> str | None:
        return reg.get(name) or reg_bc.get(base_code(name))
    return kind_of


# Clase de columna → columna de `hexagon` que aporta (para el upsert selectivo).
_KIND_TO_TARGET = {
    "variable_modelo": "variables", "variable_informativa": "variables",
    "alerta": "variables", "bandera": "variables",
    "criterio": "scores", "subcriterio": "scores",
    "global": "score_general", "excluyente": "tipo_celda",
}


def chunk_plan(db: Session, root: str, files: list[str] | None = None) -> dict:
    """Enumera los PAQUETES (un row-group de cada parquet) para ingerir por lotes
    consecutivos desde el front. No lee datos: solo el footer de cada parquet."""
    if not os.path.isdir(root):
        return {"error": f"no es una carpeta: {root}"}
    pend = _pendientes(db)
    if pend:
        return {"error": f"{pend} columnas pendientes de conciliación — resuélvelas antes de aplicar"}
    import pyarrow.parquet as pq  # noqa: PLC0415
    paths = [p for p in _select_paths(root, files) if os.path.exists(p)]
    if not paths:
        return {"error": "no se encontró ningún parquet en el bundle subido"}
    chunks: list[dict] = []
    total_rows = 0
    idx = 0
    for p in paths:
        rel = os.path.relpath(p, root)
        pf = pq.ParquetFile(p)
        for rg in range(pf.num_row_groups):
            n = pf.metadata.row_group(rg).num_rows
            chunks.append({"index": idx, "file": rel, "row_group": rg, "rows": n})
            total_rows += n
            idx += 1
    return {"root": root, "chunks": chunks, "total_chunks": len(chunks), "total_rows": total_rows}


def _trim_memory() -> None:
    """Devuelve al SO la memoria liberada tras un lote. glibc NO la devuelve sola
    → el RSS del worker de gunicorn crece en ingestas largas (aun por chunks)
    hasta el OOM que mata al worker y deja el back unreachable PARA TODOS.
    `malloc_trim(0)` fuerza la devolución; no-op fuera de glibc/Linux."""
    import gc  # noqa: PLC0415
    import ctypes  # noqa: PLC0415
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def apply_chunk(db: Session, root: str, file: str, row_group: int,
                emplazamiento: str = "onshore") -> dict:
    """Ingiere UN paquete (un row-group de un parquet ya conciliado): actualiza
    SOLO las columnas que ese parquet aporta, fusionando el JSONB (no reemplaza).
    Memoria acotada al row-group (leído en tandas). Idempotente por `h3_index`."""
    if not os.path.isdir(root):
        return {"error": f"no es una carpeta: {root}"}
    pend = _pendientes(db)
    if pend:
        return {"error": f"{pend} columnas pendientes de conciliación — resuélvelas antes de aplicar"}

    import pandas as pd  # noqa: PLC0415
    import h3  # noqa: PLC0415
    import pyarrow.parquet as pq  # noqa: PLC0415
    from domain.query_engine.scoring import is_in_colombia
    from infrastructure.db import hexagon_repository as hexrepo

    # Ruta saneada DENTRO de root (evita salir con '..').
    path = os.path.normpath(os.path.join(root, file))
    if not path.startswith(os.path.normpath(root) + os.sep) or not os.path.exists(path):
        return {"error": f"archivo no válido: {file}"}

    pf = pq.ParquetFile(path)
    if row_group < 0 or row_group >= pf.num_row_groups:
        return {"error": f"row_group {row_group} fuera de rango (0..{pf.num_row_groups - 1})"}

    columns = list(pf.schema_arrow.names)
    if "h3_index" not in columns:
        return {"error": f"el parquet '{file}' no tiene columna h3_index"}
    h3_pos = columns.index("h3_index")

    # PONDERADAS: mismo código que las crudas pero valor ponderado → van a su
    # tabla propia (hexagon_variables_pon), NO a hexagon.variables. Se distingue
    # por el NOMBRE del archivo (variables_pon.h3.parquet).
    if os.path.basename(path) in _CANON_PON:
        if emplazamiento != "onshore":
            return {"processed": 0, "file": file, "row_group": row_group,
                    "provided": ["variables_pon (offshore: tabla futura)"]}
        from infrastructure.db import hexagon_pon_repository as ponrepo  # noqa: PLC0415

        def pon_rows():
            for batch in pf.iter_batches(batch_size=20000, row_groups=[row_group]):
                bdf = batch.to_pandas()
                for tup in bdf.itertuples(index=False, name=None):
                    h3i = tup[h3_pos]
                    if not h3i or (isinstance(h3i, float) and pd.isna(h3i)):
                        continue
                    h3i = str(h3i)
                    lat, lng = h3.cell_to_latlng(h3i)
                    if not is_in_colombia(lat, lng):
                        continue
                    vp: dict = {}
                    for name, val in zip(columns, tup):
                        if name == "h3_index":
                            continue
                        if isinstance(val, float) and pd.isna(val):
                            val = None
                        vp[name] = val
                    yield {"h3_index": h3i, "variables_pon": vp}

        n = ponrepo.upsert_pon(db, pon_rows())
        _trim_memory()
        return {"processed": n, "file": file, "row_group": row_group,
                "provided": ["hexagon_variables_pon"]}

    kind_of = _kind_of_fn(db)
    # Qué columnas de hexagon toca este archivo (para el upsert selectivo).
    provided: set[str] = set()
    for name in columns:
        if name == "h3_index":
            continue
        tgt = _KIND_TO_TARGET.get(kind_of(name) or "")
        if tgt:
            provided.add(tgt)

    meta_acc: dict[str, list[float]] = {}

    def rows():
        # Row-group leído en tandas de 20k (bajo consumo aunque sea 1 solo grupo).
        for batch in pf.iter_batches(batch_size=20000, row_groups=[row_group]):
            bdf = batch.to_pandas()
            for tup in bdf.itertuples(index=False, name=None):
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
                hexrepo._accumulate_meta(meta_acc, variables)
                yield {
                    "h3_index": h3i, "score_general": score_general, "tipo_celda": tipo,
                    "lat": lat, "lng": lng, "variables": variables, "scores": scores,
                    "emplazamiento": emplazamiento,
                }

    n = hexrepo.upsert_chunk(db, rows(), provided)
    hexrepo.merge_meta(db, meta_acc)
    # Devuelve la memoria del lote al SO → el RSS del worker NO crece chunk a
    # chunk (evita el OOM que tumbaba el back para todos).
    _trim_memory()
    return {"processed": n, "file": file, "row_group": row_group, "provided": sorted(provided)}


def finalize(db: Session, root: str, files: list[str] | None = None,
             emplazamiento: str = "onshore") -> dict:
    """Cierra una ingesta POR PAQUETES: registra la nueva versión de la máster
    (version++ + fecha + archivo de origen) y actualiza `data_source`. Lo llama el
    front UNA vez, tras subir todos los lotes. Así los informes estampan la versión
    y fuente correctas (el flujo por chunks no lo hacía por lote)."""
    from application.services import master_service  # noqa: PLC0415 (evita ciclo)

    # Anclas oficiales (minmax_anchors.json): el flujo por chunks no las toca (no es
    # parquet) → se cargan aquí, al cerrar. hexagon_meta ya trae los rangos derivados.
    _ingest_anchors(db, root)

    paths = [p for p in _select_paths(root, files) if os.path.exists(p)]
    filenames = (
        ", ".join(os.path.relpath(p, root) for p in paths) if paths else root
    )
    hex_count = db.scalar(select(func.count()).select_from(Hexagon)) or 0
    var_count = db.scalar(select(func.count()).select_from(HexagonMeta)) or 0

    # Fuente de datos: fecha de ingesta + archivos de origen.
    ds = db.get(DataSource, "bundle")
    if ds is None:
        ds = DataSource(id="bundle")
        db.add(ds)
    ds.label = f"Máster ({emplazamiento})"
    ds.kind = "parquet"
    ds.location = root
    ds.rows = hex_count
    ds.status = "ok"
    ds.ingested_at = datetime.now(timezone.utc)
    ds.notes = f"archivos: {filenames}"
    db.commit()

    # Nueva versión de la máster (version++ + fecha + archivo).
    mv = master_service.record_ingest(
        db, source=filenames, hex_count=hex_count, var_count=var_count
    )
    return {
        "version": mv["version"],
        "appliedAt": mv["appliedAt"],
        "source": filenames,
        "hex_count": hex_count,
        "var_count": var_count,
    }


def _find(root: str, basenames: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f in basenames:
                found.append(os.path.join(dirpath, f))
    return found


def _ingest_pon(db: Session, root: str, emplazamiento: str) -> int:
    """Ingiere `variables_pon.h3.parquet` → `hexagon_variables_pon` (tabla propia,
    particionada por emplazamiento). Las columnas son los MISMOS códigos que las
    crudas pero con el VALOR PONDERADO; por eso van a su tabla, no a hexagon.variables.
    Devuelve nº de hex ponderados ingeridos (0 si no está el archivo)."""
    paths = [p for p in _find(root, _CANON_PON) if os.path.exists(p)]
    if not paths:
        return 0
    import pandas as pd  # noqa: PLC0415
    import h3  # noqa: PLC0415
    from domain.query_engine.scoring import is_in_colombia
    from infrastructure.db import hexagon_pon_repository as ponrepo

    df = pd.read_parquet(paths[0])
    if "h3_index" not in df.columns:
        return 0
    columns = list(df.columns)
    h3_pos = columns.index("h3_index")

    def rows():
        for tup in df.itertuples(index=False, name=None):
            h3i = tup[h3_pos]
            if not h3i or (isinstance(h3i, float) and pd.isna(h3i)):
                continue
            h3i = str(h3i)
            lat, lng = h3.cell_to_latlng(h3i)
            if not is_in_colombia(lat, lng):
                continue
            vp: dict = {}
            for name, val in zip(columns, tup):
                if name == "h3_index":
                    continue
                if isinstance(val, float) and pd.isna(val):
                    val = None
                vp[name] = val
            yield {"h3_index": h3i, "variables_pon": vp}

    # Separación por tablas: hoy solo onshore → `hexagon_variables_pon`. El offshore
    # irá a `hexagon_variables_pon_offshore` (tabla nueva) cuando llegue ese bundle.
    if emplazamiento != "onshore":
        return 0
    return ponrepo.upsert_pon(db, rows())


def _ingest_anchors(db: Session, root: str) -> int:
    """Ingiere `minmax_anchors.json` (rangos OFICIALES de normalización por
    subcriterio del modelo) → `hexagon_meta` (variable=nombre del subcriterio,
    vmin/vmax). Reemplaza los rangos derivados del dato por los oficiales para esas
    claves. Devuelve cuántas anclas cargó (0 si no está el archivo)."""
    import json  # noqa: PLC0415
    from infrastructure.db.models import HexagonMeta  # noqa: PLC0415

    paths = _find(root, ("minmax_anchors.json",))
    if not paths or not os.path.exists(paths[0]):
        return 0
    try:
        with open(paths[0], encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return 0
    n = 0
    for name, rng in (data or {}).items():
        if not isinstance(rng, dict):
            continue
        vmin, vmax = rng.get("vmin"), rng.get("vmax")
        if vmin is None or vmax is None:
            continue
        m = db.get(HexagonMeta, str(name))
        if m is None:
            db.add(HexagonMeta(variable=str(name), vmin=float(vmin), vmax=float(vmax)))
        else:
            m.vmin, m.vmax = float(vmin), float(vmax)
        n += 1
    db.commit()
    return n


def apply_detached(db: Session, root: str, emplazamiento: str = "onshore") -> dict:
    """Lanza la ingesta DESTRUCTIVA en un SUBPROCESO AISLADO (su propia memoria).

    Motivación: la ingesta en el worker de gunicorn crece en RSS y, con másters
    pesadas, el kernel mata al worker por OOM → back unreachable PARA TODOS. En
    subproceso, si se come la RAM y muere, **solo muere él**; los workers de la
    API quedan intactos. Es blue-green (la app sigue leyendo `hexagon` durante la
    carga) y el advisory lock evita ingestas simultáneas.

    Devuelve el pid al instante; el estado se consulta en `data_source` (id
    'bundle') — lo actualiza el propio `tools.ingest_bundle`. Cap de memoria
    opcional por env `INGEST_MEM_CAP_MB` (el subproceso falla limpio en vez de
    tumbar el host). POSIX (Linux)."""
    import os  # noqa: PLC0415
    import sys  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import resource  # noqa: PLC0415

    if not os.path.isdir(root):
        return {"error": f"no es una carpeta: {root}"}
    if _pendientes(db):
        return {"error": "hay columnas pendientes de conciliación — resuélvelas antes de aplicar"}

    ds = db.get(DataSource, "bundle")
    if ds is None:
        ds = DataSource(id="bundle")
        db.add(ds)
    ds.status = "updating"
    ds.location = root
    ds.notes = f"ingesta aislada (subproceso) — {emplazamiento}"
    db.commit()

    cap_mb = int(os.getenv("INGEST_MEM_CAP_MB", "0") or 0)

    def _preexec() -> None:
        os.setsid()  # grupo de procesos propio → no muere con el worker de la API
        if cap_mb > 0:
            b = cap_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (b, b))

    # Raíz del proyecto backend (donde vive el paquete `tools`).
    proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    log_path = os.path.join(root, "ingest.log")
    log = open(log_path, "ab")  # noqa: SIM115 — el subproceso hereda el fd
    proc = subprocess.Popen(
        [sys.executable, "-m", "tools.ingest_bundle", root, "--emplazamiento", emplazamiento],
        cwd=proj, stdout=log, stderr=log,
        preexec_fn=_preexec, start_new_session=True, close_fds=True,
    )
    log.close()  # el subproceso ya heredó su propia copia del fd
    return {"pid": proc.pid, "status": "updating", "log": log_path,
            "mem_cap_mb": cap_mb or None}


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
    _trim_memory()     # devuelve la memoria del merge al SO

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

    # Ponderadas → su tabla propia (`hexagon_variables_pon`), si el bundle la trae.
    # Independiente del hexagon crudo; alimenta el "descargar crudos" del informe.
    n_pon = _ingest_pon(db, root, emplazamiento)
    # Anclas oficiales de normalización (minmax_anchors.json) → hexagon_meta.
    _ingest_anchors(db, root)

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

    return {"rows": n, "rows_pon": n_pon, "emplazamiento": emplazamiento,
            "files": [os.path.relpath(p, root) for p in paths]}
