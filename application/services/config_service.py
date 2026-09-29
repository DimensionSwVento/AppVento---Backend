"""
Config editable clave→valor (tabla `app_config`). Store genérico para textos y
parámetros que el admin ajusta sin tocar código; el valor es un blob JSON libre y
cada consumidor conoce su forma. Hoy lo usa el "Resumen del cálculo" del informe
(`report_calc_text`); reutilizable para otros ajustes.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from infrastructure.db.models import AppConfig


def get_config(db: Session, key: str) -> dict | None:
    """Valor JSON de `key`, o `None` si no existe (el consumidor usa su default)."""
    row = db.get(AppConfig, key)
    return row.value if row else None


def set_config(db: Session, key: str, value: dict) -> dict:
    """Upsert del valor JSON de `key`. Devuelve el valor persistido."""
    row = db.get(AppConfig, key)
    if row is None:
        row = AppConfig(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()
    return value
