"""
RNF-1 · Disponibilidad — persistencia de usuario e informes (PostgreSQL real).
"""
from __future__ import annotations

import pytest

from infrastructure.db import repository
from infrastructure.db.models import AppUser


@pytest.mark.postgres
def test_ensure_user_creates_new_user(db):
    user = repository.ensure_user(db, "sub-1", "a@x.co", ["admin"])
    assert isinstance(user, AppUser)
    assert user.sub == "sub-1"
    assert user.email == "a@x.co"
    assert user.roles == ["admin"]


@pytest.mark.postgres
def test_ensure_user_updates_changed_fields(db):
    repository.ensure_user(db, "sub-1", "a@x.co", ["admin"])
    user = repository.ensure_user(db, "sub-1", "b@x.co", ["analista"])
    assert user.email == "b@x.co"
    assert user.roles == ["analista"]
    assert repository.count_informes(db, "sub-1") == 0


@pytest.mark.postgres
def test_ensure_user_no_change_keeps_fields(db):
    repository.ensure_user(db, "sub-1", "a@x.co", ["admin"])
    user = repository.ensure_user(db, "sub-1", "a@x.co", ["admin"])
    assert user.email == "a@x.co"


@pytest.mark.postgres
def test_informe_crud_roundtrip(db):
    repository.ensure_user(db, "sub-1", "a@x.co", [])
    created = repository.create_informe(
        db, "sub-1",
        {"nombre": "Informe A", "hexes": ["h1"], "variables": ["v1"],
         "caracteristicas": {}, "scores_snapshot": {"TEC": 0.8},
         "region": "Cundinamarca", "area_km2": 12.5},
    )
    assert created.id

    listed = repository.list_informes(db, "sub-1")
    assert [i.id for i in listed] == [created.id]

    assert repository.count_informes(db, "sub-1") == 1
    assert repository.delete_informe(db, "sub-1", created.id) is True
    assert repository.list_informes(db, "sub-1") == []


@pytest.mark.postgres
def test_delete_informe_guards_ownership(db):
    repository.ensure_user(db, "sub-1", "a@x.co", [])
    repository.ensure_user(db, "sub-2", "b@x.co", [])
    created = repository.create_informe(db, "sub-1", {"nombre": "A"})
    # Otro usuario no puede borrarlo.
    assert repository.delete_informe(db, "sub-2", created.id) is False
    assert repository.delete_informe(db, "sub-1", "missing") is False


@pytest.mark.postgres
def test_list_informes_ordered_by_most_recent_first(db):
    repository.ensure_user(db, "sub-1", "a@x.co", [])
    repository.create_informe(db, "sub-1", {"nombre": "A"})
    repository.create_informe(db, "sub-1", {"nombre": "B"})
    names = [i.nombre for i in repository.list_informes(db, "sub-1")]
    # created_at se fija por defecto; B se creó después, así que va primero.
    assert names[0] == "B"


@pytest.mark.postgres
def test_prune_to_limit_keeps_newest(db):
    repository.ensure_user(db, "sub-1", "a@x.co", [])
    for name in ["A", "B", "C"]:
        repository.create_informe(db, "sub-1", {"nombre": name})
    removed = repository.prune_to_limit(db, "sub-1", keep=2)
    assert removed == 1
    remaining = [i.nombre for i in repository.list_informes(db, "sub-1")]
    assert remaining == ["C", "B"]
