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
import threading
import time
import uuid
from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import FastAPI, HTTPException, Request
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

# ── Traza de peticiones (observabilidad anti-saturación) ──────────────────────
# Cuando el server se "cuelga vivo" (workers activos pero sin responder), esto
# muestra en consola QUÉ peticiones tienen tomados los hilos y cuáles se apilan.
#   TRACE_REQUESTS=1  → loguea CADA request (→ entra / ← sale con duración).
#   INFLIGHT_WARN=N   → si hay ≥N en vuelo a la vez, WARNING (detecta apilamiento).
#   SLOW_REQUEST_MS=N → loguea las que tarden más de N ms aunque TRACE esté off.
# El endpoint GET /api/_debug/inflight (si DEBUG_INFLIGHT=1) lista las que están
# procesándose AHORA (curl en pleno "ataque" → ves qué está atascado).
_TRACE_REQUESTS = os.getenv("TRACE_REQUESTS", "0") == "1"
_INFLIGHT_WARN = int(os.getenv("INFLIGHT_WARN", "12"))
_SLOW_REQUEST_MS = float(os.getenv("SLOW_REQUEST_MS", "3000"))
_inflight: dict[str, tuple[str, str, float]] = {}  # rid → (método, path, t0)
_inflight_lock = threading.Lock()


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


@app.middleware("http")
async def _trace_requests(request: Request, call_next):
    """Traza peticiones para diagnosticar saturación: cuenta las EN VUELO, avisa
    cuando se apilan y loguea las lentas. Ver flags TRACE_REQUESTS/INFLIGHT_WARN/
    SLOW_REQUEST_MS arriba."""
    rid = uuid.uuid4().hex[:8]
    method, path = request.method, request.url.path
    t0 = time.monotonic()
    with _inflight_lock:
        _inflight[rid] = (method, path, t0)
        n = len(_inflight)
    if _TRACE_REQUESTS:
        log.info("→ [%s] %s %s (en-vuelo=%d)", rid, method, path, n)
    elif n >= _INFLIGHT_WARN:
        # Apilamiento: los hilos se están llenando → probable cuelgue inminente.
        log.warning("PETICIONES EN VUELO ALTAS=%d (última %s %s)", n, method, path)
    status = 500
    try:
        resp = await call_next(request)
        status = resp.status_code
        return resp
    finally:
        with _inflight_lock:
            _inflight.pop(rid, None)
            n2 = len(_inflight)
        dur_ms = (time.monotonic() - t0) * 1000.0
        if _TRACE_REQUESTS or dur_ms > _SLOW_REQUEST_MS:
            log.info("← [%s] %s %s %s %.0fms (en-vuelo=%d)", rid, method, path, status, dur_ms, n2)


@app.get("/api/_debug/inflight", include_in_schema=False)
def debug_inflight():
    """Peticiones que se están PROCESANDO ahora mismo (curl en pleno cuelgue → ves
    qué endpoint tiene tomados los hilos). Gate: DEBUG_INFLIGHT=1 (si no, 404)."""
    if os.getenv("DEBUG_INFLIGHT", "0") != "1":
        raise HTTPException(status_code=404, detail="disabled")
    now = time.monotonic()
    with _inflight_lock:
        items = [
            {"id": rid, "method": m, "path": p, "age_ms": round((now - t0) * 1000)}
            for rid, (m, p, t0) in _inflight.items()
        ]
    items.sort(key=lambda x: -x["age_ms"])
    return {"count": len(items), "requests": items}


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
