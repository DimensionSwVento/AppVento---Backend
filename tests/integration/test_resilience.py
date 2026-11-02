"""
Resilience — behaviour of the backend under injected faults and its recovery once
the fault is removed.

Faults injected: the database becomes unreachable; the master parquet is
missing, corrupt, or carries an invalid row in the middle of the stream; the
bundle carries a value Postgres rejects; a second ingest arrives while one is
running. All of them go through the production code paths (FastAPI app,
`tile_service.update_from_parquet`, `master_service`, `bundle_service.apply`).

Catalog tests implemented (`resilience`):
- "controlled failure tests (fault injection)"
- "recovery testing"
"""
from __future__ import annotations

import os
import socket
import threading
import time

import h3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import main as app_module
from application.services import bundle_service, master_service, tile_service
from infrastructure.db import catalog_seed, master_repository
from infrastructure.db import hexagon_repository as repo
from infrastructure.db import session as app_session
from infrastructure.db.models import Hexagon, HexagonMeta
from infrastructure.db.session import get_db
from shared import config
from tests.integration.conftest import TEST_DATABASE_URL

pytestmark = [pytest.mark.qa_resilience, pytest.mark.postgres]

SECRET = "test-secret"
BOGOTA = h3.latlng_to_cell(4.71, -74.07, 8)
MEDELLIN = h3.latlng_to_cell(6.24, -75.58, 8)
CALI = h3.latlng_to_cell(3.45, -76.53, 8)
_INGEST_LOCK_KEY = bundle_service._INGEST_LOCK_KEY
# Upper bound for "fails fast": far below any client or load-balancer timeout.
_FAST_S = 5.0


@pytest.fixture
def app_db_is_test_db():
    """Some code paths open their own session through the application engine
    (`main.SessionLocal`, `master_service._ingest`). Those tests need the
    application engine to point at the test database (DATABASE_URL ==
    TEST_DATABASE_URL, as in CI)."""
    app_url = app_session.engine.url.render_as_string(hide_password=False)
    test_url = make_url(TEST_DATABASE_URL).render_as_string(hide_password=False)
    if app_url != test_url:
        pytest.skip("DATABASE_URL must equal TEST_DATABASE_URL for this test")


