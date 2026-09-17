"""Operaciones de BD de las versiones de la MASTER (dataset de hexágonos)."""
from __future__ import annotations
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.db.models import MasterVersion


def current(db: Session) -> MasterVersion | None:
    """Versión ACTIVA: la más reciente aplicada (`ready`, mayor `applied_at`)."""
    stmt = (
        select(MasterVersion)
        .where(MasterVersion.status == "ready")
        .order_by(MasterVersion.applied_at.desc().nullslast())
        .limit(1)
    )
    return db.scalars(stmt).first()


def get(db: Session, version_id: str) -> MasterVersion | None:
    return db.get(MasterVersion, version_id)


def create(db: Session, data: dict) -> MasterVersion:
    mv = MasterVersion(**data)
    db.add(mv)
    db.commit()
    db.refresh(mv)
    return mv


def update(db: Session, version_id: str, patch: dict) -> MasterVersion | None:
    mv = db.get(MasterVersion, version_id)
    if mv is None:
        return None
    for key, value in patch.items():
        if hasattr(mv, key):
            setattr(mv, key, value)
    db.commit()
    db.refresh(mv)
    return mv


def is_updating(db: Session) -> bool:
    """Hay una ingesta en curso (los informes de ese lapso pueden ser inconsistentes)."""
    return db.scalar(
        select(MasterVersion.id).where(MasterVersion.status == "updating").limit(1)
    ) is not None


def latest_scheduled(db: Session) -> MasterVersion | None:
    """Actualización programada pendiente (la más próxima)."""
    stmt = (
        select(MasterVersion)
        .where(MasterVersion.status == "scheduled")
        .order_by(MasterVersion.scheduled_at.asc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def due_scheduled(db: Session, now: datetime) -> list[MasterVersion]:
    """Programadas cuya hora ya llegó (para ejecutarlas)."""
    stmt = (
        select(MasterVersion)
        .where(MasterVersion.status == "scheduled")
        .where(MasterVersion.scheduled_at <= now)
        .order_by(MasterVersion.scheduled_at.asc())
    )
    return list(db.scalars(stmt))
