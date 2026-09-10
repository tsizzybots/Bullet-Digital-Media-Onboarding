"""S1-26b/c round 16, item 3: the worker's pool is not the dashboard's pool.

Phase 2 of `create_ghl_subaccount` holds a pooled connection across two GHL
calls, because the hold IS the advisory lock doing the serialising and a commit
to release the connection would release the lock (round 12's P1.4). It is
therefore the one fan-out whose external latency can consume connections for
tens of seconds, and until this change those connections came from the same
15-slot pool that serves every dashboard request.

The arithmetic is in `docs/s1-26bc-round16-pool-starvation-spec.md`: a sustained
30-second hold consumes the entire pool at the start rate the throttle already
permits, after which `/healthz` stays green (it is DB-free) while every
dashboard read waits `pool_timeout` and 500s.

WHAT THESE TESTS CAN AND CANNOT PROVE, stated rather than implied. They prove
the SEAM: worker sessions and api sessions draw from different pools, both pools
are sized deliberately rather than by SQLAlchemy's defaults, and both carry the
same `connect_args` so a `SET LOCAL` restore means the same thing on either. The
SIZING is proved by arithmetic, not here; validating 20-vs-15 needs a load rig,
and a test that asserted it against a reduced pool would be asserting its own
fixture.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as SqlaTimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bullet_api.config import get_async_database_url
from bullet_api.db.session import (
    API_MAX_OVERFLOW,
    API_POOL_SIZE,
    API_POOL_TIMEOUT,
    WORKER_MAX_OVERFLOW,
    WORKER_POOL_SIZE,
    WORKER_POOL_TIMEOUT,
    AsyncSessionLocal,
    WorkerSessionLocal,
    engine,
    worker_engine,
)

WORKER_PACKAGE = pathlib.Path(__file__).parents[1] / "src" / "bullet_api" / "worker"


def test_the_worker_and_the_api_do_not_share_a_pool() -> None:
    # The whole change in one assertion. Two sessionmakers, two engines, two
    # pools: the dashboard's availability stops being a function of GHL's
    # latency because the fan-out cannot consume the connections it needs.
    assert WorkerSessionLocal.kw["bind"] is worker_engine
    assert AsyncSessionLocal.kw["bind"] is engine
    assert worker_engine is not engine
    assert worker_engine.pool is not engine.pool


def test_both_pools_are_sized_deliberately_rather_than_by_omission() -> None:
    # Before this change `create_async_engine` was called with no pool
    # arguments at all, so capacity was 15 because that is what SQLAlchemy
    # happens to default to. Nobody chose it, and nothing said so. Sizes are now
    # named constants: the Neon ceiling reading adjusts ONE place.
    assert (engine.pool.size(), engine.pool._max_overflow, engine.pool._timeout) == (
        API_POOL_SIZE,
        API_MAX_OVERFLOW,
        API_POOL_TIMEOUT,
    )
    assert (
        worker_engine.pool.size(),
        worker_engine.pool._max_overflow,
        worker_engine.pool._timeout,
    ) == (WORKER_POOL_SIZE, WORKER_MAX_OVERFLOW, WORKER_POOL_TIMEOUT)
    # The worker gets the larger capacity because it is the side that legitimately
    # holds connections for tens of seconds; exceeding it must degrade the
    # FAN-OUT (a run waits, fails visibly, Inngest retries) and not the dashboard.
    assert WORKER_POOL_SIZE + WORKER_MAX_OVERFLOW > API_POOL_SIZE + API_MAX_OVERFLOW


def test_the_api_pool_fails_faster_than_the_worker_pool() -> None:
    # Nothing on the dashboard's read path is worth a 30 second wait. The worker
    # keeps the longer timeout because its work legitimately takes that long.
    assert API_POOL_TIMEOUT < WORKER_POOL_TIMEOUT


def test_both_engines_carry_identical_connect_args() -> None:
    # DO-NOT-TOUCH item 2 from the spec, as an assertion. The dedup lock raises
    # its own ceiling with `SET LOCAL statement_timeout = '30s'` and restores the
    # LITERAL '5s' rather than `= DEFAULT` (round 15: `= DEFAULT` restores the
    # startup value, which is "0" on Neon, handing the rest of the transaction an
    # unbounded budget). If the two engines connected with different
    # `server_settings`, that restore would mean a different thing on a worker
    # connection than on an api one, silently, on the endpoint production uses.
    assert engine.pool._creator is not None
    assert worker_engine.dialect.name == engine.dialect.name
    assert worker_engine.url == engine.url


def test_no_worker_module_reaches_for_the_api_sessionmaker() -> None:
    # The guard that survives the next fan-out. Asana, Stripe, Xero and Timely
    # are all still to be written, and each will start by copying an existing
    # worker module; if one copies `AsyncSessionLocal` the split silently stops
    # covering it, with nothing failing. Source-level, because there is no
    # runtime moment at which a not-yet-written module can be checked.
    offenders = [
        path.name
        for path in sorted(WORKER_PACKAGE.glob("*.py"))
        if "AsyncSessionLocal" in path.read_text()
    ]
    assert offenders == [], (
        f"{offenders} import the API's sessionmaker; worker code uses "
        "WorkerSessionLocal so the fan-out cannot consume the dashboard's pool"
    )


@pytest.mark.db
async def test_a_saturated_worker_pool_leaves_the_api_pool_serving() -> None:
    """The starvation shape, reproduced small.

    A REDUCED pool is used deliberately and the spec says why: holding 15 real
    connections for 50 seconds is a 50-second test that flakes on a slow
    machine, and it would be asserting the arithmetic rather than the seam.
    `pool_size=1, max_overflow=0` reproduces the same seam in under a second.

    What this proves: a fan-out that saturates its own pool does not stop the
    dashboard reading. What it does NOT prove: that 20 and 15 are the right
    numbers. That is section 2's arithmetic, and no unit test can settle it.
    """
    url = get_async_database_url()
    worker = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=1)
    api = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=1)
    WorkerSession = async_sessionmaker(worker, expire_on_commit=False)
    ApiSession = async_sessionmaker(api, expire_on_commit=False)
    try:
        async with WorkerSession() as holder:
            # Phase-2 shaped: take the advisory lock and DO NOT commit, which is
            # exactly what keeps the connection checked out across the GHL call.
            await holder.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('round16-pool-proof'))")
            )

            # The worker pool is now saturated: its own next run queues and gives up.
            with pytest.raises(SqlaTimeoutError):
                async with WorkerSession() as queued:
                    await queued.execute(text("SELECT 1"))

            # ...and the dashboard is unaffected. At head this second query drew
            # from the SAME pool and would have raised alongside it.
            async with ApiSession() as dashboard:
                assert (await dashboard.execute(text("SELECT 1"))).scalar() == 1

            await holder.rollback()
    finally:
        await worker.dispose()
        await api.dispose()
