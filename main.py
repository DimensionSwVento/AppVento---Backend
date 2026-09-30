"""
Punto de entrada del backend Vento (FastAPI).

- Autenticación: verifica el JWT de Keycloak en cada endpoint (guards); NO hay
  login ni registro (los emite Keycloak).
- Mapa: sirve hexágonos por tile y, sobre todo, por ÁREA (polígono → hexágonos
  de esa área con su score general, scores por dimensión y detalle).
- Histórico: persiste los informes por usuario en Postgres.
"""
from __future__ import annotations
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from shared import config
from infrastructure.db.session import init_db, SessionLocal
from infrastructure.db.hexagon_query_repository import PostgresHexagonSource
from infrastructure.db.catalog_seed import seed_catalog
from application.services import master_service, catalog_ingest
from api.routers import admin_catalog, admin_master, admin_bundle, historial, tiles

# Cada cuánto revisa el scheduler si hay una actualización de master programada.
_MASTER_TICK_SECONDS = 60

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
    # 0) Limita la concurrencia de endpoints sync (threadpool de Starlette, 40 por
    #    defecto) para que NUNCA supere el pool de conexiones a la BD. Así, bajo la
    #    ráfaga de tiles del mapa, las peticiones de más hacen COLA en el threadpool
    #    en vez de reventar con `QueuePool ... TimeoutError`. Debe quedar ≤
    #    (DB_POOL_SIZE + DB_MAX_OVERFLOW); por defecto 18 < 20.
    try:
        limit = int(os.getenv("APP_THREAD_LIMIT", "18"))
        to_thread.current_default_thread_limiter().total_tokens = limit
        log.info("Threadpool limitado a %s hilos concurrentes (≤ pool BD).", limit)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo ajustar el límite de threadpool: %s", exc)

    # 1) Tablas de Postgres (dev). En prod: migraciones (Alembic).
    try:
        init_db()
        log.info("DB lista.")
        # Semilla del catálogo admin (normas/escenarios) — idempotente.
        db = SessionLocal()
        try:
            seed_catalog(db)
            # Persiste dónde se guarda cada fuente (master/CSV/JSON) + su estado.
            catalog_ingest.sync_sources(db)
        finally:
            db.close()
    except Exception as exc:  # la DB puede no estar arriba en dev
        log.warning("No se pudo inicializar la DB: %s", exc)

    # 2) Chequeo de arranque (sin carga masiva a RAM).
    try:
        await run_in_threadpool(_startup_check)
    except Exception as exc:
        log.warning("Chequeo de arranque falló: %s", exc)

    # 3) Scheduler de master: tick de fondo que ejecuta las actualizaciones
    #    programadas cuando llega su hora (no hay scheduler externo).
    scheduler_task = asyncio.create_task(_master_scheduler_loop())

    yield  # ── app en ejecución ──

    scheduler_task.cancel()


async def _master_scheduler_loop() -> None:
    while True:
        try:
            db = SessionLocal()
            try:
                await run_in_threadpool(master_service.run_due, db)
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("Tick del scheduler de master falló: %s", exc)
        try:
            await asyncio.sleep(_MASTER_TICK_SECONDS)
        except asyncio.CancelledError:
            break


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
app.include_router(admin_catalog.router, prefix="/api")
app.include_router(admin_master.router, prefix="/api")
app.include_router(admin_bundle.router, prefix="/api")


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
