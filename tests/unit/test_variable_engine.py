"""
RNF-3 · Procesamiento — motor de variables: resolución, lectura y normalización.
"""
from __future__ import annotations

import pytest

from domain.query_engine import variable_engine as ve
from tests.conftest import hexrow


def _hx(variables):
    return hexrow("h3idx", variables=variables)


# ── Resolución de nombres ─────────────────────────────────────────────────────

def test_is_category():
    assert ve.is_category("TEC") is True
    assert ve.is_category("SOE") is True
    assert ve.is_category("TEC_001") is False
    assert ve.is_category("TEC_001_sv001") is False


def test_resolve_variable_names_category():
    available = ["TEC_001", "TEC_004", "SOE_001"]
    assert ve.resolve_variable_names("TEC", available) == ["TEC_001", "TEC_004"]


def test_resolve_variable_names_concrete():
    available = ["TEC_001", "TEC_004"]
    assert ve.resolve_variable_names("TEC_001", available) == ["TEC_001"]


# ── Lectura de valor ──────────────────────────────────────────────────────────

def test_get_variable_value_numeric_and_string_and_none():
    hx = _hx({"A": 5.0, "B": "7", "C": "no-num", "D": None})
    assert ve.get_variable_value(hx, "A") == 5.0
    assert ve.get_variable_value(hx, "B") == 7.0
    assert ve.get_variable_value(hx, "C") is None
    assert ve.get_variable_value(hx, "D") is None
    assert ve.get_variable_value(hx, "MISSING") is None


def test_get_aggregated_value_averages_category():
    hx = _hx({"TEC_001": 2.0, "TEC_004": 4.0, "SOE_001": 9.0})
    assert ve.get_aggregated_value(hx, "TEC", ["TEC_001", "TEC_004", "SOE_001"]) == 3.0


def test_get_aggregated_value_returns_none_when_empty():
    hx = _hx({})
    assert ve.get_aggregated_value(hx, "TEC", []) is None


# ── Normalización ─────────────────────────────────────────────────────────────

def test_normalize_basic():
    assert ve.normalize(0.5, 0.0, 1.0) == pytest.approx(0.5)


def test_normalize_clamps():
    assert ve.normalize(2.0, 0.0, 1.0) == 1.0
    assert ve.normalize(-1.0, 0.0, 1.0) == 0.0


def test_normalize_zero_range_returns_zero():
    assert ve.normalize(5.0, 5.0, 5.0) == 0.0


# ── resolve_value ─────────────────────────────────────────────────────────────

def test_resolve_value_score_mode():
    hx = hexrow("h3idx", score=0.42, variables={})
    raw, norm = ve.resolve_value(hx, "TEC", [], None, "score")
    assert raw == 0.42
    assert norm is None


def test_resolve_value_raw_mode():
    hx = _hx({"TEC_001": 3.0})
    raw, norm = ve.resolve_value(hx, "TEC_001", ["TEC_001"], None, "raw")
    assert raw == 3.0
    assert norm is None


def test_resolve_value_normalized_mode():
    hx = _hx({"TEC_001": 3.0})
    raw, norm = ve.resolve_value(hx, "TEC_001", ["TEC_001"],
                                 {"min": 0.0, "max": 6.0}, "normalized")
    assert raw == 3.0
    assert norm == pytest.approx(0.5)


def test_resolve_value_missing_returns_none():
    hx = _hx({})
    raw, norm = ve.resolve_value(hx, "TEC_001", ["TEC_001"],
                                 {"min": 0.0, "max": 6.0}, "normalized")
    assert raw is None
    assert norm is None
