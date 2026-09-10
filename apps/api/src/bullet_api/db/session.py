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

# ----- POOL SIZES (S1-26b/c round 16) -----
#
# TWO POOLS, and the reason is one fan-out. `create_ghl_subaccount`'s phase 2
# holds a pooled connection across two GHL calls, because the hold IS the
# transaction-scoped advisory lock doing the serialising: committing to release
# the connection would release the lock, reopening round 12's P1.4 cross-bucket
# race. Every other worker either commits before its slow call
# (`client_record`, `sales_summary`, `signed_pdf`) or holds only across a short
# Inngest emit. So this is the one place where external latency converts
# directly into connections held for tens of seconds.
#
# The arithmetic, in full in `docs/s1-26bc-round16-pool-starvation-spec.md`:
# `Throttle(limit=5, period=10s)` admits 0.5 run starts/second sustained; a
# phase-2 hold is 20s uncontended (two 10s GHL calls) and up to 50s behind the
# lock's own 30s `SET LOCAL` ceiling. Little's law then puts 10 connections in
# use at a 20s hold, 15 at 30s and 25 at 50s. The old single pool held 15 - not
# by decision, but because `create_async_engine` was called with no pool
# arguments and 15 is what SQLAlchemy defaults to. A sustained 30-second GHL
# response therefore consumed the whole pool, after which every dashboard
# request queued for `pool_timeout` and 500ed while `/healthz`, which touches no
# database, kept Render's health check green.
#
# Sizes are NAMED so the Neon ceiling reading adjusts one place, and so a test
# can assert they were chosen rather than inherited. The worker gets the larger
# capacity deliberately: exceeding it must degrade the FAN-OUT (a run waits,
# fails visibly through `_record_failure`, and Inngest retries it) rather than
# the dashboard, which is the failure being fixed.
#
# The api timeout drops from SQLAlchemy's default 30s to 10s. Nothing on the
# dashboard's read path is worth holding a request for half a minute; failing
# fast is the better answer for a surface a human is watching.
API_POOL_SIZE = 5
API_MAX_OVERFLOW = 10
API_POOL_TIMEOUT = 10

WORKER_POOL_SIZE = 5
WORKER_MAX_OVERFLOW = 15
WORKER_POOL_TIMEOUT = 30

engine = create_async_engine(
    get_async_database_url(),
    pool_pre_ping=True,
    pool_size=API_POOL_SIZE,
    max_overflow=API_MAX_OVERFLOW,
    pool_timeout=API_POOL_TIMEOUT,
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

# THE SAME `connect_args`, deliberately and not incidentally (round 16, spec
# do-not-touch item 2). The dedup lock raises its own ceiling with `SET LOCAL
# statement_timeout = '30s'` and restores the LITERAL '5s' rather than
# `= DEFAULT`, because round 15 measured that `= DEFAULT` puts back the startup
# value - "0" on Neon - handing the rest of that transaction an unbounded
# budget. If these two engines connected with different `server_settings`, that
# restore would silently mean one thing on a worker connection and another on an
# api one, on the endpoint production actually uses. They are built from the
# same URL and the same args for that reason; only the POOL differs.
worker_engine = create_async_engine(
    get_async_database_url(),
    pool_pre_ping=True,
    pool_size=WORKER_POOL_SIZE,
    max_overflow=WORKER_MAX_OVERFLOW,
    pool_timeout=WORKER_POOL_TIMEOUT,
    future=True,
    hide_parameters=True,
    connect_args={
        "ssl": get_settings().database_ssl_mode,
        "server_settings": ENGINE_SERVER_SETTINGS,
    },
)

# Every Inngest fan-out uses THIS, never `AsyncSessionLocal`. A new fan-out that
# copies an older one and keeps the api sessionmaker would silently drop out of
# the split with nothing failing, so `test_worker_pool_isolation.py` asserts at
# the source level that no module under `worker/` mentions `AsyncSessionLocal`.
WorkerSessionLocal = async_sessionmaker(
    worker_engine,
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
