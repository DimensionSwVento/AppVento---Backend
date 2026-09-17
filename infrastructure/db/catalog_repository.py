"""
Operaciones de BD del CATÁLOGO de administración (US08-01/02/03):
variables, normas y escenarios. Estilo igual que `repository.py` (bajo nivel).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.db.models import CatalogVariable, Escenario, Norma, Turbine


# ── Variables ────────────────────────────────────────────────────────────────

def list_variables(db: Session, tipo: str | None = None) -> list[CatalogVariable]:
    stmt = select(CatalogVariable)
    if tipo:
        stmt = stmt.where(CatalogVariable.tipo == tipo)
    stmt = stmt.order_by(CatalogVariable.dimension, CatalogVariable.label)
    return list(db.scalars(stmt))


def get_variable(db: Session, var_id: str) -> CatalogVariable | None:
    return db.get(CatalogVariable, var_id)


def create_variable(db: Session, data: dict) -> CatalogVariable:
    var = CatalogVariable(**data)
    db.add(var)
    db.commit()
    db.refresh(var)
    return var


def update_variable(db: Session, var_id: str, patch: dict) -> CatalogVariable | None:
    var = db.get(CatalogVariable, var_id)
    if var is None:
        return None
    # Regla de negocio: una variable del modelo NO se deshabilita.
    if var.tipo == "modelo":
        patch.pop("enabled", None)
    for key, value in patch.items():
        if hasattr(var, key):
            setattr(var, key, value)
    db.commit()
    db.refresh(var)
    return var


# ── Normas ───────────────────────────────────────────────────────────────────

def list_normas(db: Session) -> list[Norma]:
    return list(db.scalars(select(Norma).order_by(Norma.nombre)))


def create_norma(db: Session, data: dict) -> Norma:
    norma = Norma(**data)
    db.add(norma)
    db.commit()
    db.refresh(norma)
    return norma


def update_norma(db: Session, norma_id: str, patch: dict) -> Norma | None:
    norma = db.get(Norma, norma_id)
    if norma is None:
        return None
    for key, value in patch.items():
        if hasattr(norma, key) and key != "id":
            setattr(norma, key, value)
    db.commit()
    db.refresh(norma)
    return norma


def delete_norma(db: Session, norma_id: str) -> bool:
    norma = db.get(Norma, norma_id)
    if norma is None:
        return False
    db.delete(norma)
    db.commit()
    return True


# ── Escenarios ───────────────────────────────────────────────────────────────

def list_escenarios(db: Session) -> list[Escenario]:
    return list(db.scalars(select(Escenario).order_by(Escenario.label)))


def create_escenario(db: Session, data: dict) -> Escenario:
    esc = Escenario(**data)
    db.add(esc)
    db.commit()
    db.refresh(esc)
    return esc


def update_escenario(db: Session, esc_id: str, patch: dict) -> Escenario | None:
    esc = db.get(Escenario, esc_id)
    if esc is None:
        return None
    for key, value in patch.items():
        if hasattr(esc, key) and key != "id":
            setattr(esc, key, value)
    db.commit()
    db.refresh(esc)
    return esc


def delete_escenario(db: Session, esc_id: str) -> bool:
    esc = db.get(Escenario, esc_id)
    if esc is None:
        return False
    db.delete(esc)
    db.commit()
    return True


# ── Turbinas (aerogeneradores S/M/L) ─────────────────────────────────────────

def list_turbines(db: Session) -> list[Turbine]:
    return list(db.scalars(select(Turbine).order_by(Turbine.potencia_min_mw)))


def update_turbine(db: Session, turbine_id: str, patch: dict) -> Turbine | None:
    t = db.get(Turbine, turbine_id)
    if t is None:
        return None
    for key, value in patch.items():
        if hasattr(t, key) and key != "id":
            setattr(t, key, value)
    db.commit()
    db.refresh(t)
    return t
