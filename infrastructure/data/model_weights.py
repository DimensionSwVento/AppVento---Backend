"""
Pesos del modelo multicriterio (WLC / AHP) — US04-03 · `datos-requeridos §4`.

PLACEHOLDER: todos los pesos = 1.0 → media simple (IDÉNTICO al comportamiento
actual) hasta que llegue el modelo AHP real del equipo de análisis. Al llegar,
llenar:
  - `VARIABLE_WEIGHTS`: peso por CÓDIGO BASE de variable (p.ej. "TEC_001").
  - `DIMENSION_WEIGHTS`: peso por prefijo de dimensión ("TEC", "AMB", "SOE", …).

Fórmula que aplica `tile_service.get_area_report`:
    score_dim = Σ(peso_var · valor_norm) / Σ peso_var
    índice    = Σ(peso_dim · score_dim) / Σ peso_dim
"""
from __future__ import annotations

from infrastructure.data.variable_catalog import base_code

# ⚠️ Vacíos = pesos 1.0 (media simple). Rellenar con el AHP real.
VARIABLE_WEIGHTS: dict[str, float] = {}
DIMENSION_WEIGHTS: dict[str, float] = {}


def variable_weight(var: str) -> float:
    """Peso AHP de una variable (por su código base). Default 1.0."""
    return VARIABLE_WEIGHTS.get(base_code(var), 1.0)


def dimension_weight(cat: str) -> float:
    """Peso AHP de una dimensión (por prefijo). Default 1.0."""
    return DIMENSION_WEIGHTS.get(cat, 1.0)
