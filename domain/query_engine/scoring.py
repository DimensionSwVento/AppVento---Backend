"""
Lógica pura de score y de pertenencia geográfica (sin estado, sin BD).

Extraído del antiguo índice en memoria para que la INGESTA pueda calcular el
score efectivo y el filtro de Colombia una sola vez (y persistirlos), en vez de
recalcularlos en cada arranque.
"""
from __future__ import annotations
from typing import Optional


# ── Score temporal (PLACEHOLDER) ──────────────────────────────────────────────
# Se reemplazará por el modelo AHP real del equipo de análisis.

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


def calc_temp_viability(variables: dict) -> float:
    wind    = _get_numeric(variables, "TEC_001")
    density = _get_numeric(variables, "TEC_004")
    reserve = _get_numeric(variables, "SOE_001")
    cig     = _get_numeric(variables, "SOE_002")

    wind_norm    = max(0.0, min(1.0, (wind - 3) / 9)) if wind    is not None else 0.5
    density_norm = max(0.0, min(1.0, density / 600))  if density is not None else 0.5
    reserve_norm = 1.0                                 if reserve is None    else 0.2
    cig_norm     = 1 - max(0.0, min(1.0, cig / 100))  if cig     is not None else 0.5

    return wind_norm * 0.40 + density_norm * 0.35 + reserve_norm * 0.15 + cig_norm * 0.10


def effective_score(score_general: float, variables: dict) -> float:
    """Score persistido: el del parquet si es > 0, o el placeholder calculado."""
    return score_general if score_general and score_general > 0 else calc_temp_viability(variables)


# ── Zoom → resolución H3 ──────────────────────────────────────────────────────

def zoom_to_h3_res(z: int) -> int:
    if z >= 10: return 7
    if z >= 8:  return 6
    if z >= 7:  return 5
    if z >= 6:  return 4
    return 3


# ── Pertenencia a Colombia ────────────────────────────────────────────────────

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
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def is_in_colombia(lat: float, lng: float) -> bool:
    if lat < -4.3 or lat > 13.5 or lng < -82.0 or lng > -66.5:
        return False
    if 12.0 <= lat <= 13.5 and -82.0 <= lng <= -81.0:
        return True
    if 3.5 <= lat <= 4.2 and -82.0 <= lng <= -81.0:
        return True
    return _point_in_polygon(lat, lng, _COL_CONTINENTAL)
