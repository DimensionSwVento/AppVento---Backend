"""
RNF-1 · Disponibilidad — servicio de histórico de informes (PostgreSQL real).
"""
from __future__ import annotations

import pytest

from application.services import historial_service
from infrastructure.db import repository


@pytest.mark.postgres
def test_save_and_list_for_user(db):
    repository.ensure_user(db, "sub-1", "a@x.co", [])
    saved = historial_service.save_for_user(db, "sub-1", {
        "nombre": "Informe", "hexes": ["h1"], "variables": ["TEC_001"],
        "caracteristicas": {"capacidad": 10}, "scores": {"TEC": 0.8},
        "region": "Antioquia", "areaKm2": 3.2,
    })
    assert saved["nombre"] == "Informe"
    assert saved["scores"]["TEC"] == 0.8
    assert saved["areaKm2"] == 3.2

    listed = historial_service.list_for_user(db, "sub-1")
    assert [i["id"] for i in listed] == [saved["id"]]


@pytest.mark.postgres
def test_save_applies_defaults_and_snake_case(db):
    repository.ensure_user(db, "sub-1", "a@x.co", [])
    saved = historial_service.save_for_user(db, "sub-1", {})
    assert saved["nombre"] == "Informe de prefactibilidad"
    assert saved["areaKm2"] == 0.0
    assert saved["hexes"] == []
    assert saved["scores"] == {}

    # Acepta area_km2 (snake) como alternativa.
    saved2 = historial_service.save_for_user(db, "sub-1", {"area_km2": 5.5})
    assert saved2["areaKm2"] == 5.5


@pytest.mark.postgres
def test_delete_for_user_guards_ownership(db):
    repository.ensure_user(db, "sub-1", "a@x.co", [])
    repository.ensure_user(db, "sub-2", "b@x.co", [])
    saved = historial_service.save_for_user(db, "sub-1", {"nombre": "A"})
    assert historial_service.delete_for_user(db, "sub-2", saved["id"]) is False
    assert historial_service.delete_for_user(db, "sub-1", saved["id"]) is True
