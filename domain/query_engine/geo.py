"""
Geometría pura H3 (sin estado, sin BD): tile↔bbox y polígono→celdas.
El resolver espacial usa esto para traducir la petición a un conjunto de celdas
o a un bounding box; la obtención de datos la hace el `HexagonSource`.
"""
from __future__ import annotations
import math

import h3

# Resolución del índice del parquet (los hexágonos base están a res 8).
INDEX_RES = 8


def tile_to_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Tile XYZ → (west, south, east, north)."""
    n  = math.pi - (2 * math.pi * y)       / (2 ** z)
    n2 = math.pi - (2 * math.pi * (y + 1)) / (2 ** z)
    west  = (x       / (2 ** z)) * 360 - 180
    east  = ((x + 1) / (2 ** z)) * 360 - 180
    north = (180 / math.pi) * math.atan(0.5 * (math.exp(n)  - math.exp(-n)))
    south = (180 / math.pi) * math.atan(0.5 * (math.exp(n2) - math.exp(-n2)))
    return west, south, east, north


def polygon_to_cells(polygon: list, resolution: int = INDEX_RES) -> set[str]:
    """
    Polígono GeoJSON [[lng, lat], ...] → conjunto de celdas H3 a `resolution`.
    (h3 v4 usa LatLngPoly con orden (lat, lng); el cliente manda (lng, lat).)
    """
    latlng_coords = [(lat, lng) for lng, lat in polygon]
    h3_poly = h3.LatLngPoly(latlng_coords)
    return set(h3.polygon_to_cells(h3_poly, resolution))


def expand_to_index_res(cells: list[str], index_res: int = INDEX_RES) -> set[str]:
    """
    Normaliza una lista de celdas a la resolución del índice: las más gruesas que
    `index_res` se expanden a sus hijos (soporta la selección a res 7 del frontend).
    """
    out: set[str] = set()
    for c in cells:
        try:
            r = h3.get_resolution(c)
        except Exception:
            continue
        if r == index_res:
            out.add(c)
        elif r < index_res:
            out.update(h3.cell_to_children(c, index_res))
        # r > index_res (más fino): se ignora, no existe en el índice base
    return out