@pytest.fixture
def dead_engine():
    """Engine to a port nobody listens on: every connection attempt is refused."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = make_url(TEST_DATABASE_URL).set(host="127.0.0.1", port=port)
    engine = create_engine(url, pool_pre_ping=True)
    yield engine
    engine.dispose()


def _headers():
    return {"X-Internal-Secret": SECRET, "X-User-Sub": "resilience-user"}


def _write_master(path, cells=(BOGOTA, MEDELLIN), tec=(6.0, 3.0)):
    pq.write_table(pa.table({"h3_index": list(cells), "TEC_001": list(tec)}), path)


def _snapshot(db) -> tuple[dict, dict]:
    db.expire_all()
    hexes = {h.h3_index: (h.score_general, h.variables) for h in db.scalars(select(Hexagon))}
    meta = {m.variable: (m.vmin, m.vmax) for m in db.scalars(select(HexagonMeta))}
    return hexes, meta


@pytest.fixture
def baseline(db, monkeypatch, tmp_path):
    """A healthy master already ingested by the production (COPY) path."""
    monkeypatch.setattr(tile_service, "HEXAGON_INGEST", "copy")
    good = tmp_path / "good.h3.parquet"
    _write_master(good)
    assert tile_service.update_from_parquet(db, str(good)) == 2
    return _snapshot(db)


# ── Database unavailable ─────────────────────────────────────────────────────

def test_health_degrades_quickly_when_database_is_unreachable(dead_engine, monkeypatch):
    monkeypatch.setattr(app_module, "SessionLocal", sessionmaker(bind=dead_engine))

    t0 = time.monotonic()
    r = TestClient(app_module.app).get("/api/health")
    elapsed = time.monotonic() - t0

    assert r.status_code == 200
    assert r.json() == {"status": "ok", "index_ready": False}
    assert elapsed < _FAST_S


def test_data_endpoint_fails_fast_when_database_is_unreachable(dead_engine, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", SECRET)
    Dead = sessionmaker(bind=dead_engine)

    def _dead_db():
        s = Dead()
        try:
            yield s
        finally:
            s.close()

    app_module.app.dependency_overrides[get_db] = _dead_db
    try:
        t0 = time.monotonic()
        r = TestClient(app_module.app, raise_server_exceptions=False).get(
            "/api/tiles/variables", headers=_headers())
        elapsed = time.monotonic() - t0
    finally:
        app_module.app.dependency_overrides.clear()

    assert r.status_code >= 500
    assert elapsed < _FAST_S


def test_health_recovers_when_database_comes_back(app_db_is_test_db, baseline, dead_engine,
                                                  monkeypatch):
    client = TestClient(app_module.app)
    original = app_module.SessionLocal

    monkeypatch.setattr(app_module, "SessionLocal", sessionmaker(bind=dead_engine))
    assert client.get("/api/health").json()["index_ready"] is False

    monkeypatch.setattr(app_module, "SessionLocal", original)  # fault removed
    assert client.get("/api/health").json() == {"status": "ok", "index_ready": True}


# ── Master ingest faults (production COPY path) ──────────────────────────────

def _corrupt(tmp_path):
    p = tmp_path / "corrupt.h3.parquet"
    p.write_bytes(b"PAR1" + os.urandom(256) + b"PAR1")
    return p


def _missing(tmp_path):
    return tmp_path / "does-not-exist.h3.parquet"


def _bad_row_mid_stream(tmp_path):
    p = tmp_path / "bad-row.h3.parquet"
    _write_master(p, cells=(CALI, "zz-not-a-cell", BOGOTA), tec=(1.0, 2.0, 3.0))
    return p


@pytest.mark.parametrize("fault", [_corrupt, _missing, _bad_row_mid_stream],
                         ids=["corrupt-file", "missing-file", "invalid-row-mid-stream"])
def test_failed_master_ingest_leaves_previous_dataset_intact(baseline, db, session_factory,
                                                            fault, tmp_path):
    with pytest.raises(Exception):
        tile_service.update_from_parquet(db, str(fault(tmp_path)))
    db.rollback()  # what closing the request/worker session does

    fresh = session_factory()
    try:
        assert _snapshot(fresh) == baseline  # no partial rows, nothing lost
    finally:
        fresh.close()


def test_failed_master_update_is_recorded_and_does_not_block_the_next_one(
        app_db_is_test_db, baseline, db, monkeypatch, tmp_path):
    """Admin 'update master' with a missing parquet: the background ingest fails.
    The version must end as `failed` (with the reason), the dataset must be
    intact, and a new update must be accepted once the fault is removed."""
    def run_and_wait(version_id, source):
        worker = threading.Thread(target=master_service._ingest, args=(version_id, source))
        worker.start()
        worker.join(30)
        assert not worker.is_alive()

    monkeypatch.setattr(master_service, "_start_ingest", run_and_wait)

    mv = master_service.request_update(db, "immediate", None, str(_missing(tmp_path)))

    db.expire_all()
    failed = master_repository.get(db, mv["id"])
    assert _snapshot(db) == baseline
    assert failed.status == "failed", f"version left in status {failed.status!r}"
    assert failed.notes
    assert master_repository.is_updating(db) is False

    good = tmp_path / "fixed.h3.parquet"
    _write_master(good)
    retry = master_service.request_update(db, "immediate", None, str(good))  # must not raise
    db.expire_all()
    assert master_repository.get(db, retry["id"]).status == "ready"


# ── Bundle ingest: concurrency and lock recovery ─────────────────────────────

def _bundle(root, amb=(4.0, 2.0)):
    os.makedirs(root, exist_ok=True)
    pq.write_table(pa.table({
        "h3_index": [BOGOTA, MEDELLIN],
        "global": [4.2, 1.8],
        "AMB_005": list(amb),
    }), os.path.join(root, "variables.h3.parquet"))
    return str(root)


def _advisory_locks_held(engine_url: str) -> int:
    probe = create_engine(engine_url, poolclass=NullPool)
    try:
        with probe.connect() as c:
            return c.execute(text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :k"
            ), {"k": _INGEST_LOCK_KEY}).scalar()
    finally:
        probe.dispose()


def test_concurrent_bundle_ingest_is_rejected_then_accepted_after_release(db, db_engine, tmp_path):
    catalog_seed.seed_catalog(db)
    root = _bundle(tmp_path / "bundle")

    running = db_engine.connect()  # another worker holding the single-flight lock
    try:
        running.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _INGEST_LOCK_KEY})
        rejected = bundle_service.apply(db, root)
        assert "error" in rejected and "en curso" in rejected["error"]
        assert repo.count(db) == 0  # the rejected ingest wrote nothing
    finally:
        running.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _INGEST_LOCK_KEY})
        running.close()

    accepted = bundle_service.apply(db, root)  # fault removed → recovers
    assert accepted.get("rows") == 2, accepted
    assert repo.count(db) == 2


def test_failed_bundle_ingest_releases_the_ingest_lock(db, db_engine, session_factory, tmp_path):
    """A bundle whose data Postgres rejects (a NUL character inside a text value)
    makes the ingest fail. After the failing request ends, the single-flight lock
    must be free again; otherwise every later ingest is refused with 'Ya hay una
    ingesta en curso' until the pooled connection is recycled."""
    catalog_seed.seed_catalog(db)
    root = _bundle(tmp_path / "bad", amb=("ok", "bad\x00value"))
    try:
        with pytest.raises(Exception):
            bundle_service.apply(db, root)
        db.close()  # end of the request: the session goes back to the pool

        url = db_engine.url.render_as_string(hide_password=False)
        assert _advisory_locks_held(url) == 0, "ingest lock still held after the failed ingest"
    finally:
        db_engine.dispose()  # test hygiene: drop pooled connections (and any leaked lock)
