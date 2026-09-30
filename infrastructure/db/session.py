"""Sesión SQLAlchemy hacia Postgres + dependencia FastAPI `get_db`."""
from __future__ import annotations
import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from shared import config


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Pool de conexiones EXPLÍCITO (tuneable por env, sin redeploy). El default de
# SQLAlchemy (5 + 10 = 15) se agotaba bajo la ráfaga de tiles del mapa: cada
# request toma una conexión y el streaming la retiene durante todo el barrido, así
# que con >15 concurrentes las demás esperaban y reventaban con QueuePool
# TimeoutError. Se sube el margen y se fija `pool_timeout`/`recycle`. OJO en un
# server chico: pool_size+max_overflow por worker × nº workers ≤ max_connections
# de Postgres (default 100) y cabe en RAM (cada conexión PG ~5–10 MB).
engine = create_engine(
    config.DATABASE_URL,
    pool_pre_ping=True,                              # descarta conexiones muertas
    pool_size=_int_env("DB_POOL_SIZE", 5),           # persistentes por worker
    max_overflow=_int_env("DB_MAX_OVERFLOW", 15),    # extra en ráfaga (→ 20 máx)
    pool_timeout=_int_env("DB_POOL_TIMEOUT", 30),    # s esperando una conexión
    pool_recycle=_int_env("DB_POOL_RECYCLE", 1800),  # recicla a los 30 min
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)

Base = declarative_base()


def get_db() -> Iterator[Session]:
    """Dependencia de FastAPI: abre y cierra una sesión por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crea las tablas si no existen (dev). En prod usar migraciones (Alembic)."""
    from infrastructure.db import models  # noqa: F401  (registra los modelos)

    Base.metadata.create_all(bind=engine)
