"""
Operaciones de base de datos sobre `hexagon` (espejo persistido del parquet).

Dos vías de actualización, según lo acordado:
- `replace_all`: ingesta DESTRUCTIVA (borra todo y recarga). Para las muestras del
  modelo mientras llega el dataset definitivo.
- CRUD (`get` / `upsert` / `update` / `delete`): mantenimiento incremental una vez
  la tabla es la fuente de verdad.

`load_for_warmup` reconstruye lo que antes devolvía `read_parquet`, para alimentar
el índice H3 en memoria desde la BD.
"""
from __future__ import annotations
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from domain.models.hexagon import HexagonData
from infrastructure.db.models import Hexagon


# ── Sanitizado ────────────────────────────────────────────────────────────────

def _clean_variables(variables: dict | None) -> dict:
    """
    Normaliza los valores del parquet a tipos JSON nativos (pandas/numpy pueden
    traer np.float64, np.int64, np.nan…). JSONB no serializa escalares numpy.
    """
    out: dict[str, str | float | None] = {}
    for k, v in (variables or {}).items():
        if v is None:
            out[str(k)] = None
        elif hasattr(v, "item"):          # escalares numpy → python nativo
            native = v.item()
            out[str(k)] = None if native != native else native  # NaN → None
        elif isinstance(v, float) and v != v:  # NaN
            out[str(k)] = None
        else:
            out[str(k)] = v
    return out


def _to_row(data: dict) -> dict:
    """Fila lista para insertar/actualizar a partir de un dict de entrada."""
    return {
        "h3_index": str(data["h3_index"]),
        "score_general": float(data.get("score_general") or 0.0),
        "tipo_celda": str(data.get("tipo_celda") or "disponible"),
        "lat": data.get("lat"),
        "lng": data.get("lng"),
        "variables": _clean_variables(data.get("variables")),
    }


# ── Ingesta destructiva ───────────────────────────────────────────────────────

def replace_all(db: Session, hexagons: Iterable[dict], batch_size: int = 5000) -> int:
    """
    Reemplazo total: elimina TODO lo existente e inserta el nuevo conjunto.
    Pensada para recargar desde una muestra del parquet. Es transaccional: si
    falla la carga, no se pierde lo anterior (rollback).
    """
    rows = [_to_row(h) for h in hexagons]

    db.execute(delete(Hexagon))
    for i in range(0, len(rows), batch_size):
        db.bulk_insert_mappings(Hexagon, rows[i : i + batch_size])
    db.commit()
    return len(rows)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def get(db: Session, h3_index: str) -> Hexagon | None:
    return db.get(Hexagon, h3_index)


def list_all(db: Session) -> list[Hexagon]:
    return list(db.scalars(select(Hexagon)))


def count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Hexagon)) or 0


def upsert(db: Session, data: dict) -> Hexagon:
    """Crea el hexágono o actualiza sus campos si ya existe (por `h3_index`)."""
    row = _to_row(data)
    hx = db.get(Hexagon, row["h3_index"])
    if hx is None:
        hx = Hexagon(**row)
        db.add(hx)
    else:
        hx.score_general = row["score_general"]
        hx.tipo_celda = row["tipo_celda"]
        hx.lat = row["lat"]
        hx.lng = row["lng"]
        hx.variables = row["variables"]
    db.commit()
    db.refresh(hx)
    return hx


def update(db: Session, h3_index: str, changes: dict) -> Hexagon | None:
    """Actualiza solo los campos presentes en `changes`. None si no existe."""
    hx = db.get(Hexagon, h3_index)
    if hx is None:
        return None
    if "score_general" in changes:
        hx.score_general = float(changes["score_general"] or 0.0)
    if "tipo_celda" in changes:
        hx.tipo_celda = str(changes["tipo_celda"] or "disponible")
    if "lat" in changes:
        hx.lat = changes["lat"]
    if "lng" in changes:
        hx.lng = changes["lng"]
    if "variables" in changes:
        hx.variables = _clean_variables(changes["variables"])
    db.commit()
    db.refresh(hx)
    return hx


def delete_one(db: Session, h3_index: str) -> bool:
    hx = db.get(Hexagon, h3_index)
    if hx is None:
        return False
    db.delete(hx)
    db.commit()
    return True


# ── Carga para el warmup ──────────────────────────────────────────────────────

def load_for_warmup(db: Session) -> tuple[list[HexagonData], list[str], str]:
    """
    Reconstruye el shape de `read_parquet`: (hexágonos, nombres de variables,
    timestamp). Los nombres de variables se derivan de la unión de claves (el
    índice ya carga todas las filas a memoria, así que el barrido es gratis).
    """
    rows = list_all(db)

    hexagons: list[HexagonData] = []
    variable_names: set[str] = set()
    latest: datetime | None = None

    for r in rows:
        variables = r.variables or {}
        variable_names.update(variables.keys())
        hexagons.append(
            HexagonData(
                h3_index=r.h3_index,
                score_general=r.score_general or 0.0,
                tipo_celda=r.tipo_celda or "disponible",
                variables=variables,
            )
        )
        if r.updated_at and (latest is None or r.updated_at > latest):
            latest = r.updated_at

    parsed_at = (latest or datetime.now(timezone.utc)).isoformat()
    return hexagons, sorted(variable_names), parsed_at
