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
from application.services import tile_service
from infrastructure.db.session import init_db
from api.routers import historial, tiles

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vento")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 1) Tablas de Postgres (dev). En prod: migraciones (Alembic).
    try:
        init_db()
        log.info("DB lista.")
    except Exception as exc:  # la DB puede no estar arriba en dev
        log.warning("No se pudo inicializar la DB: %s", exc)

    # 2) Warmup del índice espacial desde el parquet.
    try:
        result = await run_in_threadpool(tile_service.warmup)
        log.info("Índice cargado: %s hexágonos.", result.get("count"))
    except FileNotFoundError:
        log.warning(
            "Parquet no encontrado (%s) — el índice queda vacío hasta llamar /api/tiles/warmup.",
            config.PARQUET_PATH,
        )
    except Exception as exc:
        log.warning("Warmup falló: %s", exc)

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
    return {"status": "ok", "index_ready": tile_service.index_ready()}
