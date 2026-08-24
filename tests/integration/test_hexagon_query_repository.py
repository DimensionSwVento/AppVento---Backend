"""
RNF-1 · Disponibilidad / RNF-3 · Procesamiento — fuente Postgres (por request).
"""
from __future__ import annotations

import h3
import pytest

from infrastructure.db import hexagon_repository as repo
from infrastructure.db.hexagon_query_repository import PostgresHexagonSource
from infrastructure.db.models import HexagonMeta

BOGOTA = h3.latlng_to_cell(4.71, -74.07, 8)
MEDELLIN = h3.latlng_to_cell(6.24, -75.58, 8)


@pytest.fixture()
def source(db):
    lat1, lng1 = h3.cell_to_latlng(BOGOTA)
    lat2, lng2 = h3.cell_to_latlng(MEDELLIN)
    repo.upsert(db, {"h3_index": BOGOTA, "score_general": 0.8, "tipo_celda": "disponible",
                     "lat": lat1, "lng": lng1,
                     "variables": {"TEC_001": 6.0, "SOE_001": 10.0}})
    repo.upsert(db, {"h3_index": MEDELLIN, "score_general": 0.2, "tipo_celda": "disponible",
                     "lat": lat2, "lng": lng2,
                     "variables": {"TEC_001": 3.0, "SOE_001": 0.0}})
    db.add_all([
        HexagonMeta(variable="TEC_001", vmin=0.0, vmax=6.0),
        HexagonMeta(variable="SOE_001", vmin=0.0, vmax=10.0),
    ])
    db.commit()
    return PostgresHexagonSource(db)


@pytest.mark.postgres
def test_fetch_cells_and_one(source):
    rows = source.fetch_cells([BOGOTA, "missing"])
    assert [r.h3_index for r in rows] == [BOGOTA]
    assert source.fetch_one(BOGOTA).score_general == 0.8
    assert source.fetch_one("missing") is None


@pytest.mark.postgres
def test_fetch_bbox(source):
    lat, lng = h3.cell_to_latlng(BOGOTA)
    rows = source.fetch_bbox(lng - 0.01, lat - 0.01, lng + 0.01, lat + 0.01)
    assert [r.h3_index for r in rows] == [BOGOTA]


@pytest.mark.postgres
def test_variable_metadata(source):
    assert source.variable_names() == ["SOE_001", "TEC_001"]
    assert source.variable_range("TEC_001") == {"min": 0.0, "max": 6.0}
    assert source.variable_range("missing") is None
    assert source.variable_ranges()["SOE_001"] == {"min": 0.0, "max": 10.0}


@pytest.mark.postgres
def test_count_and_counts_by_score(source):
    assert source.count() == 2
    counts = source.counts([BOGOTA])
    assert counts["total"] == 2
    assert counts["selected"] == 1
    assert counts["aboveThreshold"] == 1  # solo BOGOTA (0.8) > 0.33


@pytest.mark.postgres
def test_counts_by_variable_threshold(source):
    counts = source.counts([], var_name="TEC_001", threshold=0.33)
    # corte = 0 + 0.33*6 = 1.98 → TEC_001 > 1.98 cuenta 2 (6.0 y 3.0)
    assert counts["aboveThreshold"] == 2


@pytest.mark.postgres
def test_is_ready(source):
    assert source.is_ready() is True
    assert PostgresHexagonSource(db=source.db)  # sanity: se construye
