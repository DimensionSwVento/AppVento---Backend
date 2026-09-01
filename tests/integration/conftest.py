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

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from infrastructure.db.session import Base
from infrastructure.db import models  # noqa: F401  (registra los modelos en Base)

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
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(db_engine):
    """Limpia todas las tablas antes de cada test (aislamiento entre tests)."""
    with db_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
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
