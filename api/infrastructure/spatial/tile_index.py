"""
Índice espacial H3 — migrado desde h3TileIndex.ts
Almacena todos los hexágonos en memoria, agrupados por resolución.
"""
import math
from dataclasses import dataclass, field
from typing import Optional
import h3
from domain.models.hexagon import HexagonData, TileHex, HexDetail

# ── Tipos internos ────────────────────────────────────────────────────────────

@dataclass
class _IndexedHex:
    h3_index: str
    score_general: float
    tipo_celda: str
    lat: float
    lng: float
    variables: dict[str, str | float | None]


# ── Score temporal de viabilidad (PLACEHOLDER) ────────────────────────────────
# Se reemplazará por el modelo AHP real del equipo de análisis.
# Pesos aproximados, NO validados.

_ROCK_SCORES: dict[str, float] = {
    "muy superficiales": 1.0,
    "superficiales": 0.75,
    "moderadamente profundas": 0.5,
    "profundas": 0.25,
    "muy profundas": 0.1,
}


def _get_numeric(variables: dict, prefix: str) -> Optional[float]:
    for k, v in variables.items():
        if k.startswith(prefix):
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v)
                except ValueError:
                    pass
    return None


def _get_string(variables: dict, prefix: str) -> Optional[str]:
    for k, v in variables.items():
        if k.startswith(prefix) and isinstance(v, str):
            return v
    return None


def calc_temp_viability(variables: dict) -> float:
    wind = _get_numeric(variables, "001_velocidad_de_viento")
    wind_norm = max(0.0, min(1.0, (wind - 3) / 9)) if wind is not None else 0.5

    density = _get_numeric(variables, "004_densidad_energ")
    density_norm = max(0.0, min(1.0, density / 600)) if density is not None else 0.5

    rock = _get_string(variables, "009_profundidad_roca")
    rock_norm = _ROCK_SCORES.get(rock.lower(), 0.5) if rock else 0.5

    reserve = _get_numeric(variables, "soc_022_sr")
    reserve_norm = 1.0 if reserve is None else 0.2

    cig = _get_numeric(variables, "soc_025_er")
    cig_norm = 1 - max(0.0, min(1.0, cig / 100)) if cig is not None else 0.5

    return wind_norm * 0.35 + density_norm * 0.30 + rock_norm * 0.10 + reserve_norm * 0.15 + cig_norm * 0.10


# ── Helpers de tile/resolución ────────────────────────────────────────────────

def zoom_to_h3_res(z: int) -> int:
    if z >= 10: return 7
    if z >= 8:  return 6
    if z >= 7:  return 5
    if z >= 6:  return 4
    return 3


