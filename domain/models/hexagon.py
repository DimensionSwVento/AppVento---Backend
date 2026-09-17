"""
Modelos de dominio del hexágono.
`HexagonData` es la fila cruda del parquet; el resto son shapes de salida.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

Value = str | float | None


@dataclass
class HexagonData:
    """Fila cruda leída del `.h3.parquet`."""
    h3_index: str
    score_general: float
    tipo_celda: str
    variables: dict[str, Value] = field(default_factory=dict)


@dataclass
class TileHex:
    """Hex compacto para el tile del mapa (payload liviano: claves cortas).
    `t` = tipo_celda (disponible/excluida). `off` = mar/tierra (1/0) si la master
    trae la columna offshore (nombre configurable, aún por definir)."""
    h: str
    s: float
    t: str
    la: float
    ln: float
    v: Optional[float] = None
    off: Optional[int] = None


@dataclass
class HexDetail:
    """Detalle completo de un hexágono (popup)."""
    h3_index: str
    score_general: float
    tipo_celda: str
    lat: float
    lng: float
    variables: dict[str, Value] = field(default_factory=dict)


@dataclass
class AreaHex:
    """
    Hex devuelto por `POST /tiles/area`: score general + score por dimensión
    (categoría) + valores de variables para el detalle del informe.
    """
    h3_index: str
    lat: float
    lng: float
    score_general: float
    scores: dict[str, float]  # por categoría/dimensión: TEC, AMB, SOE, MAC, REG…
    tipo_celda: str
    variables: dict[str, Value] = field(default_factory=dict)
