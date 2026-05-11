"""Alembic environment - async migrations against SQLAlchemy 2.x + asyncpg.

`DATABASE_URL` is the single source of truth for which database we point at.
Locally it comes from `.env` (matches docker-compose). On Render staging /
production it comes from the Render env group. In CI on a per-PR run
(S1-33) it will be overridden to a per-PR Neon branch URL - no code change
here is needed for that to work.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from bullet_api.config import get_async_database_url
from bullet_api.db.base import Base

# Alembic Config object - reads alembic.ini.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the async-flavoured URL at runtime so alembic.ini does not have to
# carry a value (and contributors never paste credentials into a tracked file).
config.set_main_option("sqlalchemy.url", get_async_database_url())

# Metadata target for `alembic revision --autogenerate`. No models exist yet
# (S1-06+ adds them); having Base.metadata wired now means future autogenerate
# runs work without further env.py changes.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations through an `AsyncEngine` and a sync-bridged connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
