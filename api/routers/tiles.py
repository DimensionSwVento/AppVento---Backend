from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import JSONResponse
from typing import Optional
from dataclasses import asdict

from application.services import tile_service
from domain.query_engine.source import HexagonSource
from api.deps import get_hexagon_source
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
def get_variables(source: HexagonSource = Depends(get_hexagon_source)):
    if not tile_service.index_ready(source):
        return JSONResponse(content={"variables": []})
    return JSONResponse(content=tile_service.get_variables(source))


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
