"""
Result quality — the multicriteria area report (the figure the prefeasibility
report is built on) checked against reference data and for topological
consistency of the hexes it returns.

Catalog tests implemented (quality/quality-attributes.json, `result-quality`):
- "validation against reference data": golden file `golden/area_report_reference.json`,
  whose expected values were derived by hand from the WLC/AHP formula documented in
  `infrastructure/data/model_weights.py` (never by calling the code under test).
- "topological consistency": a selection resolves to a set of index-resolution
  cells with no duplicates, no cells outside the selection and no gaps.
"""
from __future__ import annotations

import json
from pathlib import Path

import h3
import pytest

from application.services import tile_service
from infrastructure.data import model_weights
from tests.conftest import FakeHexagonSource, hexrow

pytestmark = pytest.mark.qa_result_quality

_GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "area_report_reference.json").read_text(encoding="utf-8")
)
_INDEX_RES = tile_service.INDEX_RES


def _golden_source() -> tuple[FakeHexagonSource, dict[str, str]]:
    fixture = _GOLDEN["fixture"]
    rows, keys = [], {}
    for hx in fixture["hexes"]:
        cell = h3.latlng_to_cell(hx["lat"], hx["lng"], _INDEX_RES)
        keys[hx["key"]] = cell
        lat, lng = h3.cell_to_latlng(cell)
        variables = {k: v for k, v in hx["variables"].items() if v is not None}
        rows.append(hexrow(cell, score=hx["global"], lat=lat, lng=lng, variables=variables))
    return FakeHexagonSource(rows=rows, ranges=fixture["ranges"]), keys


def test_shipped_weights_match_the_reference_case():
    """The `placeholder-weights` reference case is only valid while model_weights.py
    ships the weights it was derived from. A change of weights must come with a
    hand re-derivation of the reference, not with a silent regeneration."""
    case = next(c for c in _GOLDEN["cases"] if c["id"] == "placeholder-weights")
    assert model_weights.VARIABLE_WEIGHTS == case["variableWeights"]
    assert model_weights.DIMENSION_WEIGHTS == case["dimensionWeights"]


@pytest.mark.parametrize("case", _GOLDEN["cases"], ids=lambda c: c["id"])
def test_area_report_matches_reference_data(case, monkeypatch):
    monkeypatch.setattr(model_weights, "VARIABLE_WEIGHTS", dict(case["variableWeights"]))
    monkeypatch.setattr(model_weights, "DIMENSION_WEIGHTS", dict(case["dimensionWeights"]))
    source, keys = _golden_source()

    report = tile_service.get_area_report(source, hexes=list(keys.values()))
    by_cell = {h.h3_index: h for h in report}

    assert set(by_cell) == set(keys.values())
    for key, expected in case["expected"].items():
        got = by_cell[keys[key]]
        assert got.scores == pytest.approx(expected["scores"], abs=1e-9), key
        assert set(got.scores) == set(expected["scores"]), key
        assert got.score_general == pytest.approx(expected["score_general"], abs=1e-9), key
        assert got.tipo_celda == expected["tipo_celda"], key


def test_area_report_scores_stay_in_unit_interval():
    """Every score the report exposes is in [0, 1] — the contract the frontend
    colour ramps rely on — even with out-of-range raw values in the fixture."""
    source, keys = _golden_source()
    for hx in tile_service.get_area_report(source, hexes=list(keys.values())):
        assert 0.0 <= hx.score_general <= 1.0
        for value in hx.scores.values():
            assert 0.0 <= value <= 1.0


# ── Topological consistency ──────────────────────────────────────────────────

def _all_children_source(parent: str, extra: list[str]) -> FakeHexagonSource:
    cells = list(h3.cell_to_children(parent, _INDEX_RES)) + extra
    rows = []
    for c in cells:
        lat, lng = h3.cell_to_latlng(c)
        rows.append(hexrow(c, score=3.0, lat=lat, lng=lng, variables={"TEC_001": 5.0}))
    return FakeHexagonSource(rows=rows, ranges={"TEC_001": {"min": 0.0, "max": 10.0}})


def test_coarse_selection_resolves_to_exact_children_partition():
    """A res-7 selection must come back as exactly its res-8 children: no gaps
    (all 7 present), no overlaps (no duplicates), no cell outside the parent."""
    parent = h3.latlng_to_cell(4.71, -74.07, _INDEX_RES - 1)
    outside = h3.grid_ring(h3.cell_to_center_child(parent, _INDEX_RES), 3)
    outside = [c for c in outside if h3.cell_to_parent(c, _INDEX_RES - 1) != parent][:2]
    source = _all_children_source(parent, outside)

    report = tile_service.get_area_report(source, hexes=[parent])
    cells = [h.h3_index for h in report]

    assert len(cells) == len(set(cells)), "duplicate hexes in the report"
    assert set(cells) == set(h3.cell_to_children(parent, _INDEX_RES))
    assert all(h3.cell_to_parent(c, _INDEX_RES - 1) == parent for c in cells)
    assert not set(outside) & set(cells)


def test_overlapping_selection_does_not_double_count_hexes():
    """Selecting a parent together with one of its own children (the UI can send
    both) must not report the shared child twice."""
    parent = h3.latlng_to_cell(4.71, -74.07, _INDEX_RES - 1)
    child = h3.cell_to_center_child(parent, _INDEX_RES)
    source = _all_children_source(parent, [])

    cells = [h.h3_index for h in tile_service.get_area_report(source, hexes=[parent, child])]

    assert len(cells) == len(set(cells)) == 7


def test_polygon_selection_returns_only_cells_inside_the_polygon():
    """Cells of a polygon report have their centroid inside the polygon (H3
    containment rule) and form a contiguous patch (no isolated islands)."""
    polygon = [[-74.10, 4.60], [-74.10, 4.70], [-74.00, 4.70], [-74.00, 4.60]]  # lng, lat
    inside = h3.polygon_to_cells(
        h3.LatLngPoly([(lat, lng) for lng, lat in polygon]), _INDEX_RES
    )
    ring = {n for c in inside for n in h3.grid_disk(c, 1)} - set(inside)
    rows = []
    for c in list(inside) + list(ring):
        lat, lng = h3.cell_to_latlng(c)
        rows.append(hexrow(c, score=3.0, lat=lat, lng=lng))
    source = FakeHexagonSource(rows=rows)

    cells = {h.h3_index for h in tile_service.get_area_report(source, polygon=polygon)}

    assert cells == set(inside)
    for c in cells:
        lat, lng = h3.cell_to_latlng(c)
        assert -74.10 <= lng <= -74.00 and 4.60 <= lat <= 4.70
    # Contiguity: every cell touches at least one other cell of the selection.
    for c in cells:
        assert (set(h3.grid_disk(c, 1)) - {c}) & cells
