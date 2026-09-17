"""
Ingesta de DOCUMENTOS de datos + registro de FUENTES.

Unifica de dónde sale cada dato real (master parquet + CSV/JSON del catálogo),
permite RE-INGERIR a demanda (admin) y PERSISTE en `data_source` DÓNDE se guarda
cada fuente y el estado/fecha de su última ingesta. Así "dónde se guarda la
master" (y el resto de docs) queda registrado y consultable.

Principio: NADA hardcodeado — cada ingesta lee su archivo de datos (mismo patrón
que la master). Las re-ingestas de catálogo son DESTRUCTIVAS por tabla (reemplazan
el contenido con el del archivo), igual que la master.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from infrastructure.db import catalog_seed as seed
from infrastructure.db.models import (
    CatalogVariable, DataSource, Escenario, Hexagon, Norma, Turbine,
)
from application.services import tile_service
from shared import config

log = logging.getLogger("vento")


# ── Ingestores por doc ────────────────────────────────────────────────────────
# `destructive=False` (default, lo que dispara la INTERFAZ): UPSERT — actualiza los
#   existentes por PK y añade los nuevos; NO borra los que no estén en el archivo.
# `destructive=True` (opcional, solo por CONSOLA — ver `tools/ingest_docs.py`):
#   reemplazo total (borra la tabla y reinserta), igual que la master.

def _apply(db: Session, model, kwargs_rows: list[dict], destructive: bool) -> int:
    if destructive:
        db.execute(delete(model))
        db.add_all(model(**kw) for kw in kwargs_rows)
    else:
        for kw in kwargs_rows:
            db.merge(model(**kw))  # upsert por clave primaria
    return len(kwargs_rows)


def _ingest_variables(db: Session, destructive: bool) -> int:
    if not seed._VARIABLES_SEED.exists():
        raise FileNotFoundError(seed._VARIABLES_SEED.name)
    entries = json.loads(seed._VARIABLES_SEED.read_text(encoding="utf-8"))
    return _apply(db, CatalogVariable, entries, destructive)


def _ingest_normas(db: Session, destructive: bool) -> int:
    rows = seed._read_csv(seed._NORMAS_CSV)
    kwargs = [
        dict(id=r["id"], nombre=r["nombre"], descripcion=r["descripcion"],
             estado=r["estado"])
        for r in rows
    ]
    return _apply(db, Norma, kwargs, destructive)


def _ingest_escenarios(db: Session, destructive: bool) -> int:
    rows = seed._read_csv(seed._ESCENARIOS_CSV)
    kwargs = [
        dict(id=r["id"], label=r["label"], description=r["description"],
             horizonte=int(r["horizonte"]), mult=float(r["mult"]),
             impacto=r["impacto"])
        for r in rows
    ]
    return _apply(db, Escenario, kwargs, destructive)


def _ingest_turbines(db: Session, destructive: bool) -> int:
    rows = seed._read_csv(seed._TURBINES_CSV)
    kwargs = [
        dict(id=r["id"], label=r["label"], detail=r["detail"],
             potencia_min_mw=float(r["potencia_min_mw"]),
             potencia_max_mw=float(r["potencia_max_mw"]),
             area_por_aero_km2=float(r["area_por_aero_km2"]),
             espaciamiento_m=int(r["espaciamiento_m"]))
        for r in rows
    ]
    return _apply(db, Turbine, kwargs, destructive)


def _ingest_master(db: Session, destructive: bool) -> int:
    # La master siempre reemplaza el dataset de hexágonos (parquet completo).
    return tile_service.update_from_parquet(db)


# ── Registro de docs: id → dónde vive + cómo se ingiere + cómo se cuenta ───────

_DOCS: dict[str, dict] = {
    "master": {
        "label": "Master (parquet de hexágonos)",
        "kind": "parquet",
        "location": config.PARQUET_PATH,
        "ingest": _ingest_master,
        "count": lambda db: db.scalar(select(func.count()).select_from(Hexagon)) or 0,
    },
    "variables": {
        # Origen real: CSVs del cliente InfoVariables 1/2/3. Hoy `variables_seed.json`
        # es un proxy generado del front (`tools/gen_variables_seed.py`) hasta que se
        # dropen los CSV reales y se escriba su parser.
        "label": "Catálogo de variables (InfoVariables 1/2/3)",
        "kind": "json",
        "location": str(seed._VARIABLES_SEED),
        "ingest": _ingest_variables,
        "count": lambda db: db.scalar(select(func.count()).select_from(CatalogVariable)) or 0,
    },
    "normas": {
        "label": "Normas regulatorias",
        "kind": "csv",
        "location": str(seed._NORMAS_CSV),
        "ingest": _ingest_normas,
        "count": lambda db: db.scalar(select(func.count()).select_from(Norma)) or 0,
    },
    "escenarios": {
        "label": "Escenarios económicos",
        "kind": "csv",
        "location": str(seed._ESCENARIOS_CSV),
        "ingest": _ingest_escenarios,
        "count": lambda db: db.scalar(select(func.count()).select_from(Escenario)) or 0,
    },
    "turbines": {
        "label": "Aerogeneradores (S/M/L)",
        "kind": "csv",
        "location": str(seed._TURBINES_CSV),
        "ingest": _ingest_turbines,
        "count": lambda db: db.scalar(select(func.count()).select_from(Turbine)) or 0,
    },
}


def _upsert_source(db: Session, sid: str, doc: dict, rows: int,
                   status: str, notes: str = "", stamp: bool = True) -> DataSource:
    ds = db.get(DataSource, sid)
    if ds is None:
        ds = DataSource(id=sid)
        db.add(ds)
    ds.label = doc["label"]
    ds.kind = doc["kind"]
    ds.location = doc["location"]
    ds.rows = rows
    ds.status = status
    ds.notes = notes
    if stamp:
        ds.ingested_at = datetime.now(timezone.utc)
    return ds


def _to_dict(sid: str, doc: dict, ds: DataSource | None) -> dict:
    return {
        "id": sid,
        "label": doc["label"],
        "kind": doc["kind"],
        "location": doc["location"],
        "rows": ds.rows if ds else 0,
        "status": ds.status if ds else "pending",
        "ingestedAt": ds.ingested_at.isoformat() if ds and ds.ingested_at else None,
        "notes": ds.notes if ds else "",
    }


# ── API pública ───────────────────────────────────────────────────────────────

def list_sources(db: Session) -> list[dict]:
    """Todas las fuentes registradas: dónde se guarda cada doc + estado/filas."""
    return [_to_dict(sid, doc, db.get(DataSource, sid)) for sid, doc in _DOCS.items()]


def ingest(db: Session, source_id: str, destructive: bool = False) -> dict | None:
    """
    (Re)ingiere un doc por su id desde su archivo. Registra el resultado en
    `data_source`.
    - `destructive=False` (default, lo que llama la INTERFAZ): update normal
      (upsert: actualiza y añade, sin borrar).
    - `destructive=True` (solo por CONSOLA, `tools/ingest_docs.py`): reemplazo total.
    """
    doc = _DOCS.get(source_id)
    if doc is None:
        return None
    mode = "destructiva" if destructive else "upsert"
    try:
        rows = doc["ingest"](db, destructive)
        ds = _upsert_source(db, source_id, doc, doc["count"](db),
                            "ok", notes=f"{mode}: {rows} filas del archivo")
        db.commit()
        log.info("Ingesta '%s' (%s) OK: %s filas desde %s",
                 source_id, mode, rows, doc["location"])
        return _to_dict(source_id, doc, ds)
    except Exception as exc:  # noqa: BLE001 — registrar el fallo y re-lanzar
        db.rollback()
        ds = _upsert_source(db, source_id, doc, doc["count"](db), "error", str(exc)[:300])
        db.commit()
        log.exception("Ingesta '%s' FALLÓ: %s", source_id, exc)
        raise


def sync_sources(db: Session) -> None:
    """
    Sincroniza `data_source` con el estado actual (sin re-ingerir): cuenta filas
    de cada tabla y registra dónde vive su fuente. Se llama al arrancar, tras el
    seed, para que quede PERSISTIDO dónde se guarda cada doc.
    """
    for sid, doc in _DOCS.items():
        try:
            rows = doc["count"](db)
        except Exception:  # noqa: BLE001
            rows = 0
        status = "ok" if rows > 0 else "pending"
        # No re-estampa la fecha (no fue una ingesta explícita), salvo si es nueva.
        existing = db.get(DataSource, sid)
        _upsert_source(db, sid, doc, rows, status, stamp=existing is None)
    db.commit()
