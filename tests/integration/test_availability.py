"""
Availability — the map service keeps answering while its data source changes
underneath it.

Catalog tests implemented (`availability`):
- "GIS service recovery tests": the application's database connections are
  killed server-side (failover, idle-timeout, Postgres restart); the next
  request must be served on a fresh connection (`pool_pre_ping`), not fail.
- "connectivity tests to data sources" is implemented by the Postgres source
  tests (test_hexagon_query_repository.py, test_api_db.py).

Also checked: the liveness endpoint must stay responsive while a master ingest
is running (the admin can trigger one from the UI at any time).
"""
from __future__ import annotations

import threading

import h3
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

import main as app_module
from infrastructure.db import hexagon_repository as repo
from infrastructure.db import session as app_session
from tests.integration.conftest import TEST_DATABASE_URL

pytestmark = [pytest.mark.qa_availability, pytest.mark.postgres]

BOGOTA = h3.latlng_to_cell(4.71, -74.07, 8)
_LIVENESS_BUDGET_S = 5.0


@pytest.fixture
def app_engine():
    """The application's own engine (infrastructure/db/session.py), pointed at the
    test database (DATABASE_URL == TEST_DATABASE_URL, as in CI)."""
    app_url = app_session.engine.url.render_as_string(hide_password=False)
    if app_url != make_url(TEST_DATABASE_URL).render_as_string(hide_password=False):
        pytest.skip("DATABASE_URL must equal TEST_DATABASE_URL for this test")
    app_session.engine.dispose()
    yield app_session.engine
    app_session.engine.dispose()


@pytest.fixture
def seeded(db):
    lat, lng = h3.cell_to_latlng(BOGOTA)
    repo.upsert(db, {"h3_index": BOGOTA, "score_general": 4.2, "lat": lat, "lng": lng,
                     "variables": {"TEC_001": 6.0}})
    db.commit()  # end the read transaction opened by upsert's refresh (no idle-in-transaction)
    return db


def test_service_recovers_after_its_database_connections_are_killed(app_engine, seeded, db_engine):
    client = TestClient(app_module.app)
    assert client.get("/api/health").json()["index_ready"] is True  # pool now holds a connection

    with app_engine.connect() as c:
        app_pids = {c.execute(text("SELECT pg_backend_pid()")).scalar()}
    with db_engine.connect() as admin:
        killed = admin.execute(
            text("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                 "WHERE pid = ANY(:pids)"), {"pids": list(app_pids)}).scalar()
    assert killed == len(app_pids)

    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "index_ready": True}


def test_health_stays_responsive_while_a_master_ingest_is_running(app_engine, seeded,
                                                                  session_factory):
    """A destructive master ingest (production COPY path) is in progress: the
    liveness endpoint must still answer within the budget."""
    lat, lng = h3.cell_to_latlng(BOGOTA)
    ingest_started, finish_ingest = threading.Event(), threading.Event()

    def slow_master():  # the parquet stream of a large master: rows keep coming
        yield {"h3_index": BOGOTA, "score_general": 3.0, "lat": lat, "lng": lng, "variables": {}}
        ingest_started.set()
        finish_ingest.wait(30)

    def ingest():
        s = session_factory()
        try:
            repo.copy_ingest(s, slow_master())
        finally:
            s.close()

    result: dict = {}

    def probe():
        result["response"] = TestClient(app_module.app).get("/api/health")

    ingester = threading.Thread(target=ingest)
    prober = threading.Thread(target=probe)
    ingester.start()
    try:
        assert ingest_started.wait(10)
        prober.start()
        prober.join(_LIVENESS_BUDGET_S)
        answered_in_time = not prober.is_alive()
    finally:
        finish_ingest.set()
        ingester.join(30)
        if prober.is_alive() or prober.ident is not None:
            prober.join(30)

    assert answered_in_time, (
        f"/api/health did not answer within {_LIVENESS_BUDGET_S}s while a master ingest "
        "was running")
    assert result["response"].status_code == 200
