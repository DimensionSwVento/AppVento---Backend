"""
RNF-4 · Calidad de datos — ingesta destructiva del `.h3.parquet` → tablas (PostgreSQL real).

Cubre `tile_service.update_from_parquet` por ambas vías (orm y copy) y verifica que
el filtro geográfico (Colombia) se aplica y el score efectivo se calcula/persiste.
"""
from __future__ import annotations

import h3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from application.services import tile_service
from infrastructure.db import hexagon_repository as repo

BOGOTA = h3.latlng_to_cell(4.71, -74.07, 8)
MEDELLIN = h3.latlng_to_cell(6.24, -75.58, 8)
MADRID = h3.latlng_to_cell(40.42, -3.70, 8)  # fuera de Colombia


def _write_parquet(path):
    pq.write_table(pa.table({
        "h3_index": [BOGOTA, MEDELLIN, MADRID],
        "TEC_001": [6.0, 3.0, 6.0],
    }), path)


@pytest.mark.postgres
def test_update_from_parquet_orm_path(db, monkeypatch, tmp_path):
    monkeypatch.setattr(tile_service, "HEXAGON_INGEST", "orm")
    p = tmp_path / "m.h3.parquet"
    _write_parquet(p)

    n = tile_service.update_from_parquet(db, str(p))
    # Madrid queda fuera (filtro Colombia).
    assert n == 2
    assert repo.count(db) == 2
    stored = {h.h3_index for h in repo.list_all(db)}
    assert stored == {BOGOTA, MEDELLIN}


@pytest.mark.postgres
def test_update_from_parquet_copy_path(db, monkeypatch, tmp_path):
    monkeypatch.setattr(tile_service, "HEXAGON_INGEST", "copy")
    p = tmp_path / "m.h3.parquet"
    _write_parquet(p)

    n = tile_service.update_from_parquet(db, str(p))
    assert n == 2
    assert repo.count(db) == 2
    # El score efectivo (placeholder) se calcula y persiste.
    bogo = repo.get(db, BOGOTA)
    assert 0.0 <= bogo.score_general <= 1.0
