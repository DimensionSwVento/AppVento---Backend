from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from typing import Optional
from application.services import tile_service
from dataclasses import asdict
from infrastructure.auth.keycloak_guard import require_user

router = APIRouter(prefix="/tiles", tags=["tiles"])

# Autenticación obligatoria (no configurable): todos los endpoints de datos exigen
# identidad verificada. Solo `warmup`/`health` quedan abiertos por diseño.
_auth = [Depends(require_user)]


@router.get("/warmup")
async def warmup():
    if tile_service.index_ready():
        return JSONResponse(content={"status": "ready"})
    try:
        result = await run_in_threadpool(tile_service.warmup)
        return JSONResponse(content=result)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=f"Parquet file not found: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/variables", dependencies=_auth)
def get_variables():
    if not tile_service.index_ready():
        return JSONResponse(content={"variables": []})
    return JSONResponse(content=tile_service.get_variables())


@router.get("/counts", dependencies=_auth)
def get_counts(
    zoom: int = Query(default=6),
    var: Optional[str] = Query(default=None),
    selected: Optional[str] = Query(default=None),
):
    if not tile_service.index_ready():
        return JSONResponse(content={"total": 0, "selected": 0, "aboveThreshold": 0})
    selected_list = [s for s in (selected or "").split(",") if s]
    return JSONResponse(content=tile_service.get_counts(zoom, selected_list, var))


@router.get("/detail/{h3_index}", dependencies=_auth)
def get_detail(h3_index: str):
    if not tile_service.index_ready():
        return JSONResponse(content={"error": "loading"}, status_code=202)
    detail = tile_service.get_detail(h3_index)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Hex {h3_index} not found.")
    return JSONResponse(content=asdict(detail))


@router.get("/{z}/{x}/{y}", dependencies=_auth)
def get_tile(z: int, x: int, y: int, var: Optional[str] = Query(default=None)):
    if not tile_service.index_ready():
        return JSONResponse(content=[])
    hexes = tile_service.get_tile(z, x, y, var)
    return JSONResponse(
        content=[asdict(h) for h in hexes],
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/heatmap/area", dependencies=_auth)
def get_area_heatmap(payload: dict = Body(...)):
    if not tile_service.index_ready():
        return JSONResponse(content={"error": "loading"}, status_code=202)

    polygon    = payload.get("polygon")
    hexes      = payload.get("hexes")
    var        = payload.get("var")
    resolution = payload.get("resolution", 8)

    if not var:
        raise HTTPException(status_code=400, detail="Missing var")
    if not polygon and not hexes:
        raise HTTPException(status_code=400, detail="Missing polygon or hexes")

    return tile_service.get_area_heatmap(polygon, var, resolution, hexes)


@router.post("/area", dependencies=_auth)
def get_area(payload: dict = Body(...)):
    """
    Nuevo método del informe: recibe el POLÍGONO del área seleccionada (o la
    lista de hexes) y devuelve SOLO los hexágonos de esa área con su score
    general, scores por dimensión y valores de variables (detalle).
    """
    if not tile_service.index_ready():
        return JSONResponse(content={"error": "loading"}, status_code=202)

    polygon    = payload.get("polygon")
    hexes      = payload.get("hexes")
    resolution = payload.get("resolution", 8)

    if not polygon and not hexes:
        raise HTTPException(status_code=400, detail="Falta 'polygon' o 'hexes'")

    result = tile_service.get_area_report(polygon=polygon, hexes=hexes, resolution=resolution)
    return JSONResponse(content=[asdict(h) for h in result])
