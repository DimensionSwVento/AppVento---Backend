"""
RNF-4 · Calidad de datos — lectura del `.h3.parquet`: nulos, score y filtro Colombia.
"""
from __future__ import annotations

import h3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from infrastructure.data.parquet_reader import iter_parquet_rows, read_parquet


def _write_parquet(path, rows):
    table = pa.table({
        "h3_index": [r[0] for r in rows],
        "score_general": [r[1] for r in rows],
        "tipo_celda": [r[2] for r in rows],
        "TEC_001": [r[3] for r in rows],
        "SOE_001": [r[4] for r in rows],
    })
    pq.write_table(table, path)


BOGOTA = h3.latlng_to_cell(4.71, -74.07, 8)     # dentro de Colombia
MEDELLIN = h3.latlng_to_cell(6.24, -75.58, 8)   # dentro de Colombia
MADRID = h3.latlng_to_cell(40.42, -3.70, 8)     # fuera de Colombia


def test_read_parquet_maps_rows_and_handles_nulls(tmp_path):
    p = tmp_path / "sample.h3.parquet"
    _write_parquet(p, [
        (BOGOTA, 0.7, "disponible", 6.0, None),
        (MEDELLIN, None, None, None, 2.0),
    ])

    hexagons, variable_names, parsed_at = read_parquet(str(p))

    assert variable_names == ["TEC_001", "SOE_001"]
    assert len(hexagons) == 2

    bogo, med = hexagons
    assert bogo.h3_index == BOGOTA
    assert bogo.score_general == 0.7
    assert bogo.tipo_celda == "disponible"
    assert bogo.variables["TEC_001"] == 6.0
    assert bogo.variables["SOE_001"] is None  # NaN → None

    # score y tipo_celda ausentes → defaults
    assert med.score_general == 0.0
    assert med.tipo_celda == "disponible"
    assert med.variables["TEC_001"] is None
    assert med.variables["SOE_001"] == 2.0
    assert parsed_at  # timestamp ISO presente


def test_read_parquet_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        read_parquet("/no/existe.parquet")


def test_iter_parquet_rows_filters_out_of_colombia(tmp_path):
    p = tmp_path / "sample.h3.parquet"
    _write_parquet(p, [
        (BOGOTA, 0.7, "disponible", 6.0, None),
        (MADRID, 0.9, "disponible", 6.0, None),
    ])

    rows = list(iter_parquet_rows(str(p)))
    assert [r["h3_index"] for r in rows] == [BOGOTA]


def test_iter_parquet_rows_applies_score_and_tipo_defaults(tmp_path):
    p = tmp_path / "sample.h3.parquet"
    _write_parquet(p, [
        (MEDELLIN, None, None, None, 2.0),
    ])

    row = next(iter_parquet_rows(str(p)))
    assert row["h3_index"] == MEDELLIN
    assert row["tipo_celda"] == "disponible"
    # score ausente → placeholder calculado (efectivo), no NaN.
    assert isinstance(row["score_general"], float)
    assert 0.0 <= row["score_general"] <= 1.0
    lat, lng = h3.cell_to_latlng(MEDELLIN)
    assert row["lat"] == lat
    assert row["lng"] == lng


def test_iter_parquet_rows_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        list(iter_parquet_rows("/no/existe.parquet"))
