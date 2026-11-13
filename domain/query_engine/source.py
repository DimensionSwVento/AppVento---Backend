"""
Abstracción de la fuente de hexágonos (Dependency Inversion).

El servicio y el motor dependen de este Protocol, no de una implementación
concreta. Hoy la implementación es Postgres por request (`PostgresHexagonSource`);
mañana podría ser otra sin tocar el dominio.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from collections.abc import Iterable
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

    def stream_cells(self, cells: list[str]) -> Iterable[HexRow]:
        """Como `fetch_cells` pero en STREAMING (no materializa el área en RAM) —
        para el informe sobre áreas grandes sin OOM."""
        ...

    def fetch_one(self, h3_index: str) -> Optional[HexRow]:
        """Un hexágono por su índice (detalle)."""
        ...

    def fetch_bbox(
        self, west: float, south: float, east: float, north: float,
        include_variables: bool = True,
    ) -> list[HexRow]:
        """Hexágonos dentro del bounding box (tiles). `include_variables=False`
        (Fase C2) omite el JSONB `variables` cuando el llamador no lo necesita
        (p.ej. la cuadrícula base sin variable ni mar/tierra activos) — fila
        más liviana para la consulta de mayor frecuencia (cada pan/zoom)."""
        ...

    def stream_bbox(
        self, west: float, south: float, east: float, north: float,
        include_variables: bool = True,
    ) -> Iterable[HexRow]:
        """Como `fetch_bbox` pero en STREAMING (no materializa el bbox en RAM) —
        para agregar la capa de una variable sobre zonas grandes sin OOM."""
        ...

    def fetch_resolution_mv(
        self, res: int, west: float, south: float, east: float, north: float,
    ) -> Optional[list[HexRow]]:
        """Fase C5: atajo por vista materializada (`mv_hexagon_res5`/`res6`,
        Fase C3) para resoluciones agregadas que YA están precomputadas. Evita
        leer y promediar en caliente miles/millones de filas `hexagon` res8 por
        request (la causa original de la caída de `/tiles/variables-bulk`).

        Devuelve `None` si `res` NO tiene vista precomputada (el llamador debe
        usar `stream_bbox` + agregación en Python, el camino de siempre). Una
        lista (posiblemente vacía) si SÍ hay vista para esa `res` — cada
        `HexRow.variables` ya viene como el PROMEDIO numérico por variable
        sobre los hijos de esa celda padre (no el detalle exacto)."""
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

    def variable_quantiles(self, var_name: str, bands: int = 5) -> Optional[dict]:
        """Cortes de cuantil (bands-1) + min/max de una variable sobre toda la tabla,
        para la ESCALA DE COLOR choropleth (bandas por cuantiles). None si no hay
        valores numéricos. Ver `variables-en-mapa` (escala de color por cuartiles)."""
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
