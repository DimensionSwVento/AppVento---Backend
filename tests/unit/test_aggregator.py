"""
RNF-3 · Procesamiento — agregación de hexágonos a la resolución del zoom.
"""
from __future__ import annotations

import h3
import pytest

from domain.query_engine.aggregator import aggregate
from domain.query_engine.source import HexRow
from tests.conftest import hexrow


def _row(h3_index, score, variables):
    lat, lng = h3.cell_to_latlng(h3_index)
    return hexrow(h3_index, score=score, lat=lat, lng=lng, variables=variables)


def test_aggregate_empty():
    assert aggregate([], 6) == []


def test_aggregate_same_resolution_is_passthrough():
    rows = [_row(h3.latlng_to_cell(4.71, -74.07, 8), 0.5, {"TEC_001": 1.0})]
    out = aggregate(rows, 8)
    assert out == rows


def test_aggregate_groups_children_into_parent_and_averages():
    parent = h3.latlng_to_cell(4.71, -74.07, 7)
    children = h3.cell_to_children(parent, 8)

    rows = [
        _row(c, score=float(i), variables={"TEC_001": float(i * 10), "REG_001": "texto"})
        for i, c in enumerate(children)
    ]
    out = aggregate(rows, 7)

    assert len(out) == 1
    hx = out[0]
    assert hx.h3_index == parent
    assert hx.score_general == pytest.approx(sum(range(7)) / 7)
    # TEC_001 promediada, REG_001 (texto) conservada.
    assert hx.variables["TEC_001"] == pytest.approx(sum(i * 10 for i in range(7)) / 7)
    assert hx.variables["REG_001"] == "texto"


def test_aggregate_preserves_lat_lng_of_parent():
    parent = h3.latlng_to_cell(4.71, -74.07, 7)
    children = h3.cell_to_children(parent, 8)
    rows = [_row(c, 0.5, {}) for c in children]
    out = aggregate(rows, 7)
    expected_lat, expected_lng = h3.cell_to_latlng(parent)
    assert out[0].lat == pytest.approx(expected_lat)
    assert out[0].lng == pytest.approx(expected_lng)


def test_aggregate_skips_invalid_cells():
    valid = _row(h3.latlng_to_cell(4.71, -74.07, 8), 0.5, {})
    bad = HexRow(h3_index="invalid", score_general=0.5, tipo_celda="x",
                 lat=0.0, lng=0.0, variables={})
    out = aggregate([valid, bad], 7)
    assert len(out) == 1
