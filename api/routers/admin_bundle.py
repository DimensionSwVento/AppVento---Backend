"""
Router del motor de ingesta por clases (docs/spec/ingesta-clases-conciliacion.md).
Todo exige rol admin.

- POST /admin/bundle/plan            → FASE A: plan de un bundle (clasifica columnas)
- GET  /admin/bundle/columns         → registro columna→clase (filtra por estado)
- PATCH/admin/bundle/columns/{name}  → concilia/crea una columna del registro
"""
from __future__ import annotations
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from application.services import bundle_service
from infrastructure.auth.keycloak_guard import CurrentUser, require_admin
from infrastructure.db.session import get_db

router = APIRouter(prefix="/admin/bundle", tags=["admin-bundle"])


@router.post("/plan")
def plan_bundle(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """FASE A: inspecciona el bundle (solo cabeceras) y devuelve el plan. `body.root`
    = ruta de la carpeta del bundle en el servidor."""
    return bundle_service.plan(db, body.get("root", ""))


@router.get("/columns")
def list_columns(
    estado: str | None = Query(default=None),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Registro columna→clase. `estado` opcional: ok|pendiente|ignorada."""
    return bundle_service.list_columns(db, estado)


@router.patch("/columns/{name}")
def upsert_column(
    name: str,
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Concilia o crea una columna del registro (kind, target_ref, usos, estado…)."""
    return bundle_service.upsert_column(db, name, body)


@router.post("/apply")
def apply_bundle(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """FASE C por INTERFAZ: ingesta UPSERT del bundle → hexagon (actualiza y añade,
    NO borra → no frena los informes). Solo si no hay columnas pendientes. La
    ingesta DESTRUCTIVA (reemplazo total con backup+swap) va por consola:
    `python -m tools.ingest_bundle <carpeta>`. `body.root` = carpeta del bundle."""
    result = bundle_service.apply(
        db, body.get("root", ""), body.get("emplazamiento", "onshore"),
        body.get("files"), mode="upsert",
    )
    if "error" in result:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=result["error"])
    return result
