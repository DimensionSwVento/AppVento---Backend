"""Sesión SQLAlchemy hacia Postgres + dependencia FastAPI `get_db`."""
from __future__ import annotations
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from shared import config

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, future=True)
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
