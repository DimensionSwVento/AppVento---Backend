"""Operaciones de base de datos (bajo nivel) sobre usuario e informe."""
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from infrastructure.db.models import AppUser, Informe


# ── Usuario (verificación / provisión desde el token de Keycloak) ────────────

def ensure_user(db: Session, sub: str, email: str | None, roles: list[str]) -> AppUser:
    """
    Verifica el usuario en Postgres; si no existe lo crea. Si existe, refresca
    email/roles cuando cambian (Keycloak es la fuente de verdad).
    """
    user = db.get(AppUser, sub)
    if user is None:
        user = AppUser(sub=sub, email=email, roles=roles or [])
        db.add(user)
        db.commit()
        return user

    changed = False
    if email and user.email != email:
        user.email = email
        changed = True
    if roles and user.roles != roles:
        user.roles = roles
        changed = True
    if changed:
        db.commit()
    return user


# ── Informes ─────────────────────────────────────────────────────────────────

def list_informes(db: Session, user_id: str) -> list[Informe]:
    stmt = (
        select(Informe)
        .where(Informe.user_id == user_id)
        .order_by(Informe.created_at.desc())
    )
    return list(db.scalars(stmt))


def create_informe(db: Session, user_id: str, data: dict) -> Informe:
    inf = Informe(user_id=user_id, **data)
    db.add(inf)
    db.commit()
    db.refresh(inf)
    return inf


def delete_informe(db: Session, user_id: str, informe_id: str) -> bool:
    inf = db.get(Informe, informe_id)
    if inf is None or inf.user_id != user_id:
        return False
    db.delete(inf)
    db.commit()
    return True


def count_informes(db: Session, user_id: str) -> int:
    return db.scalar(
        select(func.count()).select_from(Informe).where(Informe.user_id == user_id)
    ) or 0


def prune_to_limit(db: Session, user_id: str, keep: int) -> int:
    """Rotación: elimina los informes que exceden los `keep` más recientes."""
    old_ids = list(
        db.scalars(
            select(Informe.id)
            .where(Informe.user_id == user_id)
            .order_by(Informe.created_at.desc())
            .offset(keep)
        )
    )
    if not old_ids:
        return 0
    db.execute(delete(Informe).where(Informe.id.in_(old_ids)))
    db.commit()
    return len(old_ids)
