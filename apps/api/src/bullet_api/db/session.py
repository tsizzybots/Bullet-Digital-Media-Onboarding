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
#
# IT ALSO BOUNDS TIME SPENT WAITING ON A LOCK (round 13, P1.5). Postgres
# applies `statement_timeout` to the whole statement including the queue wait,
# not just to execution, so a statement that BLOCKS on a lock for more than 5s
# is cancelled with `QueryCanceledError`. Proven by execution: with these exact
# `server_settings`, a second connection blocking on `pg_advisory_xact_lock`
# held by a first raised after 5.02s instead of queueing. Any statement whose
# INTENDED behaviour is to wait longer than this - the dedup advisory lock in
# `worker/ghl_subaccount.py`, held across two 10s-timeout GHL calls - must
# raise the ceiling for itself with `SET LOCAL statement_timeout` and put it
# back with `SET LOCAL statement_timeout = DEFAULT`. Both are transaction-
# scoped, so the engine default below is what every other statement sees, on
# this connection and on every other checkout from the pool.
#
# The value is per-STATEMENT, not per-transaction (verified: three sequential
# 2s statements complete inside one transaction under this 5s ceiling), so a
# transaction that legitimately spans ~20s of external HTTP calls is not
# itself at risk - only a single statement that stalls.
#
# EXPORTED as a named constant so a test can build an engine that provably
# carries the PRODUCTION settings. `test_dedup_lock_production_settings.py`
# reproduces the lock-wait cancellation against these exact values; a test that
# retyped "5000" locally would keep passing after this dict changed, proving
# the lock against a ceiling production no longer has.
ENGINE_SERVER_SETTINGS = {"statement_timeout": "5000"}

engine = create_async_engine(
    get_async_database_url(),
    pool_pre_ping=True,
    future=True,
    # No bind parameters in error messages (round 12, P2): a StatementError's
    # str() otherwise appends `[parameters: {...}]` - for the client upsert
    # that is the full PII bind set - and error strings travel into logs and
    # `platform_actions.last_error`. The SQL text itself still appears; only
    # the values are hidden.
    hide_parameters=True,
    connect_args={
        "ssl": get_settings().database_ssl_mode,
        "server_settings": ENGINE_SERVER_SETTINGS,
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
