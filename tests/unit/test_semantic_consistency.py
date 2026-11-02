"""
Data integration — semantic consistency of the column→class registry shipped with
the backend (`infrastructure/data/parquet_column_seed.csv`), which every bundle
ingest classifies against.

Catalog test implemented (`data-integration`): "semantic consistency".
"""
from __future__ import annotations

import csv

import pytest

from application.services.bundle_service import DESTINO
from infrastructure.data.variable_catalog import DIMENSIONS, base_code, dimension_of
from infrastructure.db import catalog_seed

pytestmark = pytest.mark.qa_data_integration

_ROWS = list(csv.DictReader(catalog_seed._PARQUET_COLUMN_CSV.open(encoding="utf-8")))
_VARIABLE_KINDS = {"variable_modelo", "variable_informativa", "alerta"}


def test_registry_has_unique_column_names_and_one_key():
    names = [r["name"] for r in _ROWS]
    assert len(names) == len(set(names))
    assert [r["name"] for r in _ROWS if r["kind"] == "clave"] == ["h3_index"]
    assert [r["name"] for r in _ROWS if r["kind"] == "global"] == ["global"]


def test_every_registry_row_is_semantically_consistent():
    offenders = []
    for row in _ROWS:
        problems = []
        if row["kind"] not in DESTINO:
            problems.append(f"unknown class {row['kind']!r}")
        if row["base_code"] != base_code(row["name"]):
            problems.append(f"base_code {row['base_code']!r} != {base_code(row['name'])!r}")
        if row["estado"] not in ("ok", "pendiente", "ignorada"):
            problems.append(f"estado {row['estado']!r}")
        if row["tipo_dato"] not in ("double", "int", "string", "bool"):
            problems.append(f"tipo_dato {row['tipo_dato']!r}")
        # every variable column belongs to a known model dimension
        if row["kind"] in _VARIABLE_KINDS and dimension_of(row["base_code"]) not in DIMENSIONS:
            problems.append("variable without a known dimension")
        if problems:
            offenders.append(f"{row['name']}: {', '.join(problems)}")
    assert len(_ROWS) > 0
    assert not offenders, "\n".join(offenders)
