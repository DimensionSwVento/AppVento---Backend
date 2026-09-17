"""
Router de actualización de la MASTER (US04-03 · admin). El admin consulta el
estado y lanza/programa la actualización del dataset de hexágonos.

Advertencia (la expone `status.warning`): durante la ingesta los informes pueden
salir inconsistentes → el frontend debe avisarlo antes de confirmar.
"""
from __future__ import annotations
from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from application.services import master_service
from infrastructure.auth.keycloak_guard import CurrentUser, require_admin
from infrastructure.db.session import get_db

router = APIRouter(prefix="/admin/master", tags=["admin-master"])


@router.get("/status")
def master_status(
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return master_service.status(db)


@router.post("/update")
def master_update(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """
    Body: `{ mode: "immediate" | "scheduled", scheduledAt?, source? }`.
    - immediate → arranca la ingesta ya (en background).
    - scheduled → la programa para `scheduledAt` (ISO).
    """
    mode = body.get("mode", "immediate")
    if mode not in ("immediate", "scheduled"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="mode inválido")
    try:
        return master_service.request_update(
            db, mode, body.get("scheduledAt"), body.get("source")
        )
    except ValueError as exc:
        # p.ej. ya hay una ingesta en curso, o falta la hora programada.
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
