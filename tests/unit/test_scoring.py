"""
RNF-3 · Procesamiento / RNF-4 · Calidad de datos — scoring y pertenencia a Colombia.
"""
from __future__ import annotations

import pytest

from domain.query_engine.scoring import (
    calc_temp_viability,
    effective_score,
    is_in_colombia,
    zoom_to_h3_res,
)


# ── Score placeholder (se reemplazará por el modelo AHP real) ─────────────────

def test_calc_temp_viability_with_all_values():
    v = {"TEC_001": 12, "TEC_004": 600, "SOE_001": 0.5, "SOE_002": 0}
    # wind=1.0, density=1.0, reserve=0.2, cig=1.0
    expected = 1.0 * 0.40 + 1.0 * 0.35 + 0.2 * 0.15 + 1.0 * 0.10
    assert calc_temp_viability(v) == pytest.approx(expected)


def test_calc_temp_viability_with_missing_variables():
    # Todas ausentes → defaults: wind 0.5, density 0.5, reserve 1.0, cig 0.5.
    expected = 0.5 * 0.40 + 0.5 * 0.35 + 1.0 * 0.15 + 0.5 * 0.10
    assert calc_temp_viability({}) == pytest.approx(expected)


def test_calc_temp_viability_reads_string_numbers():
    v = {"TEC_001": "12", "TEC_004": "600", "SOE_001": "0.5", "SOE_002": "0"}
    expected = 1.0 * 0.40 + 1.0 * 0.35 + 0.2 * 0.15 + 1.0 * 0.10
    assert calc_temp_viability(v) == pytest.approx(expected)


def test_calc_temp_viability_clamps_out_of_range():
    v = {"TEC_001": 1000, "TEC_004": 100000, "SOE_001": 0.5, "SOE_002": -50}
    # wind y density se saturan a 1.0; cig = 1 - 0 = 1.0.
    assert 0.0 <= calc_temp_viability(v) <= 1.0


def test_effective_score_prefers_parquet_score():
    assert effective_score(0.73, {"TEC_001": 12}) == pytest.approx(0.73)


def test_effective_score_falls_back_when_score_missing():
    assert effective_score(0.0, {"TEC_001": 12, "TEC_004": 600,
                                 "SOE_001": 0.5, "SOE_002": 0}) == pytest.approx(
        calc_temp_viability({"TEC_001": 12, "TEC_004": 600, "SOE_001": 0.5, "SOE_002": 0})
    )


# ── Zoom → resolución H3 ──────────────────────────────────────────────────────

def test_zoom_to_h3_res():
    assert zoom_to_h3_res(10) == 7
    assert zoom_to_h3_res(9) == 6
    assert zoom_to_h3_res(8) == 6
    assert zoom_to_h3_res(7) == 5
    assert zoom_to_h3_res(6) == 4
    assert zoom_to_h3_res(5) == 3
    assert zoom_to_h3_res(0) == 3


# ── Pertenencia a Colombia (RNF-4 · completitud territorial) ──────────────────

def test_is_in_colombia_bogota():
    assert is_in_colombia(4.71, -74.07) is True


def test_is_in_colombia_medellin():
    assert is_in_colombia(6.24, -75.58) is True


def test_is_in_colombia_san_andres_archipelago():
    assert is_in_colombia(12.58, -81.70) is True


def test_is_in_colombia_providencia():
    assert is_in_colombia(13.35, -81.37) is True


def test_is_in_colombia_rejects_quito():
    assert is_in_colombia(-0.18, -78.47) is False


def test_is_in_colombia_rejects_caracas():
    assert is_in_colombia(10.48, -66.90) is False


def test_is_in_colombia_rejects_out_of_bounds():
    assert is_in_colombia(50.0, -74.0) is False  # lat > 13.5
    assert is_in_colombia(4.71, -90.0) is False  # lng < -82
