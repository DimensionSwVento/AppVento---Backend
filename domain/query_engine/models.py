"""
Modelos del Query Engine.
Todo request espacial se representa como un Query y produce list[QueryResult].
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Literal

# ── Geometrías de entrada ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class TileXYZ:
    z: int
    x: int
    y: int


Polygon = list[tuple[float, float]]  # [[lng, lat], ...] GeoJSON order

# ── Query ─────────────────────────────────────────────────────────────────────

QueryMode = Literal["raw", "normalized", "score"]
QueryType = Literal["tile", "area"]


@dataclass(frozen=True)
class Query:
    """
    Representación unificada de cualquier consulta espacial.
    TILE y AREA son solo formas distintas de construir este objeto.
    """
    type: QueryType
    geometry: TileXYZ | Polygon
    variable: Optional[str] = None
    resolution: Optional[int] = None  # None → el engine decide
    mode: QueryMode = "raw"


# ── Resultado ─────────────────────────────────────────────────────────────────

@dataclass
class QueryResult:
    """Resultado unificado por hexágono."""
    h3_index: str
    lat: float
    lng: float
    score: float
    tipo_celda: str
    value: Optional[float] = None       # valor raw de la variable
    value_norm: Optional[float] = None  # valor normalizado [0, 1]
