"""Regresión: ids H3 mal formados deben tratarse como "no encontrado" (no 500).

Desde d1bb439 la PK `hexagon.h3_index` es BIGINT y el TypeDecorator `H3Index`
convierte con ``int(token, 16)``; sin esta guarda un id no hexadecimal lanzaba
ValueError en el bind y la API respondía HTTP 500 en vez de 404.
"""
import pytest

from infrastructure.db.models import is_h3_token


@pytest.mark.parametrize("value", ["8866e4288bfffff", "614299631223832575", 614299631223832575])
def test_valid_tokens(value):
    assert is_h3_token(value)


@pytest.mark.parametrize("value", ["missing", "not-a-cell", "", None, "8866e4288bfffzz"])
def test_invalid_tokens(value):
    assert not is_h3_token(value)
