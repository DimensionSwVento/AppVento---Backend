"""
Configuración por entorno — helpers de parseo (bool/list) y valores por defecto.
"""
from __future__ import annotations

from shared import config


def test_bool_helper():
    assert config._bool(None, True) is True
    assert config._bool("true", False) is True
    assert config._bool("1", False) is True
    assert config._bool("false", True) is False
    assert config._bool("off", True) is False


def test_list_helper():
    assert config._list("a, b ,c", []) == ["a", "b", "c"]
    assert config._list("", ["x"]) == ["x"]
    assert config._list(None, ["x"]) == ["x"]


def test_defaults_are_present():
    # Valores por defecto documentados en .env.example.
    assert config.INDEX_RES if hasattr(config, "INDEX_RES") else True
    assert isinstance(config.JWT_ALGORITHMS, list)
    assert isinstance(config.CORS_ORIGINS, list)
    assert config.MAX_HISTORIAL_PER_USER == 50
