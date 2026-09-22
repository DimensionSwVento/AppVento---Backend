"""
Router del motor de ingesta por clases (docs/spec/ingesta-clases-conciliacion.md).
Todo exige rol admin.

- POST /admin/bundle/plan            → FASE A: plan de un bundle (clasifica columnas)
- GET  /admin/bundle/columns         → registro columna→clase (filtra por estado)
- PATCH/admin/bundle/columns/{name}  → concilia/crea una columna del registro
"""
from __future__ import annotations
import os
import tempfile

from fastapi import (
    APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile, status,
)
from sqlalchemy.orm import Session

from application.services import bundle_service
from infrastructure.auth.keycloak_guard import CurrentUser, require_admin
from infrastructure.db.session import get_db

router = APIRouter(prefix="/admin/bundle", tags=["admin-bundle"])


def _safe_rel(rel: str, fallback: str) -> str:
    """Ruta relativa saneada (evita salir del directorio con `..`)."""
    rel = (rel or fallback).replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", "..", ".")]
    return "/".join(parts) or fallback


@router.post("/upload")
async def upload_bundle(
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(default=[]),
    _admin: CurrentUser = Depends(require_admin),
) -> dict:
    """Recibe los archivos ARRASTRADOS desde el PC del admin (multipart),
    preservando su ruta relativa (para carpetas), los guarda en una carpeta
    temporal del servidor y devuelve `root` para usarla en /plan y /apply."""
    dest = tempfile.mkdtemp(prefix="vento_bundle_")
    for i, f in enumerate(files):
        rel = paths[i] if i < len(paths) else (f.filename or f"file_{i}")
        target = os.path.join(dest, _safe_rel(rel, f.filename or f"file_{i}"))
        os.makedirs(os.path.dirname(target) or dest, exist_ok=True)
        with open(target, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)  # 1 MB streaming, memoria acotada
                if not chunk:
                    break
                out.write(chunk)
    return {"root": dest, "files": len(files)}


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


@router.post("/chunks")
def bundle_chunks(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Plan de PAQUETES (un row-group por lote) para ingerir de forma consecutiva
    desde el front con progreso. No lee datos, solo el footer de los parquets."""
    result = bundle_service.chunk_plan(db, body.get("root", ""), body.get("files"))
    if "error" in result:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=result["error"])
    return result


@router.post("/apply-chunk")
def bundle_apply_chunk(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Ingiere UN paquete (rápido, memoria acotada): actualiza solo las columnas
    conciliadas del parquet, fusionando el JSONB. Idempotente por `h3_index`."""
    result = bundle_service.apply_chunk(
        db,
        body.get("root", ""),
        body.get("file", ""),
        int(body.get("row_group", 0)),
        body.get("emplazamiento", "onshore"),
    )
    if "error" in result:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=result["error"])
    return result


@router.post("/finalize")
def bundle_finalize(
    body: dict = Body(...),
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Cierra la ingesta por paquetes: registra la NUEVA versión de la máster
    (version++ + fecha + archivo) y actualiza data_source. El front lo llama una
    vez tras subir todos los lotes."""
    return bundle_service.finalize(
        db, body.get("root", ""), body.get("files"),
        body.get("emplazamiento", "onshore"),
    )
