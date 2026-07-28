"""
Variable Engine.
Toda la lógica de resolución, lectura y normalización de variables vive aquí.
El tile_index y el engine NO tienen lógica de variables — delegan aquí.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from domain.query_engine.source import HexRow as _IndexedHex


# ── Resolución de nombres ─────────────────────────────────────────────────────

def is_category(var_name: str) -> bool:
    """
    True si var_name es un prefijo de categoría (ej: 'TEC', 'SOE', 'REG').
    Las categorías tienen 3 caracteres o menos.
    Las variables concretas tienen más (ej: 'TEC_001_sv001').
    """
    return len(var_name) <= 3


def resolve_variable_names(prefix: str, available: list[str]) -> list[str]:
    """
    Si prefix es categoría → devuelve todas las variables que pertenecen a ella.
    Si prefix es variable concreta → devuelve [prefix].
    Las variables usan _ como separador: TEC_001_sv001, SOE_002, etc.
    """
    if is_category(prefix):
        return [v for v in available if v.startswith(prefix + "_")]
    return [prefix]


# ── Lectura de valor ──────────────────────────────────────────────────────────

def get_variable_value(hx: "_IndexedHex", var_name: str) -> Optional[float]:
    """Lee el valor numérico de una variable concreta en un hexágono."""
    raw = hx.variables.get(var_name)

    if isinstance(raw, (int, float)):
        return float(raw)

    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return None

    return None


def get_values(hx: "_IndexedHex", var_name: str, available: list[str]) -> list[float]:
    """
    Devuelve lista de valores numéricos para una variable o categoría.
    Si es categoría, agrega todas las sub-variables; si no, devuelve el valor directo.
    """
    vars_ = resolve_variable_names(var_name, available)
    return [v for v in (get_variable_value(hx, v) for v in vars_) if v is not None]


def get_aggregated_value(hx: "_IndexedHex", var_name: str, available: list[str]) -> Optional[float]:
    """Promedio de get_values. Punto de entrada principal para leer una variable."""
    values = get_values(hx, var_name, available)
    return sum(values) / len(values) if values else None


# ── Normalización ─────────────────────────────────────────────────────────────

def normalize(value: float, min_val: float, max_val: float) -> float:
    """Normaliza value al rango [0, 1]. Si min == max devuelve 0."""
    rng = max_val - min_val
    if rng == 0:
        return 0.0
    return max(0.0, min(1.0, (value - min_val) / rng))


def resolve_value(
    hx: "_IndexedHex",
    var_name: str,
    available: list[str],
    var_range: Optional[dict[str, float]],
    mode: str,
) -> tuple[Optional[float], Optional[float]]:
    """
    Punto de entrada principal del variable engine.
    Devuelve (value_raw, value_norm) según el mode pedido.

    mode="raw"        → (raw, None)
    mode="normalized" → (raw, norm)
    mode="score"      → (score_general, None)
    """
    if mode == "score":
        return hx.score_general, None

    raw = get_aggregated_value(hx, var_name, available)

    if raw is None:
        return None, None

    if mode == "normalized" and var_range:
        norm = normalize(raw, var_range["min"], var_range["max"])
        return raw, norm

    return raw, None
