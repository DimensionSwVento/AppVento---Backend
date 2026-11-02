"""
Fixtures de integración: PostgreSQL real (vía Docker) para los tests marcados
`@pytest.mark.postgres`.

Arranca la BD con:  docker compose up -d db
La URL por defecto apunta al servicio `db` del docker-compose (localhost:5432).
En CI se sobreescribe con `TEST_DATABASE_URL` apuntando al contenedor de servicio.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from infrastructure.db.session import Base
from infrastructure.db import models  # noqa: F401  (registra los modelos en Base)
from shared import config as app_config

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://vento:vento@localhost:5432/vento",
)


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(TEST_DATABASE_URL, future=True, pool_pre_ping=True)
    # Espera hasta ~15s a que Postgres arranque (docker compose up -d db / service de CI).
    for _ in range(15):
        try:
            with engine.connect():
                break
        except OperationalError:
            time.sleep(1)
    else:
        pytest.skip(
            "PostgreSQL no disponible. Arranca `docker compose up -d db` "
            f"antes de correr los tests de integración ({TEST_DATABASE_URL})."
        )
    _reset_schema(engine)
    _migrate(TEST_DATABASE_URL)
    yield engine
    _reset_schema(engine)
    engine.dispose()


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _reset_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def _migrate(url: str) -> None:
    """Esquema de test = el de PRODUCCIÓN (`alembic upgrade head`, igual que
    docker-entrypoint.sh), no `create_all` del metadata ORM: las migraciones fijan
    `server_default` (p.ej. `hexagon.scores`, 0008) en los que se apoya el COPY de
    `copy_ingest`, y el PK BIGINT de `h3_index` (0016)."""
    cfg = Config()  # sin alembic.ini → no reconfigura el logging de pytest
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    previous = app_config.DATABASE_URL
    app_config.DATABASE_URL = url  # migrations/env.py lee la URL de shared.config
    try:
        command.upgrade(cfg, "head")
    finally:
        app_config.DATABASE_URL = previous


@pytest.fixture(autouse=True)
def _clean_tables(db_engine):
    """Limpia todas las tablas antes de cada test (aislamiento entre tests)."""
    with db_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):  # tablas del modelo ORM
            conn.execute(table.delete())
    yield


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def session_factory(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
