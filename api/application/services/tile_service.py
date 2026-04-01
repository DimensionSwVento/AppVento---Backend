"""
Capa de aplicación — orquesta parquet reader + tile index.
"""
from shared.config import PARQUET_PATH
from infrastructure.data.parquet_reader import read_parquet, get_variable_label
from infrastructure.spatial.tile_index import init_tile_index, is_index_ready
from infrastructure.spatial import tile_index as _idx
from domain.models.hexagon import TileHex, HexDetail
from typing import Optional


def warmup() -> dict:
    """Carga el parquet e inicializa el índice espacial."""
    hexagons, variable_names, parsed_at = read_parquet(PARQUET_PATH)
    init_tile_index(hexagons, variable_names)
    sample = hexagons[0].h3_index if hexagons else None
    return {
        "status": "ready",
        "count": len(hexagons),
        "variableNames": variable_names,
        "sampleHex": sample,
        "parsedAt": parsed_at,
    }


def get_tile(z: int, x: int, y: int, var_name: Optional[str] = None) -> list[TileHex]:
    return _idx.get_hexagons_for_tile(z, x, y, var_name)


def get_detail(h3_index: str) -> Optional[HexDetail]:
    return _idx.get_hex_detail(h3_index)


def get_variables() -> dict:
    """Retorna formato idéntico al de Next.js: { variables: [{name, label, range}] }"""
    vars_ = _idx.get_available_variables()
    result = []
    for v in vars_:
        rng = _idx.get_variable_range(v)
        result.append({
            "name": v,
            "label": get_variable_label(v),
            "range": rng,  # {min, max} or None
        })
    return {"variables": result}


def get_counts(zoom: int, selected: list[str], var_name: Optional[str] = None) -> dict:
    return _idx.get_global_counts(zoom, selected, var_name)


def index_ready() -> bool:
    return is_index_ready()
