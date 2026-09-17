"""
RNF-3 · Procesamiento / RNF-1 · Disponibilidad — servicio de aplicación (stateless)
sobre el puerto `HexagonSource` (fake en memoria, sin Postgres).
"""
from __future__ import annotations

import h3

from application.services import tile_service
from domain.models.hexagon import TileHex
from tests.conftest import FakeHexagonSource, hexrow


def _source():
    bogo = h3.latlng_to_cell(4.71, -74.07, 8)
    med = h3.latlng_to_cell(6.24, -75.58, 8)
    lat1, lng1 = h3.cell_to_latlng(bogo)
    lat2, lng2 = h3.cell_to_latlng(med)
    rows = [
        hexrow(bogo, score=0.8, lat=lat1, lng=lng1,
               variables={"TEC_001": 6.0, "SOE_001": 10.0}),
        hexrow(med, score=0.2, lat=lat2, lng=lng2,
               variables={"TEC_001": 3.0, "SOE_001": 0.0}),
    ]
    ranges = {"TEC_001": {"min": 0.0, "max": 6.0},
              "SOE_001": {"min": 0.0, "max": 10.0}}
    return FakeHexagonSource(rows=rows, ranges=ranges)


def test_extract_categories():
    names = ["TEC_001", "TEC_004", "SOE_001", "AMB_002"]
    assert tile_service.extract_categories(names) == ["AMB", "SOE", "TEC"]


def test_warmup_ready():
    src = _source()
    out = tile_service.warmup(src)
    assert out["status"] == "ready"
    assert out["count"] == 2
    assert set(out["variableNames"]) == {"SOE_001", "TEC_001"}
    assert set(out["categories"]) == {"SOE", "TEC"}


def test_warmup_empty():
    out = tile_service.warmup(FakeHexagonSource())
    assert out["status"] == "empty"
    assert out["count"] == 0


def test_get_variables_shape():
    src = _source()
    out = tile_service.get_variables(src)
    names = {v["name"] for v in out["variables"]}
    assert names == {"SOE_001", "TEC_001"}
    tec = next(v for v in out["variables"] if v["name"] == "TEC_001")
    assert tec["dimension"] == "TEC"
    assert tec["range"] == {"min": 0.0, "max": 6.0}


def test_get_detail_found_and_missing():
    src = _source()
    bogo = h3.latlng_to_cell(4.71, -74.07, 8)
    detail = tile_service.get_detail(src, bogo)
    assert detail is not None
    assert detail.h3_index == bogo
    assert detail.score_general == 0.8
    assert tile_service.get_detail(src, "missing") is None


def test_get_tile_shape_and_aggregation():
    src = _source()
    # Tile z=6 (18, 31) contiene a Bogotá → agrega a res 4.
    out = tile_service.get_tile(src, 6, 18, 31)
    assert isinstance(out, list)
    for hx in out:
        assert isinstance(hx, TileHex)
        assert h3.get_resolution(hx.h) == 4


def test_get_tile_with_var_resolves_value():
    src = _source()
    out = tile_service.get_tile(src, 6, 18, 31, var="TEC_001")
    assert len(out) == 1
    assert out[0].v == 6.0


def test_get_area_report_scores_per_dimension():
    src = _source()
    bogo = h3.latlng_to_cell(4.71, -74.07, 8)
    med = h3.latlng_to_cell(6.24, -75.58, 8)
    out = tile_service.get_area_report(src, hexes=[bogo, med])
    by_index = {h.h3_index: h for h in out}
    assert len(out) == 2
    # Scores por dimensión = promedio de las variables de esa categoría POR HEX.
    assert by_index[bogo].scores["TEC"] == 1.0   # 6.0 → 1.0
    assert by_index[bogo].scores["SOE"] == 1.0   # 10.0 → 1.0
    assert by_index[bogo].score_general == 0.8
    assert by_index[med].scores["TEC"] == 0.5    # 3.0 → 0.5
    assert by_index[med].scores["SOE"] == 0.0    # 0.0 → 0.0


def test_get_area_report_empty_input():
    out = tile_service.get_area_report(_source(), hexes=[])
    assert out == []
    out = tile_service.get_area_report(_source(), polygon=None, hexes=None)
    assert out == []


def test_resolve_cells_prefers_hexes_over_polygon():
    bogo7 = h3.latlng_to_cell(4.71, -74.07, 7)
    children = h3.cell_to_children(bogo7, 8)
    # Con hexes presentes, se ignora el polígono y se expande el hex a res 8.
    cells = tile_service._resolve_cells(
        polygon=[[-74.1, 4.6], [-74.1, 4.8], [-74.0, 4.8]], hexes=[bogo7], resolution=8)
    assert set(cells) == set(children)


def test_resolve_cells_with_polygon():
    polygon = [[-74.1, 4.6], [-74.1, 4.8], [-74.0, 4.8], [-74.0, 4.6]]
    cells = tile_service._resolve_cells(polygon=polygon, hexes=None, resolution=8)
    assert cells  # el polígono se convierte a celdas H3


def test_get_counts():
    src = _source()
    out = tile_service.get_counts(src, zoom=6, selected=[])
    assert out["total"] == 2


def test_get_area_heatmap_with_hexes():
    src = _source()
    bogo = h3.latlng_to_cell(4.71, -74.07, 8)
    out = tile_service.get_area_heatmap(src, polygon=None, var="TEC_001", hexes=[bogo])
    assert out == [{"h": bogo, "value": 1.0}]  # 6.0 → norm 1.0


def test_get_area_heatmap_empty():
    src = _source()
    assert tile_service.get_area_heatmap(src, None, "TEC_001", hexes=[]) == []
