"""
Data integration — the model bundle arrives as several parquet files (raw
variables, criteria scores, aggregated MCDA index) plus the InfoVariables
metadata document; the backend must fuse them per H3 cell and reconcile their
codes.

Catalog tests implemented (`data-integration`), together with the existing
test_hexagon_repository.py / test_ingest.py:
- "multi-source integration": raw variables, criteria and the global index from
  three files are merged per hex (outer join) into one `hexagon` row.
- "semantic consistency": parquet column codes and catalog `master_id`s resolve
  to the same variable (base code), so the map labels a column with the
  catalog's name and dimension.
- "metadata validation": the bundle plan classifies every column against the
  confirmed registry and never guesses an unknown one (it stays `pendiente`).
"""
from __future__ import annotations

import csv
import os

import h3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from application.services import bundle_service, catalog_service, metadata_ingest
from infrastructure.data.variable_catalog import base_code
from infrastructure.db import catalog_seed
from infrastructure.db import hexagon_repository as repo
from infrastructure.db.models import DataSource, HexagonMeta

pytestmark = [pytest.mark.qa_data_integration, pytest.mark.postgres]

BOGOTA = h3.latlng_to_cell(4.71, -74.07, 8)
MEDELLIN = h3.latlng_to_cell(6.24, -75.58, 8)
CALI = h3.latlng_to_cell(3.45, -76.53, 8)


def _write(root, name, columns):
    pq.write_table(pa.table(columns), os.path.join(root, name))


@pytest.fixture
def bundle(db, tmp_path):
    catalog_seed.seed_catalog(db)
    root = tmp_path / "bundle"
    root.mkdir()
    _write(root, "variables.h3.parquet", {
        "h3_index": [BOGOTA, MEDELLIN],
        "TEC_004_sv003": [450.0, 150.0],
        "AMB_005": [4.0, 2.0],
        "excluido": [False, True],
    })
    _write(root, "criterios.h3.parquet", {
        "h3_index": [BOGOTA, MEDELLIN, CALI],  # Cali only has criteria scores
        "recurso_eolico": [4.5, 2.0, 3.0],
        "infraestructura": [3.0, 1.5, 2.5],
    })
    _write(root, "mcda_agregado.h3.parquet", {
        "h3_index": [BOGOTA, MEDELLIN, CALI],
        "global": [4.2, -1.0, 2.6],
    })
    return db, str(root)


def test_bundle_apply_fuses_three_sources_per_hex(bundle):
    db, root = bundle
    out = bundle_service.apply(db, root)
    assert "error" not in out, out
    assert sorted(out["files"]) == ["criterios.h3.parquet", "mcda_agregado.h3.parquet",
                                    "variables.h3.parquet"]

    bogo, med, cali = (repo.get(db, c) for c in (BOGOTA, MEDELLIN, CALI))
    # raw variables (file 1) + criteria (file 2) + global (file 3) on the same row
    assert bogo.variables == {"TEC_004_sv003": 450.0, "AMB_005": 4.0}
    assert bogo.scores == {"recurso_eolico": 4.5, "infraestructura": 3.0}
    assert bogo.score_general == 4.2
    assert bogo.tipo_celda == "disponible"
    # the exclusion flag of file 1 and the -1 index of file 3 agree on Medellín
    assert med.tipo_celda == "excluida"
    assert med.score_general == -1.0
    # a hex present only in the score files is kept (outer join), without raw values
    assert cali.scores == {"recurso_eolico": 3.0, "infraestructura": 2.5}
    assert all(v is None for v in cali.variables.values())

    meta = {m.variable: (m.vmin, m.vmax) for m in db.query(HexagonMeta)}
    assert meta["TEC_004_sv003"] == (150.0, 450.0)
    assert meta["AMB_005"] == (2.0, 4.0)
    ds = db.get(DataSource, "bundle")
    assert all(f in ds.notes for f in out["files"])


def test_bundle_plan_classifies_columns_and_never_guesses_unknown_ones(bundle):
    db, root = bundle
    _write(root, "extra.h3.parquet", {
        "h3_index": [BOGOTA],
        "AMB_005_revisada": [3.5],    # variant of a confirmed code → proposed, not assumed
        "XYZ_999": [1.0],             # unknown → pending reconciliation
    })
    plan = bundle_service.plan(db, root)
    by_name = {c["name"]: c for c in plan["columns"]}

    assert by_name["AMB_005"]["estado"] == "ok" and by_name["AMB_005"]["match"] == "exact"
    assert by_name["AMB_005_revisada"]["estado"] == "propuesto"
    assert by_name["AMB_005_revisada"]["match"] == "base_code"
    assert by_name["XYZ_999"]["estado"] == "pendiente"
    assert by_name["XYZ_999"]["kind"] is None
    assert plan["summary"]["pendientes"] == 1


def test_parquet_codes_and_catalog_master_ids_resolve_to_the_same_variable(bundle, tmp_path):
    db, root = bundle
    doc = tmp_path / "InfoVariables.csv"
    with doc.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Dimensión", "Criterio", "Subcriterio", "ID", "Variable"])
        w.writerow(["Técnica", "Recurso eólico", "Densidad WPD", "TEC-004-A", "Densidad de potencia"])
        w.writerow(["Ambiental", "Contexto biótico", "Áreas protegidas", "AMB-005-A",
                    "Áreas protegidas"])
    assert "error" not in metadata_ingest.apply(db, str(doc))

    labels = catalog_service.tile_labels(db)
    # Each raw parquet column (with its sub-variant suffix) finds its catalog entry.
    assert labels[base_code("TEC_004_sv003")]["label"] == "Densidad de potencia"
    assert labels[base_code("TEC_004_sv003")]["dimension"] == "tecnica"
    assert labels[base_code("AMB_005")]["label"] == "Áreas protegidas"
    assert labels[base_code("AMB_005")]["dimension"] == "ambiental"
