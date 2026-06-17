"""Async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bullet_api.config import get_async_database_url, get_settings

# SSL is set here via connect_args, not in the URL query string. Neon's
# canonical URL carries `?sslmode=require&channel_binding=require` which
# asyncpg rejects (libpq-only params). `get_async_database_url()` strips
# those before they reach the engine; TLS is re-enabled below through the
# asyncpg-native `ssl` connect arg, defaulting to "prefer" so the same
# build runs unchanged against local docker Postgres (no TLS) and Neon
# (TLS mandatory and auto-upgraded).
# `statement_timeout` is a server-side ceiling on EVERY statement (5s). Under
# the dashboard's polling load (every open tab x every 5-10s) a slow or stuck
# query must fail fast rather than pile up holding pooled connections. All
# current app statements are sub-second, so 5s is a safety ceiling, not a
# functional limit; a genuinely long operation can raise it per-transaction
# with `SET LOCAL statement_timeout`.
engine = create_async_engine(
    get_async_database_url(),
    pool_pre_ping=True,
    future=True,
    connect_args={
        "ssl": get_settings().database_ssl_mode,
        "server_settings": {"statement_timeout": "5000"},
    },
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a managed `AsyncSession`.

    Rolls back on any exception so a failing handler cannot leak partial
    writes; commits are explicit per use-case.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
