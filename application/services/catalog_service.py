"""
Servicio del catálogo de administración (US08-01/02/03). Traduce entre el shape
del frontend (camelCase, ver `docs/api/admin-catalog.md`) y el modelo ORM
(snake_case). La lógica de BD vive en `infrastructure/db/catalog_repository`.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from infrastructure.db import catalog_repository as repo
from infrastructure.db.models import CatalogVariable, Escenario, Norma
from infrastructure.data.variable_catalog import DIMENSIONS

# Etiqueta de dimensión por id (para `dimensionLabel`).
_DIM_LABEL = {
    "tecnica": "Técnica",
    "ambiental": "Ambiental",
    "social": "Social",
    "macroeconomica": "Económica",
    "regulatoria": "Regulatoria",
}


def _dim_label(dim: str) -> str:
    return _DIM_LABEL.get(dim) or DIMENSIONS.get(dim.upper()[:3], dim)


# ── Variables ────────────────────────────────────────────────────────────────

def _var_to_dict(v: CatalogVariable) -> dict:
    return {
        "id": v.id,
        "label": v.label,
        "dimension": v.dimension,
        "dimensionLabel": v.dimension_label or _dim_label(v.dimension),
        "tipo": v.tipo,
        "enabled": v.enabled,
        "enMapa": v.en_mapa,
        "enMenu": v.en_menu,
        "excluyente": v.excluyente,
        "fuente": v.fuente,
        "unidad": v.unidad,
        "rango": v.rango,
        "descripcion": v.descripcion,
        "datasetVersion": v.dataset_version,
        "datasetUpdatedAt": v.dataset_updated_at,
        # Reales por-variable (antes hardcode en el front).
        "masterId": v.master_id,
        "criterio": v.criterio,
        "subcriterio": v.subcriterio,
        "aplicaEn": v.aplica_en,
        "noCalculo": v.no_calculo,
        "polaridad": v.polaridad,
        "peso": v.peso,
        "mapKind": v.map_kind,
        "mapThreshold": v.map_threshold,
        # ── Metadatos anidados (Fase 1): InfoVariables(2) ──
        "emplazamiento": v.emplazamiento,
        "licencia": v.licencia,
        "url": v.url_fuente,
        "clase": v.clase,
        "subcriterioId": v.subcriterio_id,
        "iconKey": v.icon_key,
    }


# Mapeo camelCase (frontend) → snake_case (ORM) para variables.
_VAR_FIELDS = {
    "label": "label",
    "dimension": "dimension",
    "dimensionLabel": "dimension_label",
    "tipo": "tipo",
    "enabled": "enabled",
    "enMapa": "en_mapa",
    "enMenu": "en_menu",
    "excluyente": "excluyente",
    "fuente": "fuente",
    "unidad": "unidad",
    "rango": "rango",
    "descripcion": "descripcion",
    "datasetVersion": "dataset_version",
    "datasetUpdatedAt": "dataset_updated_at",
    "masterId": "master_id",
    "criterio": "criterio",
    "subcriterio": "subcriterio",
    "aplicaEn": "aplica_en",
    "noCalculo": "no_calculo",
    "polaridad": "polaridad",
    "peso": "peso",
    "mapKind": "map_kind",
    "mapThreshold": "map_threshold",
    "emplazamiento": "emplazamiento",
    "licencia": "licencia",
    "url": "url_fuente",
    "clase": "clase",
    "subcriterioId": "subcriterio_id",
    "iconKey": "icon_key",
}


def _var_body_to_columns(body: dict) -> dict:
    return {
        col: body[camel]
        for camel, col in _VAR_FIELDS.items()
        if camel in body
    }


def list_variables(db: Session, tipo: str | None) -> list[dict]:
    return [_var_to_dict(v) for v in repo.list_variables(db, tipo)]


def create_variable(db: Session, body: dict) -> dict:
    """Crea una variable INFORMATIVA (las del modelo no se crean aquí)."""
    cols = _var_body_to_columns(body)
    cols["tipo"] = "informativa"  # forzado por contrato
    cols.setdefault("id", _slug(body.get("label", "variable")))
    cols.setdefault("dimension_label", _dim_label(cols.get("dimension", "")))
    return _var_to_dict(repo.create_variable(db, cols))


def update_variable(db: Session, var_id: str, body: dict) -> dict | None:
    v = repo.update_variable(db, var_id, _var_body_to_columns(body))
    return _var_to_dict(v) if v else None


def master_codes(db: Session) -> list[str]:
    """Códigos REALES presentes en `hexagon.variables` (para el combobox del admin:
    vincular cada variable a su columna real de la máster). Muestra sobre 2000 hex
    —las claves son el mismo set por hex— → rápido, sin escanear millones."""
    rows = db.execute(text(
        "SELECT DISTINCT k FROM (SELECT variables FROM hexagon LIMIT 2000) s, "
        "jsonb_object_keys(s.variables) k ORDER BY k"
    ))
    return [r[0] for r in rows]


def update_dataset(db: Session, var_id: str, version: str) -> dict | None:
    v = repo.update_variable(
        db,
        var_id,
        {"dataset_version": version, "dataset_updated_at": date.today().isoformat()},
    )
    return _var_to_dict(v) if v else None


# ── Normas ───────────────────────────────────────────────────────────────────

def _norma_to_dict(n: Norma) -> dict:
    return {"id": n.id, "nombre": n.nombre, "descripcion": n.descripcion, "estado": n.estado}


def list_normas(db: Session) -> list[dict]:
    return [_norma_to_dict(n) for n in repo.list_normas(db)]


def create_norma(db: Session, body: dict) -> dict:
    return _norma_to_dict(repo.create_norma(db, {
        "nombre": body.get("nombre", ""),
        "descripcion": body.get("descripcion", ""),
        "estado": body.get("estado", "verificacion"),
    }))


def update_norma(db: Session, norma_id: str, body: dict) -> dict | None:
    patch = {k: body[k] for k in ("nombre", "descripcion", "estado") if k in body}
    n = repo.update_norma(db, norma_id, patch)
    return _norma_to_dict(n) if n else None


def delete_norma(db: Session, norma_id: str) -> bool:
    return repo.delete_norma(db, norma_id)


# ── Escenarios ───────────────────────────────────────────────────────────────

def _esc_to_dict(e: Escenario) -> dict:
    return {
        "id": e.id, "label": e.label, "description": e.description,
        "horizonte": e.horizonte, "mult": e.mult, "impacto": e.impacto,
    }


def list_escenarios(db: Session) -> list[dict]:
    return [_esc_to_dict(e) for e in repo.list_escenarios(db)]


def create_escenario(db: Session, body: dict) -> dict:
    return _esc_to_dict(repo.create_escenario(db, {
        "label": body.get("label", ""),
        "description": body.get("description", ""),
        "horizonte": int(body.get("horizonte", 10) or 10),
        "mult": float(body.get("mult", 1.03) or 1.03),
        "impacto": body.get("impacto", ""),
    }))


def update_escenario(db: Session, esc_id: str, body: dict) -> dict | None:
    patch: dict = {}
    if "label" in body:
        patch["label"] = body["label"]
    if "description" in body:
        patch["description"] = body["description"]
    if "horizonte" in body:
        patch["horizonte"] = int(body["horizonte"] or 10)
    if "mult" in body:
        patch["mult"] = float(body["mult"] or 1.03)
    if "impacto" in body:
        patch["impacto"] = body["impacto"]
    e = repo.update_escenario(db, esc_id, patch)
    return _esc_to_dict(e) if e else None


def delete_escenario(db: Session, esc_id: str) -> bool:
    return repo.delete_escenario(db, esc_id)


# ── Turbinas (aerogeneradores S/M/L) ─────────────────────────────────────────

def _turbine_to_dict(t) -> dict:
    return {
        "id": t.id,
        "label": t.label,
        "detail": t.detail,
        "potenciaMinMW": t.potencia_min_mw,
        "potenciaMaxMW": t.potencia_max_mw,
        "areaPorAeroKm2": t.area_por_aero_km2,
        "espaciamientoM": t.espaciamiento_m,
    }


_TURBINE_FIELDS = {
    "label": "label",
    "detail": "detail",
    "potenciaMinMW": "potencia_min_mw",
    "potenciaMaxMW": "potencia_max_mw",
    "areaPorAeroKm2": "area_por_aero_km2",
    "espaciamientoM": "espaciamiento_m",
}


def list_turbines(db: Session) -> list[dict]:
    return [_turbine_to_dict(t) for t in repo.list_turbines(db)]


def update_turbine(db: Session, turbine_id: str, body: dict) -> dict | None:
    patch = {col: body[camel] for camel, col in _TURBINE_FIELDS.items() if camel in body}
    t = repo.update_turbine(db, turbine_id, patch)
    return _turbine_to_dict(t) if t else None


# ── util ─────────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "variable"