def tile_to_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Returns (west, south, east, north)"""
    n = math.pi - (2 * math.pi * y) / (2 ** z)
    n2 = math.pi - (2 * math.pi * (y + 1)) / (2 ** z)
    west = (x / (2 ** z)) * 360 - 180
    east = ((x + 1) / (2 ** z)) * 360 - 180
    north = (180 / math.pi) * math.atan(0.5 * (math.exp(n) - math.exp(-n)))
    south = (180 / math.pi) * math.atan(0.5 * (math.exp(n2) - math.exp(-n2)))
    return west, south, east, north


# ── Singleton del índice ──────────────────────────────────────────────────────

_indexed_data: Optional[list[_IndexedHex]] = None
_hex_lookup: Optional[dict[str, _IndexedHex]] = None
_resolution_cache: dict[int, list[_IndexedHex]] = {}
_variable_ranges: dict[str, dict[str, float]] = {}  # varName -> {min, max}
_available_variables: list[str] = []


# Polígono simplificado de Colombia continental + mares territoriales [lng, lat]
_COL_CONTINENTAL: list[tuple[float, float]] = [
    (-77.5, 8.6), (-77.4, 9.0), (-76.8, 9.6), (-76.0, 10.0), (-75.5, 11.2),
    (-74.8, 11.5), (-73.5, 12.8), (-72.0, 12.5), (-71.0, 12.0), (-71.3, 11.0),
    (-72.2, 7.6), (-72.4, 7.1), (-72.0, 6.8), (-67.5, 6.2), (-67.0, 4.0),
    (-67.8, 2.0), (-69.8, -0.5), (-70.0, -4.2), (-73.5, -1.5), (-75.2, -0.1),
    (-77.6, 0.8), (-78.8, 1.4), (-79.2, 2.0), (-78.5, 3.5), (-78.0, 4.2),
    (-77.8, 5.5), (-78.5, 6.5), (-77.5, 8.6),
]


def _point_in_polygon(lat: float, lng: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]  # lng, lat
        xj, yj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _is_in_colombia(lat: float, lng: float) -> bool:
    # fast reject
    if lat < -4.3 or lat > 13.5 or lng < -82.0 or lng > -66.5:
        return False
    # San Andrés y Providencia
    if 12.0 <= lat <= 13.5 and -82.0 <= lng <= -81.0:
        return True
    # Malpelo
    if 3.5 <= lat <= 4.2 and -82.0 <= lng <= -81.0:
        return True
    # Continental + mares
    return _point_in_polygon(lat, lng, _COL_CONTINENTAL)


def init_tile_index(hexagons: list[HexagonData], variable_names: list[str]) -> None:
    global _indexed_data, _hex_lookup, _resolution_cache, _variable_ranges, _available_variables

    _available_variables = variable_names
    _resolution_cache = {}

    indexed: list[_IndexedHex] = []
    for hx in hexagons:
        lat, lng = h3.cell_to_latlng(hx.h3_index)
        if not _is_in_colombia(lat, lng):
            continue
        score = hx.score_general if hx.score_general > 0 else calc_temp_viability(hx.variables)
        indexed.append(_IndexedHex(
            h3_index=hx.h3_index,
            score_general=score,
            tipo_celda=hx.tipo_celda,
            lat=lat,
            lng=lng,
            variables=hx.variables,
        ))

    _indexed_data = indexed
    _hex_lookup = {hx.h3_index: hx for hx in indexed}
    _resolution_cache[8] = indexed

    # calcular rangos min/max de variables numéricas
    ranges: dict[str, dict[str, float]] = {}
    for var in variable_names:
        mn, mx = float("inf"), float("-inf")
        for hx in indexed:
            val = hx.variables.get(var)
            num: Optional[float] = None
            if isinstance(val, (int, float)):
                num = float(val)
            elif isinstance(val, str):
                try:
                    num = float(val)
                except ValueError:
                    pass
            if num is not None:
                if num < mn: mn = num
                if num > mx: mx = num
        if mn != float("inf"):
            ranges[var] = {"min": mn, "max": mx}
    _variable_ranges = ranges


def _get_at_resolution(target_res: int) -> list[_IndexedHex]:
    if not _indexed_data:
        return []
    if target_res in _resolution_cache:
        return _resolution_cache[target_res]

    groups: dict[str, dict] = {}
    for d in _indexed_data:
        parent = h3.cell_to_parent(d.h3_index, target_res)
        if parent in groups:
            g = groups[parent]
            g["total_score"] += d.score_general
            g["count"] += 1
            for k, v in d.variables.items():
                num: Optional[float] = None
                if isinstance(v, (int, float)):
                    num = float(v)
                elif isinstance(v, str):
                    try: num = float(v)
                    except ValueError: pass
                if num is not None:
                    if k not in g["var_totals"]:
                        g["var_totals"][k] = {"sum": 0.0, "count": 0}
                    g["var_totals"][k]["sum"] += num
                    g["var_totals"][k]["count"] += 1
        else:
            var_totals: dict[str, dict] = {}
            var_texts: dict[str, str] = {}
            for k, v in d.variables.items():
                num = None
                if isinstance(v, (int, float)):
                    num = float(v)
                elif isinstance(v, str):
                    try: num = float(v)
                    except ValueError: pass
                if num is not None:
                    var_totals[k] = {"sum": num, "count": 1}
                elif isinstance(v, str):
                    var_texts[k] = v
            groups[parent] = {
                "total_score": d.score_general,
                "count": 1,
                "tipo": d.tipo_celda,
                "var_totals": var_totals,
                "var_texts": var_texts,
            }

    aggregated: list[_IndexedHex] = []
    for hex_id, g in groups.items():
        lat, lng = h3.cell_to_latlng(hex_id)
        variables: dict[str, str | float | None] = {}
        for k, vt in g["var_totals"].items():
            variables[k] = vt["sum"] / vt["count"]
        for k, vt in g["var_texts"].items():
            variables[k] = vt
        aggregated.append(_IndexedHex(
            h3_index=hex_id,
            score_general=g["total_score"] / g["count"],
            tipo_celda=g["tipo"],
            lat=lat, lng=lng,
            variables=variables,
        ))

    _resolution_cache[target_res] = aggregated
    return aggregated


# ── API pública ───────────────────────────────────────────────────────────────

def is_index_ready() -> bool:
    return _indexed_data is not None


def get_hexagons_for_tile(z: int, x: int, y: int, var_name: Optional[str] = None) -> list[TileHex]:
    target_res = zoom_to_h3_res(z)
    all_at_res = _get_at_resolution(target_res)
    west, south, east, north = tile_to_bbox(z, x, y)

    lat_margin = (north - south) * 0.1
    lng_margin = (east - west) * 0.1

    result: list[TileHex] = []
    for hx in all_at_res:
        if (south - lat_margin <= hx.lat <= north + lat_margin and
                west - lng_margin <= hx.lng <= east + lng_margin):
            v: Optional[float] = None
            if var_name:
                raw = hx.variables.get(var_name)
                if isinstance(raw, (int, float)):
                    v = float(raw)
                elif isinstance(raw, str):
                    try: v = float(raw)
                    except ValueError: v = None
            result.append(TileHex(h=hx.h3_index, s=hx.score_general, t=hx.tipo_celda,
                                  la=hx.lat, ln=hx.lng, v=v))
    return result


def get_hex_detail(h3_index: str) -> Optional[HexDetail]:
    if not _hex_lookup:
        return None
    hx = _hex_lookup.get(h3_index)
    if not hx:
        return None
    return HexDetail(
        h3_index=hx.h3_index,
        score_general=hx.score_general,
        tipo_celda=hx.tipo_celda,
        lat=hx.lat,
        lng=hx.lng,
        variables=hx.variables,
    )


def get_variable_range(var_name: str) -> Optional[dict[str, float]]:
    return _variable_ranges.get(var_name)


def get_available_variables() -> list[str]:
    return _available_variables


def get_global_counts(zoom: int, selected_hexes: list[str], var_name: Optional[str] = None) -> dict:
    target_res = zoom_to_h3_res(zoom)
    all_at_res = _get_at_resolution(target_res)
    selected_set = set(selected_hexes)
    var_range = _variable_ranges.get(var_name) if var_name else None

    selected = 0
    above_threshold = 0

    for hx in all_at_res:
        if hx.h3_index in selected_set:
            selected += 1

        if var_name and var_range:
            raw = hx.variables.get(var_name)
            num: Optional[float] = None
            if isinstance(raw, (int, float)):
                num = float(raw)
            elif isinstance(raw, str):
                try: num = float(raw)
                except ValueError: pass
            if num is not None:
                rng = var_range["max"] - var_range["min"]
                ratio = (num - var_range["min"]) / rng if rng > 0 else 0
                if ratio > 0.33:
                    above_threshold += 1
        else:
            if hx.score_general > 0.33:
                above_threshold += 1

    return {"total": len(all_at_res), "selected": selected, "aboveThreshold": above_threshold}
