"""Caché en proceso de capas de variable (Fase 1 de performance)."""
from __future__ import annotations

import h3

from application.services import tile_service
from tests.conftest import FakeHexagonSource, hexrow


class PostgresHexagonSource(FakeHexagonSource):
    """Fake con el MISMO nombre que la fuente real: activa la caché de capas."""

    calls = 0

    def stream_bbox(self, west, south, east, north):  # type: ignore[override]
        PostgresHexagonSource.calls += 1
        yield from super().stream_bbox(west, south, east, north)


def _source() -> PostgresHexagonSource:
    bogo = h3.latlng_to_cell(4.71, -74.07, 8)
    med = h3.latlng_to_cell(6.24, -75.58, 8)
    lat1, lng1 = h3.cell_to_latlng(bogo)
    lat2, lng2 = h3.cell_to_latlng(med)
    rows = [
        hexrow(bogo, score=0.8, lat=lat1, lng=lng1, variables={"TEC_001": 6.0}),
        hexrow(med, score=0.2, lat=lat2, lng=lng2, variables={"TEC_001": 3.0}),
    ]
    ranges = {"TEC_001": {"min": 0.0, "max": 6.0}}
    PostgresHexagonSource.calls = 0
    return PostgresHexagonSource(rows=rows, ranges=ranges)


BBOX = {"west": -76.0, "south": 4.0, "east": -73.0, "north": 7.0}


def test_bulk_cache_hit_no_second_scan():
    tile_service.invalidate_bulk_cache()
    src = _source()
    first = tile_service.get_variable_layers(src, ["TEC_001"], bbox=BBOX, res=6)
    second = tile_service.get_variable_layers(src, ["TEC_001"], bbox=BBOX, res=6)
    assert src.calls == 1
    assert first == second
    assert first["TEC_001"]


def test_bulk_cache_invalidate_forces_new_scan():
    tile_service.invalidate_bulk_cache()
    src = _source()
    tile_service.get_variable_layers(src, ["TEC_001"], bbox=BBOX, res=6)
    tile_service.invalidate_bulk_cache()
    tile_service.get_variable_layers(src, ["TEC_001"], bbox=BBOX, res=6)
    assert src.calls == 2


def test_bulk_cache_distinguishes_bbox():
    tile_service.invalidate_bulk_cache()
    src = _source()
    tile_service.get_variable_layers(src, ["TEC_001"], bbox=BBOX, res=6)
    other = dict(BBOX, west=-75.0)
    tile_service.get_variable_layers(src, ["TEC_001"], bbox=other, res=6)
    assert src.calls == 2
