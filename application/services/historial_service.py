"""
Servicio de histórico de informes (por usuario).
Traduce el payload del frontend ↔ modelo `Informe` y aplica la rotación por
límite. La lógica de BD vive en `infrastructure/db/repository`.
"""
from __future__ import annotations
from sqlalchemy.orm import Session

from infrastructure.db import repository
from infrastructure.db.models import Informe
from shared import config


def list_for_user(db: Session, user_id: str) -> list[dict]:
    return [_to_dict(i) for i in repository.list_informes(db, user_id)]


def save_for_user(db: Session, user_id: str, payload: dict) -> dict:
    data = {
        "nombre": payload.get("nombre") or "Informe de prefactibilidad",
        "hexes": payload.get("hexes") or [],
        "variables": payload.get("variables") or [],
        "caracteristicas": payload.get("caracteristicas") or {},
        "scores_snapshot": payload.get("scores") or payload.get("scores_snapshot") or {},
        "region": payload.get("region"),
        "area_km2": float(payload.get("areaKm2") or payload.get("area_km2") or 0.0),
    }
    inf = repository.create_informe(db, user_id, data)
    # Rotación: conservar solo los N más recientes por usuario.
    repository.prune_to_limit(db, user_id, config.MAX_HISTORIAL_PER_USER)
    return _to_dict(inf)


def delete_for_user(db: Session, user_id: str, informe_id: str) -> bool:
    return repository.delete_informe(db, user_id, informe_id)


def _to_dict(i: Informe) -> dict:
    """Shape que espera el frontend (camelCase donde aplica)."""
    return {
        "id": i.id,
        "createdAt": i.created_at.isoformat(),
        "nombre": i.nombre,
        "hexes": i.hexes,
        "variables": i.variables,
        "caracteristicas": i.caracteristicas,
        "scores": i.scores_snapshot,
        "region": i.region,
        "areaKm2": i.area_km2,
    }
