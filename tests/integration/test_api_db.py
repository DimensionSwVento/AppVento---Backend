"""
RNF-1 · Disponibilidad / RNF-2 · Integración — API completa contra PostgreSQL real.

Usa el override de `get_db` (sesión del motor de test) y deja que `ensure_user`
provisione el usuario en la BD real. No usa `with` en el TestClient para evitar el
lifespan (que arrancaría `init_db` contra la BD de la app, no la de test).
"""
from __future__ import annotations

import h3
import pytest
from fastapi.testclient import TestClient

import main as app_module
from infrastructure.db import hexagon_repository as repo
from infrastructure.db.models import HexagonMeta
from infrastructure.db.session import get_db
from shared import config

SECRET = "test-secret"
BOGOTA = h3.latlng_to_cell(4.71, -74.07, 8)


def _headers():
    return {"X-Internal-Secret": SECRET, "X-User-Sub": "test-user"}


@pytest.fixture
def client(session_factory, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", SECRET)

    def _get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app_module.app.dependency_overrides[get_db] = _get_db
    yield TestClient(app_module.app)
    app_module.app.dependency_overrides.clear()


def _seed_hexagons(db):
    lat, lng = h3.cell_to_latlng(BOGOTA)
    repo.upsert(db, {"h3_index": BOGOTA, "score_general": 0.8, "tipo_celda": "disponible",
                     "lat": lat, "lng": lng, "variables": {"TEC_001": 6.0}})
    db.add(HexagonMeta(variable="TEC_001", vmin=0.0, vmax=6.0))
    db.commit()


@pytest.mark.postgres
def test_historial_crud_flow(client):
    resp = client.post("/api/historico", headers=_headers(),
                       json={"nombre": "Informe", "hexes": ["h1"]})
    assert resp.status_code == 201
    informe_id = resp.json()["id"]
    assert resp.json()["nombre"] == "Informe"

    listed = client.get("/api/historico", headers=_headers())
    assert listed.status_code == 200
    assert [i["id"] for i in listed.json()] == [informe_id]

    deleted = client.delete(f"/api/historico/{informe_id}", headers=_headers())
    assert deleted.status_code == 204
    assert client.get("/api/historico", headers=_headers()).json() == []


@pytest.mark.postgres
def test_historial_requires_auth(client):
    assert client.get("/api/historico").status_code == 401


@pytest.mark.postgres
def test_tiles_with_real_source(client, db):
    _seed_hexagons(db)
    # El guard provisiona el usuario real en la BD (ensure_user sin parchear).
    resp = client.get("/api/tiles/variables", headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["variables"][0]["name"] == "TEC_001"

    detail = client.get(f"/api/tiles/detail/{BOGOTA}", headers=_headers())
    assert detail.status_code == 200
    assert detail.json()["score_general"] == 0.8

    counts = client.get("/api/tiles/counts", headers=_headers())
    assert counts.status_code == 200
    assert counts.json()["total"] == 1


@pytest.mark.postgres
def test_tiles_area_with_real_source(client, db):
    _seed_hexagons(db)
    resp = client.post("/api/tiles/area", headers=_headers(), json={"hexes": [BOGOTA]})
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["h3_index"] == BOGOTA
    assert body[0]["scores"]["TEC"] == 1.0
