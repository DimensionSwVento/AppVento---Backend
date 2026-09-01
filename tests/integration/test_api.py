"""
RNF-1 · Disponibilidad / RNF-2 · Integración — API FastAPI (TestClient, sin Postgres/Keycloak).

La autenticación se prueba por la vía BFF interna (`X-Internal-Secret` + `X-User-Sub`),
que es el contrato real entre Next.js y FastAPI; `ensure_user` se parchea a no-op para
no tocar la BD. La fuente de hexágonos se reemplaza por la fake en memoria.
"""
from __future__ import annotations

import h3
import pytest
from fastapi.testclient import TestClient

import main as app_module
from api import deps
from infrastructure.db import repository
from infrastructure.db.session import get_db
from shared import config
from tests.conftest import FakeHexagonSource, hexrow

SECRET = "test-secret"
BOGOTA = h3.latlng_to_cell(4.71, -74.07, 8)


def _fake_source():
    lat, lng = h3.cell_to_latlng(BOGOTA)
    return FakeHexagonSource(
        rows=[hexrow(BOGOTA, score=0.8, lat=lat, lng=lng,
                     variables={"TEC_001": 6.0, "SOE_001": 10.0})],
        ranges={"TEC_001": {"min": 0.0, "max": 6.0},
                "SOE_001": {"min": 0.0, "max": 10.0}},
    )


def _auth_headers():
    return {"X-Internal-Secret": SECRET, "X-User-Sub": "test-user"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", SECRET)
    monkeypatch.setattr(repository, "ensure_user", lambda *a, **k: None)

    def _fake_db():
        yield object()  # ensure_user está parcheado; la sesión no se usa

    src = _fake_source()
    app_module.app.dependency_overrides[get_db] = _fake_db
    app_module.app.dependency_overrides[deps.get_hexagon_source] = lambda: src

    yield TestClient(app_module.app)

    app_module.app.dependency_overrides.clear()


# ── RNF-1 · Disponibilidad ────────────────────────────────────────────────────

def test_health_returns_ok_when_source_ready(monkeypatch):
    class FakeDb:
        def close(self): ...

    class FakePgSource:
        def __init__(self, db): ...
        def is_ready(self): return True

    monkeypatch.setattr(app_module, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(app_module, "PostgresHexagonSource", FakePgSource)

    client = TestClient(app_module.app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "index_ready": True}


def test_warmup_is_open_and_reports_metadata(client):
    resp = client.get("/api/tiles/warmup")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["count"] == 1


# ── RNF-2 · Integración (contrato de la API) ──────────────────────────────────

def test_tiles_require_auth(client):
    assert client.get("/api/tiles/variables").status_code == 401


def test_tiles_accept_internal_secret(client):
    resp = client.get("/api/tiles/variables", headers=_auth_headers())
    assert resp.status_code == 200
    names = {v["name"] for v in resp.json()["variables"]}
    assert names == {"SOE_001", "TEC_001"}


def test_missing_sub_with_secret_is_401(client):
    resp = client.get("/api/tiles/variables",
                      headers={"X-Internal-Secret": SECRET})
    assert resp.status_code == 401


def test_tile_endpoint_returns_compact_hexes(client):
    resp = client.get("/api/tiles/6/18/31", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert body  # contiene al menos el hex de Bogotá agregado
    for h in body:
        assert set(h.keys()) == {"h", "s", "t", "la", "ln", "v"}


def test_detail_endpoint_found_and_missing(client):
    found = client.get(f"/api/tiles/detail/{BOGOTA}", headers=_auth_headers())
    assert found.status_code == 200
    assert found.json()["h3_index"] == BOGOTA

    missing = client.get("/api/tiles/detail/not-a-cell", headers=_auth_headers())
    assert missing.status_code == 404


def test_area_endpoint_returns_scores(client):
    resp = client.post("/api/tiles/area", headers=_auth_headers(),
                       json={"hexes": [BOGOTA]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["h3_index"] == BOGOTA
    assert body[0]["scores"]["TEC"] == 1.0
    assert body[0]["scores"]["SOE"] == 1.0


def test_area_endpoint_requires_polygon_or_hexes(client):
    resp = client.post("/api/tiles/area", headers=_auth_headers(), json={})
    assert resp.status_code == 400
