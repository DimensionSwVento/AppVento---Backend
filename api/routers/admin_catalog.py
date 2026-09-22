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
import os
import tempfile

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from application.services import (
    catalog_service, catalog_ingest, metadata_ingest, economic_ingest,
)
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


@router.get("/master-codes")
def master_codes(
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[str]:
    """Códigos reales de la máster (claves de hexagon.variables) para el combobox
    de vinculación del admin. Vacío si aún no se ha ingerido ninguna máster."""
    return catalog_service.master_codes(db)


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


# ── Actualizador de METADATOS (CSV/XLSX) + jerarquía (Fase 1 rediseño) ───────

@router.get("/hierarchy")  # lectura: la app (menú de dimensiones) la consume
def get_hierarchy(
    emplazamiento: str | None = Query(default=None),
    _user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Catálogo anidado dimensión→criterio→subcriterio→variable. `emplazamiento`
    (onshore|offshore) filtra las variables (incluye siempre `both`)."""
    return metadata_ingest.hierarchy(db, emplazamiento)


@router.post("/metadata/upload")
async def metadata_upload(
    file: UploadFile = File(...),
    _admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Sube UN archivo de metadatos (CSV/XLSX) del PC del admin a una ruta temporal
    del servidor y devuelve `path` para /metadata/plan y /metadata/apply."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".csv", ".xlsx", ".xlsm"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Usa un archivo .csv o .xlsx")
    fd, path = tempfile.mkstemp(prefix="vento_meta_", suffix=ext)
    with os.fdopen(fd, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)  # 1 MB streaming
            if not chunk:
                break
            out.write(chunk)
    return {"path": path, "filename": file.filename}


@router.post("/metadata/plan")
def metadata_plan(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Clasifica las columnas de un CSV/XLSX de metadatos (qué se mapea / qué se
    omite). `body`: {path, perfil?, sheet?}."""
    path = str(body.get("path") or "").strip()
    if not path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Falta 'path'")
    result = metadata_ingest.plan(db, path, body.get("perfil") or "infovariables",
                                  body.get("sheet"))
    if "error" in result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return result


@router.get("/metadata/mappings")
def metadata_mappings(
    perfil: str | None = Query(default=None),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    return metadata_ingest.list_mappings(db, perfil)


@router.post("/metadata/mappings")
def metadata_upsert_mapping(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Concilia un encabezado desconocido → campo destino. `body`: {perfil,
    encabezado, destino, transform?, estado?}."""
    perfil = str(body.get("perfil") or "").strip()
    encabezado = str(body.get("encabezado") or "").strip()
    if not perfil or not encabezado:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Falta 'perfil' o 'encabezado'")
    return metadata_ingest.upsert_mapping(db, perfil, encabezado, body)


@router.post("/metadata/apply")
def metadata_apply(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Aplica el doc: upsert de la jerarquía + variables (concilia por master_id).
    `body`: {path, perfil?, sheet?}."""
    path = str(body.get("path") or "").strip()
    if not path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Falta 'path'")
    try:
        result = metadata_ingest.apply(db, path, body.get("perfil") or "infovariables",
                                       body.get("sheet"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Ingesta falló: {exc}")
    if "error" in result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return result


# ── Bundle ECONÓMICO macro (Fase 3) ──────────────────────────────────────────

@router.post("/economic/plan")
def economic_plan(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Lista las hojas del xlsx económico (curadas / solo-bd / meta). `body.path`
    = ruta subida vía /metadata/upload."""
    path = str(body.get("path") or "").strip()
    if not path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Falta 'path'")
    result = economic_ingest.plan(path)
    if "error" in result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return result


@router.post("/economic/apply")
def economic_apply(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Ingiere el xlsx económico → economic_series/economic_panel. Por defecto solo
    las hojas curadas; `onlyCurated=false` ingiere todas (incluye RESULTADOSPOT)."""
    path = str(body.get("path") or "").strip()
    if not path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Falta 'path'")
    try:
        result = economic_ingest.apply(
            db, path, body.get("sheets"),
            only_curated=bool(body.get("onlyCurated", True)),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Ingesta falló: {exc}")
    if "error" in result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return result


@router.get("/economic/panels")  # lectura: el panel económico de la app la consume
def economic_panels(
    curado: bool = Query(default=False),
    _user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return economic_ingest.list_panels(db, only_curated=curado)


@router.get("/economic/series")
def economic_series(
    panel: str = Query(...),
    escenario_mw: int | None = Query(default=None),
    _user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return economic_ingest.series(db, panel, escenario_mw)


@router.get("/economic/scenarios")
def economic_scenarios(
    panel: str = Query(...),
    _user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[int]:
    return economic_ingest.scenarios(db, panel)


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
