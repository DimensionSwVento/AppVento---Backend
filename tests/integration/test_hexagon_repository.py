"""
RNF-4 · Calidad de datos — persistencia de hexágonos (ingesta + CRUD, PostgreSQL real).
"""
from __future__ import annotations

import h3
import pytest

from infrastructure.db import hexagon_repository as repo
from infrastructure.db.models import HexagonMeta

BOGOTA = h3.latlng_to_cell(4.71, -74.07, 8)
MEDELLIN = h3.latlng_to_cell(6.24, -75.58, 8)


def _row(h3_index=BOGOTA, score=0.7, tipo="disponible", variables=None):
    lat, lng = h3.cell_to_latlng(h3_index)
    return {"h3_index": h3_index, "score_general": score, "tipo_celda": tipo,
            "lat": lat, "lng": lng, "variables": variables or {}}


# ── Sanitizado / helpers puros ────────────────────────────────────────────────

def test_clean_variables_normalizes_numpy_and_nan():
    import numpy as np
    out = repo._clean_variables({"a": np.float64(1.5), "b": np.nan, "c": None,
                                 "d": "texto", "e": 2})
    assert out["a"] == 1.5
    assert out["b"] is None
    assert out["c"] is None
    assert out["d"] == "texto"
    assert out["e"] == 2


def test_to_row_applies_defaults():
    row = repo._to_row({"h3_index": BOGOTA, "score_general": None,
                        "tipo_celda": None, "variables": {"x": 1}})
    assert row["score_general"] == 0.0
    assert row["tipo_celda"] == "disponible"
    assert row["h3_index"] == BOGOTA


def test_as_number():
    assert repo._as_number(True) is None
    assert repo._as_number(5) == 5.0
    assert repo._as_number("7.5") == 7.5
    assert repo._as_number("no") is None
    assert repo._as_number(None) is None


# ── Ingesta ORM (replace_all) ─────────────────────────────────────────────────

@pytest.mark.postgres
def test_replace_all_ingests_and_builds_meta(db):
    rows = [
        _row(BOGOTA, variables={"TEC_001": 6.0, "REG_001": "A"}),
        _row(MEDELLIN, variables={"TEC_001": 3.0}),
    ]
    n = repo.replace_all(db, rows)
    assert n == 2
    assert repo.count(db) == 2

    metas = {m.variable: (m.vmin, m.vmax)
             for m in db.query(HexagonMeta).all()}
    assert metas == {"TEC_001": (3.0, 6.0)}


@pytest.mark.postgres
def test_replace_all_clears_previous_data(db):
    repo.replace_all(db, [_row(BOGOTA)])
    repo.replace_all(db, [_row(MEDELLIN)])
    assert repo.count(db) == 1
    assert repo.get(db, MEDELLIN) is not None
    assert repo.get(db, BOGOTA) is None


# ── Ingesta COPY ──────────────────────────────────────────────────────────────

@pytest.mark.postgres
def test_copy_ingest_ingests_and_builds_meta(db):
    progress = []
    rows = [_row(BOGOTA, variables={"TEC_001": 6.0}), _row(MEDELLIN, variables={"TEC_001": 3.0})]
    n = repo.copy_ingest(db, rows, on_progress=progress.append)
    assert n == 2
    assert repo.count(db) == 2
    assert progress[-1] == 2  # reporte final
    metas = {m.variable: (m.vmin, m.vmax) for m in db.query(HexagonMeta).all()}
    assert metas == {"TEC_001": (3.0, 6.0)}


# ── CRUD ──────────────────────────────────────────────────────────────────────

@pytest.mark.postgres
def test_upsert_creates_then_updates(db):
    row = _row(BOGOTA, score=0.5, variables={"TEC_001": 1.0})
    hx = repo.upsert(db, row)
    assert hx.h3_index == BOGOTA
    assert hx.score_general == 0.5

    updated = repo.upsert(db, _row(BOGOTA, score=0.9, variables={"TEC_001": 2.0}))
    assert updated.score_general == 0.9
    assert repo.count(db) == 1


@pytest.mark.postgres
def test_update_and_delete(db):
    repo.upsert(db, _row(BOGOTA, score=0.5))
    changed = repo.update(db, BOGOTA, {"score_general": 0.2, "tipo_celda": "excluido"})
    assert changed.score_general == 0.2
    assert changed.tipo_celda == "excluido"

    assert repo.update(db, "missing", {"score_general": 0.1}) is None
    assert repo.delete_one(db, BOGOTA) is True
    assert repo.delete_one(db, BOGOTA) is False


@pytest.mark.postgres
def test_list_all_and_count(db):
    repo.replace_all(db, [_row(BOGOTA), _row(MEDELLIN)])
    assert repo.count(db) == 2
    assert {h.h3_index for h in repo.list_all(db)} == {BOGOTA, MEDELLIN}


# ── Carga para warmup ─────────────────────────────────────────────────────────

@pytest.mark.postgres
def test_load_for_warmup_reconstructs_shape(db):
    repo.replace_all(db, [_row(BOGOTA, variables={"TEC_001": 6.0, "SOE_001": None})])
    hexagons, names, parsed_at = repo.load_for_warmup(db)
    assert len(hexagons) == 1
    assert names == ["SOE_001", "TEC_001"]  # sorted()
    assert hexagons[0].h3_index == BOGOTA
    assert parsed_at
