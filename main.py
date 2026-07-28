"""
Punto de entrada del backend Vento (FastAPI).

- Autenticación: verifica el JWT de Keycloak en cada endpoint (guards); NO hay
  login ni registro (los emite Keycloak).
- Mapa: sirve hexágonos por tile y, sobre todo, por ÁREA (polígono → hexágonos
  de esa área con su score general, scores por dimensión y detalle).
- Histórico: persiste los informes por usuario en Postgres.
"""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from shared import config
from infrastructure.db.session import init_db, SessionLocal
from infrastructure.db.hexagon_query_repository import PostgresHexagonSource
from api.routers import historial, tiles

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vento")


def _startup_check() -> None:
    """
    STATELESS: no se carga nada a memoria. Solo verifica la BD e imprime un
    resumen de seguimiento (hexágonos y variables disponibles). Barato.
    """
    db = SessionLocal()
    try:
        src = PostgresHexagonSource(db)
        if not src.is_ready():
            log.warning("Tabla `hexagon` vacía — corre `python -m tools.ingest_parquet`.")
            return
        n_hex  = src.count()
        n_vars = len(src.variable_names())
        log.info("Backend stateless listo: %s hexágonos · %s variables (consulta por request).",
                 f"{n_hex:,}", n_vars)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 1) Tablas de Postgres (dev). En prod: migraciones (Alembic).
    try:
        init_db()
        log.info("DB lista.")
    except Exception as exc:  # la DB puede no estar arriba en dev
        log.warning("No se pudo inicializar la DB: %s", exc)

    # 2) Chequeo de arranque (sin carga masiva a RAM).
    try:
        await run_in_threadpool(_startup_check)
    except Exception as exc:
        log.warning("Chequeo de arranque falló: %s", exc)

    yield  # ── app en ejecución ──


app = FastAPI(title="Vento API", version="2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Todos los endpoints cuelgan de /api (coincide con el rewrite del frontend).
app.include_router(tiles.router, prefix="/api")
app.include_router(historial.router, prefix="/api")


@app.get("/api/health", tags=["health"])
def health() -> dict:
    db = SessionLocal()
    try:
        ready = PostgresHexagonSource(db).is_ready()
    except Exception:
        ready = False
    finally:
        db.close()
    return {"status": "ok", "index_ready": ready}
