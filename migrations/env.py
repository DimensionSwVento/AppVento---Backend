"""Entorno de Alembic. Usa la config y los modelos de la app como fuente de verdad."""
from __future__ import annotations
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

from shared import config as app_config
from infrastructure.db.session import Base
from infrastructure.db import models  # noqa: F401  (registra los modelos en el metadata)

# Objeto de config de Alembic (lee alembic.ini).
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL de la BD desde la config de la app (no desde el .ini).
config.set_main_option("sqlalchemy.url", app_config.DATABASE_URL)

# Metadata objetivo para autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=app_config.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
