"""
Spatial Resolver.
Recibe un Query y devuelve la lista de _IndexedHex correspondientes.
El engine NO sabe si viene de tile o polygon — solo ve hexágonos.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
import h3

from domain.query_engine.models import Query, TileXYZ

if TYPE_CHECKING:
    from infrastructure.spatial.tile_index import _IndexedHex


# ── Helpers geométricos ───────────────────────────────────────────────────────

def _tile_to_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Convierte tile XYZ a (west, south, east, north)."""
    import math
    n  = math.pi - (2 * math.pi * y)       / (2 ** z)
    n2 = math.pi - (2 * math.pi * (y + 1)) / (2 ** z)
    west  = (x       / (2 ** z)) * 360 - 180
    east  = ((x + 1) / (2 ** z)) * 360 - 180
    north = (180 / math.pi) * math.atan(0.5 * (math.exp(n)  - math.exp(-n)))
    south = (180 / math.pi) * math.atan(0.5 * (math.exp(n2) - math.exp(-n2)))
    return west, south, east, north


def _filter_by_bbox(
    hexes: "list[_IndexedHex]",
    west: float, south: float,
    east: float, north: float,
) -> "list[_IndexedHex]":
    """Filtra hexágonos que caen dentro del bbox con margen del 10%."""
    lat_margin = (north - south) * 0.1
    lng_margin = (east  - west)  * 0.1
    return [
        hx for hx in hexes
        if (south - lat_margin <= hx.lat <= north + lat_margin and
            west  - lng_margin <= hx.lng <= east  + lng_margin)
    ]


# ── Resolver principal ────────────────────────────────────────────────────────

def resolve(query: Query, get_at_resolution, hex_lookup: dict) -> "list[_IndexedHex]":
    """
    Entrada principal del resolver.

    - tile  → bbox → filtro sobre índice en memoria
    - area  → h3.polygon_to_cells → lookup en índice
    """
    if query.type == "tile":
        return _resolve_tile(query, get_at_resolution)
    if query.type == "area":
        return _resolve_area(query, query.resolution or 8, hex_lookup)
    raise ValueError(f"QueryType desconocido: {query.type}")


def _resolve_tile(query: Query, get_at_resolution) -> "list[_IndexedHex]":
    geom: TileXYZ = query.geometry  # type: ignore[assignment]
    from infrastructure.spatial.tile_index import zoom_to_h3_res

    target_res = zoom_to_h3_res(geom.z)
    all_hexes  = get_at_resolution(target_res)
    west, south, east, north = _tile_to_bbox(geom.z, geom.x, geom.y)
    return _filter_by_bbox(all_hexes, west, south, east, north)


def _resolve_area(query: Query, resolution: int, hex_lookup: dict) -> "list[_IndexedHex]":
    polygon: list = query.geometry  # type: ignore[assignment]

    # h3 v4: usa LatLngPoly con coordenadas (lat, lng)
    # El cliente manda [[lng, lat], ...] (GeoJSON), hay que invertir
    latlng_coords = [(lat, lng) for lng, lat in polygon]
    h3_poly = h3.LatLngPoly(latlng_coords)
    cell_set = h3.polygon_to_cells(h3_poly, resolution)

    return [hex_lookup[c] for c in cell_set if c in hex_lookup]
