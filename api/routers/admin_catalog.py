"""
Router del CATÁLOGO de administración (US08-01/02/03): variables, normas,
escenarios y turbinas. Las ESCRITURAS exigen rol `admin` (`require_admin`); las
LECTURAS (list) exigen solo usuario autenticado (`require_user`) porque la propia
app (menú de dimensiones, cálculo de capacidad, panel económico) consume estos
datos reales — no solo el panel de admin. Guard dual (BFF interno con
`X-User-Roles` o JWT de Keycloak).

Paths y shapes según `docs/api/admin-catalog.md`.
"""
from __future__ import annotations
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from application.services import catalog_service, catalog_ingest
from infrastructure.auth.keycloak_guard import CurrentUser, require_admin, require_user
from infrastructure.db.session import get_db

router = APIRouter(prefix="/admin/catalog", tags=["admin-catalog"])


# ── Variables y datasets (US08-01) ───────────────────────────────────────────

@router.get("/variables")
def list_variables(
    tipo: str | None = Query(default=None),
    _user: CurrentUser = Depends(require_user),  # lectura: la app la consume
    db: Session = Depends(get_db),
) -> list[dict]:
    return catalog_service.list_variables(db, tipo)


@router.post("/variables", status_code=status.HTTP_201_CREATED)
def create_variable(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return catalog_service.create_variable(db, body)


@router.patch("/variables/{var_id}")
def update_variable(
    var_id: str,
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    updated = catalog_service.update_variable(db, var_id, body)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Variable no encontrada")
    return updated


@router.patch("/datasets/{var_id}")
def update_dataset(
    var_id: str,
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    version = str(body.get("version") or "").strip()
    if not version:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Falta 'version'")
    updated = catalog_service.update_dataset(db, var_id, version)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Variable no encontrada")
    return updated


# ── Normas (US08-02) ──────────────────────────────────────────────────────────

@router.get("/normas")  # lectura: la app (informe regulatorio) la consume
def list_normas(
    _user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return catalog_service.list_normas(db)


@router.post("/normas", status_code=status.HTTP_201_CREATED)
def create_norma(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return catalog_service.create_norma(db, body)


@router.patch("/normas/{norma_id}")
def update_norma(
    norma_id: str,
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    updated = catalog_service.update_norma(db, norma_id, body)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Norma no encontrada")
    return updated


@router.delete("/normas/{norma_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_norma(
    norma_id: str,
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    if not catalog_service.delete_norma(db, norma_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Norma no encontrada")


# ── Escenarios (US08-03) ──────────────────────────────────────────────────────

@router.get("/escenarios")  # lectura: la app (panel económico) la consume
def list_escenarios(
    _user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return catalog_service.list_escenarios(db)


@router.post("/escenarios", status_code=status.HTTP_201_CREATED)
def create_escenario(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return catalog_service.create_escenario(db, body)


@router.patch("/escenarios/{esc_id}")
def update_escenario(
    esc_id: str,
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    updated = catalog_service.update_escenario(db, esc_id, body)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escenario no encontrado")
    return updated


@router.delete("/escenarios/{esc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_escenario(
    esc_id: str,
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    if not catalog_service.delete_escenario(db, esc_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escenario no encontrado")


# ── Turbinas / aerogeneradores (item 4) ───────────────────────────────────────

@router.get("/turbines")  # lectura: la app (cálculo de capacidad) la consume
def list_turbines(
    _user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return catalog_service.list_turbines(db)


@router.patch("/turbines/{turbine_id}")
def update_turbine(
    turbine_id: str,
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    updated = catalog_service.update_turbine(db, turbine_id, body)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turbina no encontrada")
    return updated


# ── Fuentes de datos e ingesta de docs (dónde se guarda cada fuente) ──────────

@router.get("/sources")
def list_sources(
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Registro de fuentes: dónde se guarda cada doc (master/CSV/JSON) + estado."""
    return catalog_ingest.list_sources(db)


@router.post("/ingest/{source}")
def ingest_source(
    source: str,
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Update normal desde el archivo del doc: UPSERT (actualiza y añade, no borra).
    La ingesta DESTRUCTIVA (reemplazo total) es solo por consola
    (`python -m tools.ingest_docs <source> --destructive`)."""
    try:
        result = catalog_ingest.ingest(db, source)  # destructive=False
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Ingesta falló: {exc}")
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Fuente '{source}' no registrada")
    return result
