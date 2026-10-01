"""
Router de verificación de BD (admin) — feedback #10.

Un solo endpoint que devuelve, para verificar qué está realmente ingerido:
  - las columnas por tabla (information_schema) + conteo estimado de filas,
  - los códigos de variable presentes en los H3 (`hexagon_meta.variable`),
  - los del catálogo InfoVariables (`catalog_variable`),
  - los de la master (`parquet_column`),
y el DIFF entre ellos (qué falta/sobra en los H3 respecto al catálogo/master).

Así el equipo confirma de un vistazo que "todo lo de InfoVariables y los H3 está
siendo ingerido" sin entrar a psql. Solo admin.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from infrastructure.auth.keycloak_guard import CurrentUser, require_admin
from infrastructure.db.session import get_db

router = APIRouter(prefix="/admin/db", tags=["admin-db"])


def _norm(code: str) -> str:
    """Normaliza un código para comparar entre fuentes (ignora - _ y mayúsculas)."""
    return "".join(ch for ch in code if ch.isalnum()).upper()


def _scalars(db: Session, sql: str, **params) -> list:
    try:
        return list(db.execute(text(sql), params).scalars())
    except Exception:  # noqa: BLE001 — tabla ausente / permiso → lista vacía, no 500
        return []


@router.get("/overview")
def db_overview(
    _admin: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    # ── Columnas por tabla + filas estimadas (barato, sin escanear) ──
    tables: dict[str, dict] = {}
    try:
        rows = db.execute(text(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "ORDER BY table_name, ordinal_position"
        ))
        for tname, col, dtype in rows:
            t = tables.setdefault(tname, {"columns": [], "rows_estimate": None})
            t["columns"].append({"name": col, "type": dtype})
    except Exception:  # noqa: BLE001
        pass
    try:
        for tname, est in db.execute(text(
            "SELECT relname, reltuples::bigint FROM pg_class "
            "WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace"
        )):
            if tname in tables:
                tables[tname]["rows_estimate"] = int(est) if est and est > 0 else 0
    except Exception:  # noqa: BLE001
        pass

    # ── Tríada de códigos de variable ──
    h3_vars = _scalars(db, "SELECT variable FROM hexagon_meta ORDER BY variable")
    catalog = _scalars(
        db, "SELECT COALESCE(NULLIF(base_code,''), id) FROM catalog_variable ORDER BY 1"
    )
    master = _scalars(
        db, "SELECT COALESCE(NULLIF(base_code,''), name) FROM parquet_column ORDER BY 1"
    )

    # ── Diffs (normalizados) respecto a lo que hay en los H3 ──
    h3_norm = {_norm(v) for v in h3_vars}
    cat_norm = {_norm(v) for v in catalog}
    faltan_en_h3 = sorted(v for v in catalog if _norm(v) not in h3_norm)
    sin_catalogo = sorted(v for v in h3_vars if _norm(v) not in cat_norm)

    return {
        "tables": {
            name: {"rows_estimate": t["rows_estimate"], "columns": t["columns"]}
            for name, t in sorted(tables.items())
        },
        "variables": {
            "h3": h3_vars,                 # columnas realmente en los hexágonos
            "catalogo_infovariables": catalog,
            "master_parquet": master,
            "en_catalogo_pero_no_en_h3": faltan_en_h3,  # ⚠ no ingeridas en H3
            "en_h3_pero_no_en_catalogo": sin_catalogo,  # ⚠ sin metadatos
        },
        "resumen": {
            "n_tablas": len(tables),
            "n_vars_h3": len(h3_vars),
            "n_vars_catalogo": len(catalog),
            "n_vars_master": len(master),
            "n_faltan_en_h3": len(faltan_en_h3),
            "n_sin_catalogo": len(sin_catalogo),
        },
    }
