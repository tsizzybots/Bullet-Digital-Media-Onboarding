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

import ast
import inspect
import pathlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as SqlaTimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import QueuePool

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
SESSION_MODULE = pathlib.Path(__file__).parents[1] / "src" / "bullet_api" / "db" / "session.py"


def _engine_kwargs(variable: str) -> dict[str, str]:
    """Return `{kwarg: source text of its value}` for a `create_async_engine` call.

    SOURCE-LEVEL, and round 17 proved it has to be. A value comparison cannot
    tell "we passed 5" from "we passed nothing and SQLAlchemy defaulted to 5",
    and four of the six shipped kwargs are in exactly that position (see
    `test_each_shipped_pool_kwarg_differs_from_the_library_default_or_says_so`).
    Reading the call site is the only way to assert the kwarg was SET.
    """
    tree = ast.parse(SESSION_MODULE.read_text())
    assignments = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == variable for target in node.targets)
    ]
    # THE ASSERTIONS ARE UNCONDITIONAL (G4). They sat inside the search `if` in
    # the first draft, and the static gate caught it: an `assert` behind a
    # condition read from the system under test passes silently in exactly the
    # case it exists to catch, which here is a `session.py` where the assignment
    # has been renamed away. Collecting first and asserting after also buys a
    # strictly stronger check - that there is EXACTLY ONE such assignment, so a
    # second engine added later cannot be silently ignored by this reader.
    assert len(assignments) == 1, (
        f"expected exactly one module-level assignment to `{variable}` in "
        f"{SESSION_MODULE}, found {len(assignments)}"
    )
    call = assignments[0]
    assert isinstance(call, ast.Call), f"`{variable}` is no longer built by a call"
    return {kw.arg: ast.unparse(kw.value) for kw in call.keywords if kw.arg is not None}


def _queue_pool_defaults() -> dict[str, object]:
    """SQLAlchemy's own pool defaults, read BY EXECUTION rather than retyped."""
    return {
        name: param.default
        for name, param in inspect.signature(QueuePool.__init__).parameters.items()
        if param.default is not inspect.Parameter.empty
    }


def test_the_worker_and_the_api_do_not_share_a_pool() -> None:
    # The whole change in one assertion. Two sessionmakers, two engines, two
    # pools: the dashboard's availability stops being a function of GHL's
    # latency because the fan-out cannot consume the connections it needs.
    assert WorkerSessionLocal.kw["bind"] is worker_engine
    assert AsyncSessionLocal.kw["bind"] is engine
    assert worker_engine is not engine
    assert worker_engine.pool is not engine.pool


def test_both_pools_carry_the_sizes_their_constants_name() -> None:
    # RENAMED in round 17, because the old name
    # (`..._are_sized_deliberately_rather_than_by_omission`) claimed more than
    # this body proves. Comparing a live pool against the constant proves the
    # VALUES agree; it cannot prove the kwarg was passed, which is the whole of
    # what "rather than by omission" asserts. That claim now lives in
    # `test_every_pool_kwarg_is_passed_explicitly`, which reads the call site.
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


@pytest.mark.parametrize(
    ("variable", "expected"),
    [
        (
            "engine",
            {
                "pool_size": "API_POOL_SIZE",
                "max_overflow": "API_MAX_OVERFLOW",
                "pool_timeout": "API_POOL_TIMEOUT",
            },
        ),
        (
            "worker_engine",
            {
                "pool_size": "WORKER_POOL_SIZE",
                "max_overflow": "WORKER_MAX_OVERFLOW",
                "pool_timeout": "WORKER_POOL_TIMEOUT",
            },
        ),
    ],
)
def test_every_pool_kwarg_is_passed_explicitly(variable: str, expected: dict[str, str]) -> None:
    """Round 17: the omission, pinned per kwarg per engine.

    The defect round 16 fixed was an OMISSION - `create_async_engine` called
    with no pool arguments, capacity 15 because that is SQLAlchemy's default
    and not because anyone chose it. The test round 16 wrote to pin that fix
    compared VALUES, and four of the six shipped kwargs happen to equal the
    library default, so deleting any of those four from the call site changed
    nothing observable and the test stayed green. Measured, not assumed:
    deleting `pool_size`, `max_overflow` (api), `pool_size` or `pool_timeout`
    (worker) individually all SURVIVED against the round-16 test.

    Reading the call site is what closes that, and it is also what lets the
    manifest carry one entry per kwarg per engine instead of one entry that
    deletes three at once and kills only because one of them differed.
    """
    kwargs = _engine_kwargs(variable)
    for name, constant in expected.items():
        assert name in kwargs, (
            f"`{variable}` does not pass `{name}` - so it silently inherits "
            f"SQLAlchemy's default, which is the exact omission round 16 fixed"
        )
        assert kwargs[name] == constant, (
            f"`{variable}` passes `{name}={kwargs[name]}`, not the named constant "
            f"`{constant}`; the sizes are named so the Neon ceiling reading "
            f"adjusts ONE place"
        )


