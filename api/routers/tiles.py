from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from typing import Optional
from application.services import tile_service
from dataclasses import asdict
from shared.config import AUTH_ENABLED
from infrastructure.auth.keycloak_guard import require_auth

router = APIRouter(prefix="/tiles", tags=["tiles"])

# Guard condicional: si AUTH_ENABLED=true exige JWT, si no pasa libre
_auth = [Depends(require_auth)] if AUTH_ENABLED else []


@router.get("/warmup")
def warmup():
    if tile_service.index_ready():
        return JSONResponse(content={"status": "ready"})
    try:
        result = tile_service.warmup()
        return JSONResponse(content=result)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=f"Parquet file not found: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{z}/{x}/{y}", dependencies=_auth)
def get_tile(z: int, x: int, y: int, var: Optional[str] = Query(default=None)):
    if not tile_service.index_ready():
        return JSONResponse(content=[])
    hexes = tile_service.get_tile(z, x, y, var)
    return JSONResponse(
        content=[asdict(h) for h in hexes],
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/detail/{h3_index}", dependencies=_auth)
def get_detail(h3_index: str):
    if not tile_service.index_ready():
        return JSONResponse(content={"error": "loading"}, status_code=202)
    detail = tile_service.get_detail(h3_index)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Hex {h3_index} not found.")
    return JSONResponse(content=asdict(detail))


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
