"""Entorno de Alembic para migraciones de Supply-Shield."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importar metadatos de los modelos para autogeneración
from supply_shield.config import Settings
from supply_shield.db.models import Base

# Configuración de logging de Alembic
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """URL de la base, tomada de la misma configuración que usa el servicio.

    Lee las variables ``SUPPLY_SHIELD_*`` y el ``.env``, igual que la app. Si leyera
    solo el entorno, un ``.env`` con otra ``SUPPLY_SHIELD_DATABASE_URL`` haría que
    las migraciones se aplicaran a una base distinta de la que usa el servicio.
    """
    return Settings().database_url


def run_migrations_offline() -> None:
    """Ejecuta migraciones en modo offline (sin conexión a DB)."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Ejecuta migraciones usando una conexión dada."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Ejecuta migraciones en modo online (con conexión a DB)."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