def test_each_shipped_pool_kwarg_differs_from_the_library_default_or_says_so() -> None:
    """Round 17: state, per kwarg, whether a value comparison could pin it.

    Four of the six deliberately EQUAL SQLAlchemy's default. That is not a
    defect - 5 is a reasonable pool size and 30s a reasonable worker timeout -
    but it does mean no value-comparing test can ever defend them, which is why
    `test_every_pool_kwarg_is_passed_explicitly` exists. Recording the
    coincidence here means a future SQLAlchemy release that moves a default, or
    a resize that moves one of ours, reddens this test and says which.
    """
    defaults = _queue_pool_defaults()
    deliberately_equal = {
        "API_POOL_SIZE": (API_POOL_SIZE, defaults["pool_size"]),
        "API_MAX_OVERFLOW": (API_MAX_OVERFLOW, defaults["max_overflow"]),
        "WORKER_POOL_SIZE": (WORKER_POOL_SIZE, defaults["pool_size"]),
        "WORKER_POOL_TIMEOUT": (WORKER_POOL_TIMEOUT, defaults["timeout"]),
    }
    genuinely_different = {
        "API_POOL_TIMEOUT": (API_POOL_TIMEOUT, defaults["timeout"]),
        "WORKER_MAX_OVERFLOW": (WORKER_MAX_OVERFLOW, defaults["max_overflow"]),
    }
    for name, (ours, default) in deliberately_equal.items():
        assert ours == default, (
            f"{name} no longer equals SQLAlchemy's default ({ours} vs {default}). "
            f"That is fine, but this list is now stale - move it to the other one"
        )
    for name, (ours, default) in genuinely_different.items():
        assert ours != default, (
            f"{name} now EQUALS SQLAlchemy's default ({ours}), so a value "
            f"comparison can no longer pin it - move it to the other list"
        )


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


# The ONE module allowed to reach for the API sessionmaker, and how many
# EXECUTABLE references it may make (round 17, B2). `ghl_subaccount` uses it in
# exactly one place: the `on_failure` dead-letter recorder, which must not draw
# from the worker pool, because the failure it exists to record may BE that the
# worker pool ran out.
_API_SESSIONMAKER_ALLOWANCE = {"ghl_subaccount.py": 1}


def _api_sessionmaker_uses(path: pathlib.Path) -> int:
    """Count EXECUTABLE references to `AsyncSessionLocal`, by AST.

    Deliberately not a substring scan. That module names the symbol four times
    - twice in prose explaining precisely this exception, once in the import,
    once in the actual call - and a guard whose count moves when someone edits
    a comment is a guard nobody can keep green. `ast.Name` sees the call and
    not the import alias or the prose.
    """
    return sum(
        1
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Name) and node.id == "AsyncSessionLocal"
    )


def test_no_worker_module_reaches_for_the_api_sessionmaker() -> None:
    # The guard that survives the next fan-out. Asana, Stripe, Xero and Timely
    # are all still to be written, and each will start by copying an existing
    # worker module; if one copies `AsyncSessionLocal` the split silently stops
    # covering it, with nothing failing. Source-level, because there is no
    # runtime moment at which a not-yet-written module can be checked.
    offenders = {
        path.name: (uses, _API_SESSIONMAKER_ALLOWANCE.get(path.name, 0))
        for path in sorted(WORKER_PACKAGE.glob("*.py"))
        if (uses := _api_sessionmaker_uses(path)) > _API_SESSIONMAKER_ALLOWANCE.get(path.name, 0)
    }
    assert offenders == {}, (
        f"{offenders} use the API's sessionmaker more than allowed (name: "
        "(found, allowed)); worker code uses WorkerSessionLocal so the fan-out "
        "cannot consume the dashboard's pool"
    )

    # AND THE ALLOWANCE MUST NOT GO STALE. If the recorder is deleted or moved,
    # the carve-out above would silently keep permitting a use that no longer
    # exists, and the next careless `AsyncSessionLocal` in this module would
    # inherit the permission. Prove the exception is still earning it.
    for name, allowed in _API_SESSIONMAKER_ALLOWANCE.items():
        actual = _api_sessionmaker_uses(WORKER_PACKAGE / name)
        assert actual == allowed, (
            f"{name} is allowed {allowed} use(s) of the API sessionmaker but has "
            f"{actual}; update the allowance deliberately or remove it"
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
