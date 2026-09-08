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
# `statement_timeout` is INTENDED as a server-side ceiling on every statement
# (5s), so that under the dashboard's polling load (every open tab x every
# 5-10s) a slow or stuck query fails fast rather than piling up holding pooled
# connections.
#
# IT DOES NOT REACH NEON, AND NEVER HAS - measured 07/09/2026, see the
# platform-discovery CHANGELOG entry of that date. `server_settings` is sent as
# a startup parameter and Neon's proxy discards it: `SHOW statement_timeout`
# returns "0" on BOTH the pooled endpoint the app runtime uses and the direct
# endpoint Alembic uses. So the ceiling described above applies only against a
# local Docker Postgres, which is why it has looked correct in every round's
# local verification since this engine was written. Treat the protection as
# ABSENT in any deployed environment until that is fixed on its own card: the
# fix is not a config edit, because enabling a real ceiling for the first time
# would cancel every statement that legitimately runs longer, starting with the
# dedup advisory lock below.
#
# A genuinely long operation can still raise a ceiling per-transaction with
# `SET LOCAL statement_timeout`, which Neon DOES honour (measured on both
# endpoints the same day).
#
# WHERE THE CEILING DOES APPLY, IT ALSO BOUNDS TIME SPENT WAITING ON A LOCK
# (round 13, P1.5). Postgres applies `statement_timeout` to the whole statement
# including the queue wait, not just to execution, so a statement that BLOCKS
# on a lock for more than 5s is cancelled with `QueryCanceledError`. Proven by
# execution against local Docker: a second connection blocking on
# `pg_advisory_xact_lock` held by a first raised after 5.02s instead of
# queueing. On Neon it does NOT, per the correction above, so the production
# exposure was the opposite one - an unbounded wait with nothing to cancel it.
# Either way the dedup advisory lock in `worker/ghl_subaccount.py`, held across
# two 10s-timeout GHL calls, sets its own 30s ceiling with `SET LOCAL
# statement_timeout`: on Docker that stops it being cancelled, on Neon it
# imposes the only bound that exists. It then puts back the LITERAL '5s'
# rather than `= DEFAULT` (round 15) - `= DEFAULT` restores the startup-packet
# value, which is "0" on Neon, so the reset was handing the rest of that
# transaction an unbounded budget on the endpoint production actually uses.
# Both are transaction-scoped, so the engine default below is what every other
# statement sees.
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
