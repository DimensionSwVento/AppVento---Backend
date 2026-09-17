"""
RNF-3 · Procesamiento — geometría pura H3: tile↔bbox y polígono→celdas.
"""
from __future__ import annotations

import h3

from domain.query_engine import geo


def test_tile_to_bbox_world_level_zero():
    west, south, east, north = geo.tile_to_bbox(0, 0, 0)
    assert west == -180.0
    assert east == 180.0
    assert abs(south - (-85.0511287798066)) < 1e-6
    assert abs(north - 85.0511287798066) < 1e-6


def test_tile_to_bbox_east_north_hemisphere():
    west, south, east, north = geo.tile_to_bbox(1, 1, 0)
    assert west == 0.0
    assert east == 180.0
    assert south == 0.0
    assert north > 0.0


def test_tile_to_bbox_is_well_ordered():
    west, south, east, north = geo.tile_to_bbox(6, 18, 31)
    assert west < east
    assert south < north


def test_tile_to_bbox_contains_bogota_at_zoom_6():
    # Bogotá (4.71, -74.07) cae en el tile z=6, x=18, y=31 (web mercator).
    west, south, east, north = geo.tile_to_bbox(6, 18, 31)
    assert west <= -74.07 <= east
    assert south <= 4.71 <= north


def test_polygon_to_cells_returns_valid_h3_cells():
    # Polígono pequeño alrededor de Bogotá (lng, lat).
    polygon = [
        [-74.10, 4.60], [-74.10, 4.80],
        [-74.00, 4.80], [-74.00, 4.60],
    ]
    cells = geo.polygon_to_cells(polygon, resolution=8)
    assert len(cells) > 0
    for c in cells:
        assert h3.is_valid_cell(c)
        assert h3.get_resolution(c) == 8


def test_polygon_to_cells_defaults_to_index_res():
    polygon = [[-74.10, 4.60], [-74.10, 4.80], [-74.00, 4.80]]
    cells = geo.polygon_to_cells(polygon)
    for c in cells:
        assert h3.get_resolution(c) == geo.INDEX_RES


def test_expand_to_index_res_passthrough_at_index_res():
    bogo = h3.latlng_to_cell(4.71, -74.07, 8)
    out = geo.expand_to_index_res([bogo], index_res=8)
    assert out == {bogo}


def test_expand_to_index_res_expands_coarser_cells():
    # Una celda a res 7 se expande a sus 7 hijas a res 8.
    parent = h3.latlng_to_cell(4.71, -74.07, 7)
    out = geo.expand_to_index_res([parent], index_res=8)
    assert len(out) == 7
    for c in out:
        assert h3.get_resolution(c) == 8


def test_expand_to_index_res_drops_finer_and_invalid():
    parent = h3.latlng_to_cell(4.71, -74.07, 7)
    fine = h3.latlng_to_cell(4.71, -74.07, 9)  # más fino que el índice
    out = geo.expand_to_index_res([parent, fine, "not-an-h3"], index_res=8)
    assert "not-an-h3" not in out
    assert fine not in out
    assert len(out) == 7
