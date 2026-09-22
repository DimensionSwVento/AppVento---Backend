"""
Upsert de las variables PONDERADAS por hex → `hexagon_variables_pon` (Fase 2).
Tabla propia particionada por emplazamiento; upsert modular (fusiona el JSONB por
`(h3_index, emplazamiento)`, no reemplaza). La consume el "descargar crudos" del
informe. Ver `bundle_service._ingest_pon`.
"""
from __future__ import annotations
from collections.abc import Iterable

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from infrastructure.db.models import HexagonVariablesPon


def _clean(vp: dict | None) -> dict:
    """Valores JSON nativos (numpy/NaN → python/None). Igual que hexagon_repository."""
    out: dict[str, str | float | None] = {}
    for k, v in (vp or {}).items():
        if v is None:
            out[str(k)] = None
        elif hasattr(v, "item"):
            native = v.item()
            out[str(k)] = None if native != native else native
        elif isinstance(v, float) and v != v:
            out[str(k)] = None
        else:
            out[str(k)] = v
    return out


def _upsert(db: Session, batch: list[dict]) -> None:
    if not batch:
        return
    stmt = pg_insert(HexagonVariablesPon.__table__).values(batch)
    c = HexagonVariablesPon.__table__.c
    set_ = {
        "variables_pon": func.coalesce(c.variables_pon, text("'{}'::jsonb")).op("||")(
            stmt.excluded.variables_pon
        )
    }
    db.execute(
        stmt.on_conflict_do_update(index_elements=["h3_index"], set_=set_)
    )


def upsert_pon(db: Session, rows: Iterable[dict], batch_size: int = 5000) -> int:
    """Upsertea filas {h3_index, variables_pon} por lotes (tabla onshore)."""
    total = 0
    batch: list[dict] = []
    for r in rows:
        batch.append({
            "h3_index": str(r["h3_index"]),
            "variables_pon": _clean(r.get("variables_pon")),
        })
        if len(batch) >= batch_size:
            _upsert(db, batch)
            db.commit()
            total += len(batch)
            batch = []
    if batch:
        _upsert(db, batch)
        db.commit()
        total += len(batch)
    return total
