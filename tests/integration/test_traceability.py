"""
Traceability and auditability — every master dataset that reaches the map leaves
a version record, every ingested source leaves a lineage record, and every saved
report can be traced back to the exact dataset version it was computed on.

Catalog tests implemented (`traceability-auditability`):
- "data versioning tests": `master_version` rows written by the bundle ingest
  (version, status, source, hex_count, var_count, applied_at); the latest ready
  version is the current one; version labels identify a single version.
- "spatial data lineage tests": `data_source` rows (location, files, rows,
  status, ingested_at) for the master bundle and the catalog documents, both on
  success and on failure.
- "historical reproducibility of results": a saved report keeps the master
  version and the score snapshot it was generated with after a re-ingest, and
  that version still resolves to its lineage record.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import h3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import func, select

from application.services import bundle_service, catalog_ingest, historial_service, master_service
from infrastructure.db import catalog_seed
from infrastructure.db import master_repository
from infrastructure.db import repository
from infrastructure.db.models import (
    DataSource, Escenario, Hexagon, HexagonMeta, MasterVersion,
)

pytestmark = [pytest.mark.qa_traceability_auditability, pytest.mark.postgres]

BOGOTA = h3.latlng_to_cell(4.71, -74.07, 8)
MEDELLIN = h3.latlng_to_cell(6.24, -75.58, 8)
MADRID = h3.latlng_to_cell(40.42, -3.70, 8)  # dropped by the Colombia filter


def _write_bundle(root, global_scores=(4.2, 1.8, 3.0)) -> None:
    os.makedirs(root, exist_ok=True)
    pq.write_table(pa.table({
        "h3_index": [BOGOTA, MEDELLIN, MADRID],
        "global": list(global_scores),
        "TEC_004_sv003": [450.0, 150.0, 300.0],
        "AMB_005": [4.0, 2.0, 3.0],
    }), os.path.join(root, "variables.h3.parquet"))


def _chunked_ingest(db, root) -> dict:
    """The admin UI flow: plan the chunks, upload them one by one, then finalize."""
    if not os.path.exists(os.path.join(root, "variables.h3.parquet")):
        _write_bundle(root)
    plan = bundle_service.chunk_plan(db, str(root))
    assert "error" not in plan, plan
    for chunk in plan["chunks"]:
        out = bundle_service.apply_chunk(db, str(root), chunk["file"], chunk["row_group"])
        assert "error" not in out, out
    return bundle_service.finalize(db, str(root))


@pytest.fixture
def registry(db):
    catalog_seed.seed_catalog(db)  # column→class registry the ingest classifies against
    return db


@pytest.fixture
def clock(monkeypatch):
    """Deterministic UTC clock for master_service (labels and applied_at)."""
    state = {"now": datetime(2026, 9, 12, 18, 0, 10, tzinfo=timezone.utc)}

    def advance(**delta):
        state["now"] = state["now"] + timedelta(**delta)

    monkeypatch.setattr(master_service, "_now", lambda: state["now"])
    return advance


# ── Data versioning ──────────────────────────────────────────────────────────

def test_finalize_records_a_ready_master_version_with_counts_and_source(registry, clock, tmp_path):
    db = registry
    out = _chunked_ingest(db, tmp_path / "bundle")

    versions = list(db.scalars(select(MasterVersion)))
    assert len(versions) == 1
    mv = versions[0]
    assert mv.status == "ready"
    assert mv.version == out["version"] == "2026-09-12 18:00:10 UTC"
    assert mv.source == "variables.h3.parquet"
    assert mv.applied_at is not None
    assert mv.hex_count == db.scalar(select(func.count()).select_from(Hexagon)) == 2
    assert mv.var_count == db.scalar(select(func.count()).select_from(HexagonMeta)) > 0
    assert master_service.status(db)["current"]["id"] == mv.id


def test_each_ingest_creates_a_new_version_and_the_latest_is_current(registry, clock, tmp_path):
    db = registry
    _chunked_ingest(db, tmp_path / "v1")
    first = master_repository.current(db)
    clock(hours=1)
    _write_bundle(tmp_path / "v2", global_scores=(5.0, 1.0, 3.0))
    _chunked_ingest(db, tmp_path / "v2")
    second = master_repository.current(db)

    assert db.scalar(select(func.count()).select_from(MasterVersion)) == 2
    assert second.id != first.id
    assert second.applied_at > first.applied_at
    assert master_service.current_version_label(db) == second.version
    # The previous version is kept for audit, not overwritten.
    assert master_repository.get(db, first.id).status == "ready"


def test_master_version_labels_identify_a_single_version(registry, clock, tmp_path):
    """Reports store the version LABEL (`informe.master_version`), so a label must
    identify one dataset version. Two ingests finalized 20 s apart (e.g. a retried
    finalize after a chunked upload) must not share a label."""
    db = registry
    _chunked_ingest(db, tmp_path / "v1")
    clock(seconds=20)
    _write_bundle(tmp_path / "v2", global_scores=(5.0, 1.0, 3.0))
    _chunked_ingest(db, tmp_path / "v2")

    labels = [mv.version for mv in db.scalars(select(MasterVersion))]
    assert len(labels) == 2
    assert len(set(labels)) == 2, f"two dataset versions share the label {labels[0]!r}"


# ── Spatial data lineage ─────────────────────────────────────────────────────

def test_finalize_records_bundle_lineage_in_data_source(registry, clock, tmp_path):
    db = registry
    root = tmp_path / "bundle"
    _chunked_ingest(db, root)

    ds = db.get(DataSource, "bundle")
    assert ds is not None
    assert ds.status == "ok"
    assert ds.kind == "parquet"
    assert ds.location == str(root)
    assert "variables.h3.parquet" in ds.notes
    assert ds.rows == 2
    assert ds.ingested_at is not None


def test_catalog_document_ingest_records_success_and_failure_lineage(db, monkeypatch, tmp_path):
    ok = catalog_ingest.ingest(db, "escenarios")
    assert ok["status"] == "ok"
    assert ok["location"] == str(catalog_seed._ESCENARIOS_CSV)
    first_stamp = db.get(DataSource, "escenarios").ingested_at
    assert first_stamp is not None

    broken = tmp_path / "escenarios_seed.csv"
    broken.write_text("id,label\nbase,Base\n", encoding="utf-8")  # missing columns
    monkeypatch.setattr(catalog_seed, "_ESCENARIOS_CSV", broken)
    with pytest.raises(KeyError):
        catalog_ingest.ingest(db, "escenarios")

    ds = db.get(DataSource, "escenarios")
    db.refresh(ds)
    assert ds.status == "error"
    assert ds.notes  # the failure reason is kept for audit
    assert ds.ingested_at >= first_stamp


def test_catalog_document_lineage_records_the_ingested_row_count(db):
    """`data_source.rows` must reflect what the ingest left in the table, so the
    lineage record can be checked against the data it describes."""
    catalog_ingest.ingest(db, "escenarios")
    in_table = db.scalar(select(func.count()).select_from(Escenario))
    assert in_table > 0
    assert db.get(DataSource, "escenarios").rows == in_table


# ── Historical reproducibility ───────────────────────────────────────────────

def test_saved_report_is_stamped_with_the_current_master_version(registry, clock, tmp_path):
    db = registry
    repository.ensure_user(db, sub="analyst", email=None, roles=[])
    before = historial_service.save_for_user(db, "analyst", {"nombre": "sin master"})
    assert before["masterVersion"] is None

    _chunked_ingest(db, tmp_path / "bundle")
    after = historial_service.save_for_user(db, "analyst", {"nombre": "con master"})
    assert after["masterVersion"] == master_service.current_version_label(db)


def test_report_keeps_its_version_and_snapshot_after_a_reingest(registry, clock, tmp_path):
    db = registry
    repository.ensure_user(db, sub="analyst", email=None, roles=[])
    _chunked_ingest(db, tmp_path / "v1")
    v1 = master_repository.current(db)
    snapshot = {"general": 0.8, "TEC": 0.625}
    saved = historial_service.save_for_user(
        db, "analyst", {"nombre": "Bogotá", "hexes": [BOGOTA], "scores": snapshot})

    clock(days=1)
    _write_bundle(tmp_path / "v2", global_scores=(2.0, 5.0, 3.0))
    _chunked_ingest(db, tmp_path / "v2")
    assert master_service.current_version_label(db) != v1.version

    [reopened] = [r for r in historial_service.list_for_user(db, "analyst") if r["id"] == saved["id"]]
    assert reopened["masterVersion"] == v1.version
    assert reopened["scores"] == snapshot
    assert reopened["hexes"] == [BOGOTA]
    # The stamped label still resolves to exactly one version with its lineage.
    matches = list(db.scalars(select(MasterVersion).where(MasterVersion.version == v1.version)))
    assert [m.id for m in matches] == [v1.id]
    assert matches[0].source == "variables.h3.parquet"
    assert matches[0].hex_count == 2
