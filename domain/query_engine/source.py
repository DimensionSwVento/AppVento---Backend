"""
Abstracción de la fuente de hexágonos (Dependency Inversion).

El servicio y el motor dependen de este Protocol, no de una implementación
concreta. Hoy la implementación es Postgres por request (`PostgresHexagonSource`);
mañana podría ser otra sin tocar el dominio.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

Value = str | float | None


@dataclass
class HexRow:
    """Fila de hexágono devuelta por la fuente (equivale al antiguo _IndexedHex)."""
    h3_index: str
    score_general: float
    tipo_celda: str
    lat: float
    lng: float
    variables: dict[str, Value] = field(default_factory=dict)


@runtime_checkable
class HexagonSource(Protocol):
    """Puerto de acceso a datos de hexágonos. Implementación en infraestructura."""

    def fetch_cells(self, cells: list[str]) -> list[HexRow]:
        """Hexágonos cuyo h3_index esté en `cells` (área/selección)."""
        ...

    def fetch_one(self, h3_index: str) -> Optional[HexRow]:
        """Un hexágono por su índice (detalle)."""
        ...

    def fetch_bbox(self, west: float, south: float, east: float, north: float) -> list[HexRow]:
        """Hexágonos dentro del bounding box (tiles)."""
        ...

    def fetch_variable_cells(self, var_name: str) -> list[tuple[str, float]]:
        """(h3_index, valor crudo) de TODOS los hex que tienen `var_name` numérica.

        Capa de una variable en el mapa (cobertura RAMSAR, viento, etc.): escanea
        el índice completo por esa variable. La normalización 0–1 la hace el
        servicio con el rango de `hexagon_meta`.
        """
        ...

    def variable_names(self) -> list[str]:
        """Nombres de variables disponibles (desde `hexagon_meta`)."""
        ...

    def variable_range(self, var_name: str) -> Optional[dict[str, float]]:
        """Rango {min,max} de una variable para normalizar (desde `hexagon_meta`)."""
        ...

    def variable_ranges(self) -> dict[str, dict[str, float]]:
        """Todos los rangos (para `variables` y `counts`)."""
        ...

    def count(self) -> int:
        """Total de hexágonos (para health / counts)."""
        ...

    def counts(self, selected: list[str], var_name: Optional[str] = None,
               threshold: float = 0.33) -> dict:
        """
        {total, selected, aboveThreshold} vía agregados SQL (sin traer filas).
        aboveThreshold = score>threshold (o variable normalizada>threshold).
        """
        ...

    def is_ready(self) -> bool:
        """La fuente está disponible (BD accesible y con datos)."""
        ...
