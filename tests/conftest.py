"""
Fixtures compartidas para la suite de tests del backend Vento.

El backend es stateless y depende del puerto `HexagonSource`
(domain/query_engine/source.py). Los tests de la capa de aplicación usan una
implementación fake en memoria; los tests de API sobreescriben las dependencias
de FastAPI con esa misma fake, de modo que NO se necesita Postgres ni Keycloak.
"""
from __future__ import annotations

import pytest

from domain.query_engine.source import HexRow


class FakeHexagonSource:
    """Implementación en memoria del puerto `HexagonSource` para tests."""

    def __init__(self, rows: list[HexRow] | None = None,
                 ranges: dict[str, dict[str, float]] | None = None) -> None:
        self._rows = {r.h3_index: r for r in (rows or [])}
        self._ranges = ranges or {}

    # ── datos ─────────────────────────────────────────────────────────────
    def fetch_cells(self, cells: list[str]) -> list[HexRow]:
        return [self._rows[c] for c in cells if c in self._rows]

    def fetch_one(self, h3_index: str) -> HexRow | None:
        return self._rows.get(h3_index)

    def fetch_bbox(self, west: float, south: float, east: float,
                   north: float) -> list[HexRow]:
        return [
            r for r in self._rows.values()
            if south <= r.lat <= north and west <= r.lng <= east
        ]

    # ── metadatos ─────────────────────────────────────────────────────────
    def variable_names(self) -> list[str]:
        return sorted(self._ranges.keys())

    def variable_range(self, var_name: str) -> dict[str, float] | None:
        return self._ranges.get(var_name)

    def variable_ranges(self) -> dict[str, dict[str, float]]:
        return dict(self._ranges)

    # ── estado ────────────────────────────────────────────────────────────
    def count(self) -> int:
        return len(self._rows)

    def counts(self, selected: list[str], var_name: str | None = None,
               threshold: float = 0.33) -> dict:
        total = self.count()
        sel = len([c for c in selected if c in self._rows])
        return {"total": total, "selected": sel, "aboveThreshold": 0}

    def is_ready(self) -> bool:
        return len(self._rows) > 0


def hexrow(h3_index: str, score: float = 0.5, lat: float = 0.0, lng: float = 0.0,
           variables: dict | None = None,
           tipo: str = "disponible") -> HexRow:
    """Helper para construir filas de hexágono en tests."""
    return HexRow(
        h3_index=h3_index,
        score_general=score,
        tipo_celda=tipo,
        lat=lat,
        lng=lng,
        variables=variables or {},
    )


@pytest.fixture
def fake_source():
    return FakeHexagonSource
