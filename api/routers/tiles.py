from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import JSONResponse
from typing import Optional
from dataclasses import asdict

from sqlalchemy.orm import Session

from application.services import tile_service, catalog_service
from domain.query_engine.source import HexagonSource
from api.deps import get_hexagon_source
from infrastructure.db.session import get_db
from infrastructure.auth.keycloak_guard import require_user

router = APIRouter(prefix="/tiles", tags=["tiles"])

# Autenticación obligatoria (no configurable): todos los endpoints de datos exigen
# identidad verificada. Solo `warmup`/`health` quedan abiertos por diseño.
_auth = [Depends(require_user)]


@router.get("/warmup")
def warmup(source: HexagonSource = Depends(get_hexagon_source)):
    # Ya no carga datos a memoria: devuelve el bundle de metadatos (count + meta).
    return JSONResponse(content=tile_service.warmup(source))


@router.get("/variables", dependencies=_auth)
def get_variables(
    source: HexagonSource = Depends(get_hexagon_source),
    db: Session = Depends(get_db),
):
    if not tile_service.index_ready(source):
        return JSONResponse(content={"variables": []})
    # Etiquetas REALES desde `catalog_variable` (BD, ingerido de InfoVariables);
    # el tile ya no depende del stub hardcodeado para nombrar/dimensionar.
    labels = catalog_service.tile_labels(db)
    return JSONResponse(content=tile_service.get_variables(source, labels))


@router.get("/counts", dependencies=_auth)
def get_counts(
    zoom: int = Query(default=6),
    var: Optional[str] = Query(default=None),
    selected: Optional[str] = Query(default=None),
    source: HexagonSource = Depends(get_hexagon_source),
):
    selected_list = [s for s in (selected or "").split(",") if s]
    return JSONResponse(content=tile_service.get_counts(source, zoom, selected_list, var))


@router.get("/detail/{h3_index}", dependencies=_auth)
def get_detail(h3_index: str, source: HexagonSource = Depends(get_hexagon_source)):
    detail = tile_service.get_detail(source, h3_index)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Hex {h3_index} not found.")
    return JSONResponse(content=asdict(detail))


@router.get("/excluded", dependencies=_auth)
def excluded_cells(source: HexagonSource = Depends(get_hexagon_source)):
    """Capa COMPLETA de excluidos en una sola petición → el front la cachea y deja
    de pedir `get_tile` por cada tile solo para marcar zonas no seleccionables."""
    return JSONResponse(content={"cells": tile_service.excluded_cells(source)})


@router.get("/{z}/{x}/{y}", dependencies=_auth)
def get_tile(
    z: int, x: int, y: int,
    var: Optional[str] = Query(default=None),
    source: HexagonSource = Depends(get_hexagon_source),
):
    hexes = tile_service.get_tile(source, z, x, y, var)
    return JSONResponse(
        content=[asdict(h) for h in hexes],
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/heatmap/area", dependencies=_auth)
def get_area_heatmap(
    payload: dict = Body(...),
    source: HexagonSource = Depends(get_hexagon_source),
):
    polygon    = payload.get("polygon")
    hexes      = payload.get("hexes")
    var        = payload.get("var")
    resolution = payload.get("resolution", 8)

    if not var:
        raise HTTPException(status_code=400, detail="Missing var")
    if not polygon and not hexes:
        raise HTTPException(status_code=400, detail="Missing polygon or hexes")

    return tile_service.get_area_heatmap(source, polygon, var, resolution, hexes)


@router.post("/variable", dependencies=_auth)
def get_variable_layer(
    payload: dict = Body(...),
    source: HexagonSource = Depends(get_hexagon_source),
):
    """
    Capa de UNA variable en res8 a nivel país/bbox (US04-03). Alimenta el heatmap
    de viento y la cobertura de áreas del mapa del frontend.
    Body: `{ code, bbox?: {west,south,east,north}, threshold? }`
    → `[{ h3_index, value }]` (value normalizado 0–1).
    """
    code = payload.get("code") or payload.get("var")
    if not code:
        raise HTTPException(status_code=400, detail="Falta 'code'")
    bbox = payload.get("bbox")
    threshold = float(payload.get("threshold") or 0.0)
    subtemplate = bool(payload.get("subtemplate") or False)
    # `res` (resolución del zoom): agrega al hexágono padre → respuesta pequeña y
    # memoria acotada (evita OOM con bbox grande). Sin `res` = res8 por celda.
    res_raw = payload.get("res")
    res = int(res_raw) if res_raw is not None else None
    return JSONResponse(
        content=tile_service.get_variable_layer(source, code, bbox, threshold, subtemplate, res),
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/variables-bulk", dependencies=_auth)
def get_variable_layers(
    payload: dict = Body(...),
    source: HexagonSource = Depends(get_hexagon_source),
):
    """BULK de varias capas en UNA pasada del bbox (evita N scans que apilan hilos).
    Body: `{ codes: [...], bbox?, threshold?, res?, subtemplate? }`
    → `{ code: [{h3_index, value}] }`."""
    codes = payload.get("codes") or []
    if not isinstance(codes, list) or not codes:
        raise HTTPException(status_code=400, detail="Falta 'codes' (lista no vacía)")
    bbox = payload.get("bbox")
    threshold = float(payload.get("threshold") or 0.0)
    subtemplate = bool(payload.get("subtemplate") or False)
    res_raw = payload.get("res")
    res = int(res_raw) if res_raw is not None else None
    return JSONResponse(
        content=tile_service.get_variable_layers(source, codes, bbox, threshold, subtemplate, res),
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/area", dependencies=_auth)
def get_area(
    payload: dict = Body(...),
    source: HexagonSource = Depends(get_hexagon_source),
):
    """
    Método principal del informe: recibe el POLÍGONO del área seleccionada (o la
    lista de hexes) y devuelve SOLO los hexágonos de esa área con su score
    general, scores por dimensión y valores de variables (detalle).
    """
    polygon    = payload.get("polygon")
    hexes      = payload.get("hexes")
    resolution = payload.get("resolution", 8)

    if not polygon and not hexes:
        raise HTTPException(status_code=400, detail="Falta 'polygon' o 'hexes'")

    result = tile_service.get_area_report(source, polygon=polygon, hexes=hexes, resolution=resolution)
    return JSONResponse(content=[asdict(h) for h in result])
