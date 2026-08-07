"""
Router del histórico de informes. Todas las rutas exigen JWT válido (guard) y
operan SOLO sobre los informes del usuario autenticado (`user.sub`).
"""
from __future__ import annotations
from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from application.services import historial_service
from infrastructure.auth.keycloak_guard import CurrentUser, require_user
from infrastructure.db.session import get_db

router = APIRouter(prefix="/historico", tags=["historico"])


@router.get("")
def list_historico(
    user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return historial_service.list_for_user(db, user.sub)


@router.post("", status_code=status.HTTP_201_CREATED)
def save_historico(
    payload: dict = Body(...),
    user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    return historial_service.save_for_user(db, user.sub, payload)


@router.delete("/{informe_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_historico(
    informe_id: str,
    user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    if not historial_service.delete_for_user(db, user.sub, informe_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Informe no encontrado"
        )
