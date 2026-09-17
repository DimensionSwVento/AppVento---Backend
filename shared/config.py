"""
Configuración central del backend, dirigida por variables de entorno.
Carga un `.env` si existe (dev). Expone constantes usadas por infra/app/api.
"""
from __future__ import annotations
import os

try:  # carga .env en dev; en prod las env vienen del entorno/contenedor
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _list(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [v.strip() for v in value.split(",") if v.strip()]


_HERE = os.path.dirname(os.path.abspath(__file__))

# ── Datos ────────────────────────────────────────────────────────────────────
# El parquet maestro va en `back_elements/data/`. Sobrescribible con PARQUET_PATH.
PARQUET_PATH: str = os.getenv(
    "PARQUET_PATH",
    os.path.normpath(os.path.join(_HERE, "..", "data", "master_variables.h3.parquet")),
)

# ── Autenticación (Keycloak) ─────────────────────────────────────────────────
# No hay login/registro: Keycloak emite los tokens; aquí solo se verifican.
# La autenticación NO es opcional: todos los endpoints de datos exigen identidad.
KEYCLOAK_ISSUER: str = os.getenv("KEYCLOAK_ISSUER", "http://localhost:8080/realms/vento")
KEYCLOAK_JWKS_URL: str = os.getenv(
    "KEYCLOAK_JWKS_URL", f"{KEYCLOAK_ISSUER}/protocol/openid-connect/certs"
)
KEYCLOAK_AUDIENCE: str = os.getenv("KEYCLOAK_AUDIENCE", "account")
JWT_ALGORITHMS: list[str] = _list(os.getenv("JWT_ALGORITHMS"), ["RS256"])

# Secreto compartido para peticiones internas del BFF (Next → FastAPI). El backend
# es privado en AccuWeb; el servidor Next ya validó la sesión de Keycloak y reenvía
# la identidad (sub + roles) con este secreto. Vacío = modo interno deshabilitado.
INTERNAL_API_SECRET: str = os.getenv("INTERNAL_API_SECRET", "")

# ── Base de datos (Postgres) ─────────────────────────────────────────────────
DATABASE_URL: str = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://vento:vento@localhost:5432/vento"
)

# ── CORS ─────────────────────────────────────────────────────────────────────
CORS_ORIGINS: list[str] = _list(os.getenv("CORS_ORIGINS"), ["http://localhost:3000"])

# ── Histórico ────────────────────────────────────────────────────────────────
MAX_HISTORIAL_PER_USER: int = int(os.getenv("MAX_HISTORIAL_PER_USER", "50"))

# ── Ingesta de hexágonos ──────────────────────────────────────────────────────
# "copy" (por defecto): COPY de Postgres, rápido y atómico. "orm": fallback
# portable por lotes (para entornos sin COPY, p.ej. tests).
HEXAGON_INGEST: str = os.getenv("HEXAGON_INGEST", "copy")

# ── Mar/tierra (offshore) ─────────────────────────────────────────────────────
# Código de la variable/columna de la master que marca mar(1)/tierra(0) por hex.
# El nombre real está por definir (memoria `datos-requeridos §1.4`); vacío = el
# tile no expone `off` hasta que se configure.
OFFSHORE_VAR: str = os.getenv("OFFSHORE_VAR", "")
