from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HexagonData:
    h3_index: str
    score_general: float
    tipo_celda: str
    variables: dict[str, str | float | None] = field(default_factory=dict)


@dataclass
class TileHex:
    h: str           # h3_index
    s: float         # score_general
    t: str           # tipo_celda
    la: float        # lat
    ln: float        # lng
    v: Optional[float] = None  # variable value (AC2)


@dataclass
class HexDetail:
    h3_index: str
    score_general: float
    tipo_celda: str
    lat: float
    lng: float
    variables: dict[str, str | float | None]
