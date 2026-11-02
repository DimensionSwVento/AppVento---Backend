"""
Processing — CRS consistency between the two coordinate systems the backend mixes:
H3 cells and the persisted `lat`/`lng` (WGS84, EPSG:4326) and the slippy-map tiles
the frontend requests (Web Mercator, EPSG:3857).

Catalog test implemented (`processing`): "CRS consistency tests". Expected tile
bounds are computed here with the independent inverse Web Mercator formula, not
with `geo.tile_to_bbox`.
"""
from __future__ import annotations

import math

import h3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from application.services import tile_service
from domain.query_engine import geo
from infrastructure.db import hexagon_repository as repo
from infrastructure.db.hexagon_query_repository import PostgresHexagonSource

pytestmark = [pytest.mark.qa_processing, pytest.mark.postgres]

BOGOTA = h3.latlng_to_cell(4.71, -74.07, 8)
MEDELLIN = h3.latlng_to_cell(6.24, -75.58, 8)


def _mercator_tile(lat: float, lng: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lng + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def _mercator_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2 ** z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return west, south, east, north


@pytest.fixture
def ingested(db, monkeypatch, tmp_path):
    monkeypatch.setattr(tile_service, "HEXAGON_INGEST", "copy")
    p = tmp_path / "m.h3.parquet"
    pq.write_table(pa.table({"h3_index": [BOGOTA, MEDELLIN], "TEC_001": [6.0, 3.0]}), p)
    tile_service.update_from_parquet(db, str(p))
    return db


def test_persisted_coordinates_are_wgs84_lat_lng_of_the_cell(ingested):
    """lat/lng persisted at ingest are the WGS84 centroid of the H3 cell, in
    (lat, lng) order — a swap would put Bogotá at (-74, 4.7), outside Colombia."""
    for cell in (BOGOTA, MEDELLIN):
        stored = repo.get(ingested, cell)
        lat, lng = h3.cell_to_latlng(cell)
        assert stored.lat == pytest.approx(lat, abs=1e-12)
        assert stored.lng == pytest.approx(lng, abs=1e-12)
        assert -4.3 <= stored.lat <= 13.5 and -82.0 <= stored.lng <= -66.5


@pytest.mark.parametrize("z", [6, 8, 10])
def test_tile_bbox_matches_independent_web_mercator_bounds(z):
    lat, lng = h3.cell_to_latlng(BOGOTA)
    x, y = _mercator_tile(lat, lng, z)
    got = geo.tile_to_bbox(z, x, y)
    assert got == pytest.approx(_mercator_bounds(z, x, y), abs=1e-9)


def test_tile_query_returns_exactly_the_hexes_whose_centroid_is_in_the_tile(ingested):
    """A Web Mercator tile served from Postgres contains Bogotá (its centroid is in
    the tile's EPSG:4326 bounds) and not Medellín (it is not)."""
    lat, lng = h3.cell_to_latlng(BOGOTA)
    z = 10
    x, y = _mercator_tile(lat, lng, z)
    west, south, east, north = _mercator_bounds(z, x, y)
    m_lat, m_lng = h3.cell_to_latlng(MEDELLIN)
    assert not (west <= m_lng <= east and south <= m_lat <= north)

    tile = tile_service.get_tile(PostgresHexagonSource(ingested), z, x, y)

    target_res = tile_service.zoom_to_h3_res(z)
    assert [t.h for t in tile] == [h3.cell_to_parent(BOGOTA, target_res)]
