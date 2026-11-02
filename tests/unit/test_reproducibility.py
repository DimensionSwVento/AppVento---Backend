"""
Processing — reproducibility of the geoprocessing results.

Catalog test implemented (`processing`): "reproducibility tests". The same input
must yield byte-identical output, run after run and across fresh source
instances, and must not depend on the order in which the hexes are requested or
stored (set semantics of an H3 selection).
"""
from __future__ import annotations

import json
from dataclasses import asdict

import h3
import pytest

from application.services import tile_service
from tests.conftest import FakeHexagonSource, hexrow

pytestmark = pytest.mark.qa_processing

_RES = tile_service.INDEX_RES
_PARENT = h3.latlng_to_cell(4.71, -74.07, _RES - 1)
_CELLS = sorted(h3.cell_to_children(_PARENT, _RES))


def _source(order: list[str]) -> FakeHexagonSource:
    rows = []
    for i, c in enumerate(order):
        lat, lng = h3.cell_to_latlng(c)
        k = _CELLS.index(c)  # values depend on the cell, not on its position in `order`
        rows.append(hexrow(c, score=1.0 + k * 0.5, lat=lat, lng=lng,
                           variables={"TEC_001": 3.0 + k, "AMB_005": 1.0 + (k % 5),
                                      "SOE_001": float(k * 10)}))
    ranges = {"TEC_001": {"min": 3.0, "max": 12.0}, "AMB_005": {"min": 1.0, "max": 5.0},
              "SOE_001": {"min": 0.0, "max": 100.0}}
    return FakeHexagonSource(rows=rows, ranges=ranges)


def _canonical(report) -> str:
    return json.dumps(sorted((asdict(h) for h in report), key=lambda d: d["h3_index"]),
                      sort_keys=True)


def test_area_report_is_identical_across_runs():
    first = _canonical(tile_service.get_area_report(_source(_CELLS), hexes=[_PARENT]))
    second = _canonical(tile_service.get_area_report(_source(_CELLS), hexes=[_PARENT]))
    assert first == second


def test_area_report_does_not_depend_on_input_or_storage_order():
    reference = _canonical(tile_service.get_area_report(_source(_CELLS), hexes=_CELLS))
    reversed_store = _source(list(reversed(_CELLS)))
    shuffled_request = _CELLS[3:] + _CELLS[:3]
    assert _canonical(tile_service.get_area_report(reversed_store, hexes=shuffled_request)) \
        == reference


def test_tile_aggregation_is_identical_across_runs():
    lat, lng = h3.cell_to_latlng(_PARENT)
    z = 10
    x, y = _tile_of(lat, lng, z)
    a = tile_service.get_tile(_source(_CELLS), z, x, y, var="TEC_001")
    b = tile_service.get_tile(_source(list(reversed(_CELLS))), z, x, y, var="TEC_001")
    assert a, "fixture must fall inside the tile"
    assert sorted(map(asdict, a), key=lambda d: d["h"]) == sorted(map(asdict, b),
                                                                 key=lambda d: d["h"])


def _tile_of(lat: float, lng: float, z: int) -> tuple[int, int]:
    import math
    n = 2 ** z
    x = int((lng + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y
