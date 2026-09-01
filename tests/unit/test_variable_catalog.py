"""
RNF-4 · Calidad de datos — catálogo de variables: códigos → nombres y dimensión.
"""
from __future__ import annotations

import infrastructure.data.variable_catalog as vc


def test_base_code_collapses_subvariant():
    assert vc.base_code("TEC_001_sv001") == "TEC_001"
    assert vc.base_code("TEC_001_sv005") == "TEC_001"


def test_base_code_keeps_plain_codes():
    assert vc.base_code("TEC_005") == "TEC_005"
    assert vc.base_code("SOE_001") == "SOE_001"


def test_dimension_of_known_prefixes():
    assert vc.dimension_of("TEC_001") == "TEC"
    assert vc.dimension_of("AMB_001") == "AMB"
    assert vc.dimension_of("SOE_001") == "SOE"
    assert vc.dimension_of("MAC_001") == "MAC"
    assert vc.dimension_of("REG_001") == "REG"


def test_dimension_of_unknown_prefix_is_none():
    assert vc.dimension_of("XYZ_001") is None


def test_get_variable_label_falls_back_to_code():
    # Sin LABELS cargado, el fallback es el código con espacios.
    assert vc.get_variable_label("TEC_001") == "TEC 001"


def test_get_variable_label_uses_catalog_when_present(monkeypatch):
    monkeypatch.setitem(vc.LABELS, "TEC_001", "Velocidad de viento")
    assert vc.get_variable_label("TEC_001") == "Velocidad de viento"
    # La sub-variante también resuelve a su base.
    assert vc.get_variable_label("TEC_001_sv001") == "Velocidad de viento"
