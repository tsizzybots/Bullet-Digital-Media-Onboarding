"""The dedup advisory lock, exercised under the PRODUCTION engine settings.

Round 13, P1.5. `db/session.py` puts `statement_timeout = 5000` on every
connection, and Postgres applies that ceiling to a statement BLOCKED WAITING ON
A LOCK, not only to execution time. The phase-2 holder keeps
`pg_advisory_xact_lock` from before the GHL lookup through `create_location`
(10.0s httpx timeout each) to the terminal commit - roughly 20s - so a racer
blocking on the same lock was cancelled at 5s with `QueryCanceledError` instead
of queueing behind it. The lock could not serialise the exact case it was built
for.

`test_ghl_subaccount.py::test_dedup_lock_serialises_same_email_runs` cannot see
any of that: it builds its own engine with no `statement_timeout`, so it proves
the SQL takes a lock and nothing about how that lock behaves in production.
Every engine in this module is built from
`bullet_api.db.session.ENGINE_SERVER_SETTINGS` - the same dict the production
engine is constructed with, not a local copy of the number - so these tests
cannot drift away from the settings they claim to reproduce.

The timing tests here take ~6s each by construction: proving a waiter blocks
PAST a 5s ceiling requires actually waiting past it.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from bullet_api.config import get_async_database_url, get_settings
from bullet_api.db.session import ENGINE_SERVER_SETTINGS
from bullet_api.ghl.client import GhlLocation
from bullet_api.worker import ghl_subaccount as ghl_subaccount_module
from bullet_api.worker.ghl_subaccount import (
    _DEDUP_LOCK_SQL,
    create_ghl_subaccount_core,
)
from bullet_api.worker.identity_key import compute_identity_key, identity_name

COMPANY_ID = "comp_agency_1"

# The holder keeps the lock for longer than the 5s production ceiling, so a
# waiter that survives to acquire it PROVES the ceiling was raised. Anything at
# or below 5s would pass with the fix reverted.
_HOLD_SECONDS = 6.0

# The waiter must be observed blocking past the production ceiling, with margin
# for scheduler noise on either side of `_HOLD_SECONDS`.
_MUST_BLOCK_PAST_SECONDS = 5.2

# `_acquire_dedup_lock` raises the ceiling to 30s, so a waiter that is still
# blocked well beyond the holder's release is a hang, not a pass.
_MUST_FINISH_WITHIN_SECONDS = 20.0

_LOCK_CANCELLED_MESSAGE = "canceling statement due to statement timeout"


def _production_settings_engine() -> AsyncEngine:
    """An engine carrying the PRODUCTION `server_settings`.

    NullPool, not the production engine itself: the pooled engine would hand
    connections created under this test's event loop to a later test's loop
    (the leak conftest's docstring warns about), failing an unrelated test in
    suite order only.
    """
    return create_async_engine(
        get_async_database_url(),
        poolclass=NullPool,
        connect_args={
            "ssl": get_settings().database_ssl_mode,
            "server_settings": ENGINE_SERVER_SETTINGS,
        },
    )


# The ceiling every timing test below establishes FOR ITSELF, inside its own
# transaction, rather than inheriting from the environment.
#
# WHY (round 14, second CI failure): the engine's `server_settings` are a
# STARTUP parameter, and whether they survive depends on what sits between the
# client and Postgres. Local Docker applies them (`SHOW statement_timeout` ->
# '5s'); the Neon endpoint CI connects to does NOT (-> '0'), so three tests
# here failed on `assert '0' == '5s'` while passing locally. Pinning the
# environment's number made these tests assertions about the deployment rather
# than about the code. `SET LOCAL` inside the test's own transaction is
# transaction-scoped, so it behaves identically everywhere, including through a
# transaction-pooling proxy.
_TEST_CEILING = "5000"


async def _pin_test_ceiling(executor: AsyncSession | object) -> None:
    """Establish the 5s ceiling on THIS transaction, whatever the environment default."""
    await executor.execute(text(f"SET LOCAL statement_timeout = '{_TEST_CEILING}'"))  # type: ignore[attr-defined]


async def _safe_rollback(executor: AsyncSession | object) -> None:
    """Roll back, tolerating a connection the server has already closed.

    A statement cancelled by `statement_timeout` can leave asyncpg's underlying
    connection closed, and rollback() then raises
    `InterfaceError: cannot call Transaction.rollback(): the underlying
    connection is closed`. That is exactly how CI reported the bare-lock test:
    the teardown raised, masking the assertion the test exists for.
    """
    try:
        await executor.rollback()  # type: ignore[attr-defined]
    except (InterfaceError, DBAPIError, RuntimeError):
        pass


@pytest.mark.db
async def test_the_environment_ceiling_is_reported_not_assumed() -> None:
    """What ceiling does THIS environment actually apply, and does it match the dict?

    Deliberately asserts only what is true in every environment. The engine is
    BUILT with `statement_timeout: 5000`, but whether the server honours that
    startup parameter is a property of the deployment path, not of this code:
    a transaction-pooling proxy can drop it, in which case the effective
    ceiling is the server default.

    This replaces a test that asserted `ENGINE_SERVER_SETTINGS["statement_timeout"]
    == "5000"` and called itself "the premise of every timing test below". That
    assertion never opened a connection, so it held identically whether the
    parameter reached Postgres or not - vacuous in precisely the situation it
    claimed to rule out, which is the guard-that-cannot-fail class this project
    exists to catch.
    """
    assert ENGINE_SERVER_SETTINGS["statement_timeout"] == _TEST_CEILING
    engine = _production_settings_engine()
    async with engine.connect() as conn:
        effective = (await conn.execute(text("SHOW statement_timeout"))).scalar_one()
        # No equality assertion on `effective`: '5s' where the startup parameter
        # survives, '0' where it is dropped. Both are legitimate environments,
        # and the timing tests below no longer depend on which one this is.
        assert isinstance(effective, str) and effective
        await _safe_rollback(conn)
    await engine.dispose()


@pytest.mark.db
async def test_bare_lock_wait_is_cancelled_at_the_production_ceiling() -> None:
    """THE DEFECT, reproduced: an unraised lock wait dies at 5s.

    This is the round-13 P1.5 finding executed as a test. Without it, the
    serialisation test below is unreadable - a reader cannot tell whether the
    waiter survives because the fix works or because nothing ever threatened
    it.
    """
    engine = _production_settings_engine()
    email = f"bare-{uuid.uuid4().hex}@lock.example.com"
    async with engine.connect() as holder, engine.connect() as waiter:
        await holder.execute(_DEDUP_LOCK_SQL, {"email": email})

        # The waiter establishes the ceiling for its OWN transaction, so this
        # test proves the same thing whether or not the environment applies the
        # engine's startup parameter.
        await _pin_test_ceiling(waiter)

        started = time.monotonic()
        with pytest.raises(DBAPIError) as caught:
            await waiter.execute(_DEDUP_LOCK_SQL, {"email": email})
        elapsed = time.monotonic() - started

        assert _LOCK_CANCELLED_MESSAGE in str(caught.value)
        # Cancelled by the ceiling, not by anything faster: a lock that was
        # never actually contended would return in milliseconds.
        assert 4.0 < elapsed < _MUST_BLOCK_PAST_SECONDS, elapsed

        # The cancellation can close the underlying connection, so neither
        # rollback may raise past the assertions above (CI reported this test
        # as an InterfaceError from teardown, not as its real assertion).
        await _safe_rollback(waiter)
        await _safe_rollback(holder)
    await engine.dispose()


@pytest.mark.db
async def test_acquire_dedup_lock_serialises_past_the_production_ceiling() -> None:
    """THE FIX: the same waiter, going through `_acquire_dedup_lock`, queues.

    The holder keeps the lock for 6s - past the 5s ceiling that cancelled the
    bare waiter above - and the waiter must BLOCK for that whole time and then
    ACQUIRE, rather than being cancelled mid-queue.
    """
    engine = _production_settings_engine()
    email = f"held-{uuid.uuid4().hex}@lock.example.com"
    async with engine.connect() as holder, engine.connect() as waiter:
        await holder.execute(_DEDUP_LOCK_SQL, {"email": email})
        waiter_session = AsyncSession(bind=waiter)
        # Without this the test is only meaningful where the environment
        # happens to impose a low ceiling: the waiter would survive because
        # nothing threatened it, not because `_acquire_dedup_lock` raised it.
        await _pin_test_ceiling(waiter_session)

        async def _hold_then_release() -> None:
            await asyncio.sleep(_HOLD_SECONDS)
            await holder.rollback()

        async def _wait_for_the_lock() -> float:
            started = time.monotonic()
            # Different casing on purpose: the lock keys on `lower(email)`, so
            # a racer arriving with other casing must still queue behind the
            # holder rather than taking a private lock and racing on.
            await ghl_subaccount_module._acquire_dedup_lock(waiter_session, email.upper())
            return time.monotonic() - started

        _, elapsed = await asyncio.gather(_hold_then_release(), _wait_for_the_lock())

        assert elapsed > _MUST_BLOCK_PAST_SECONDS, (
            f"the waiter returned after {elapsed:.2f}s, which is inside the 5s ceiling "
            "this test pinned - it cannot have queued behind a 6s holder"
        )
        assert elapsed < _MUST_FINISH_WITHIN_SECONDS, elapsed

        await _safe_rollback(waiter_session)
    await engine.dispose()


@pytest.mark.db
async def test_acquire_dedup_lock_restores_the_engine_ceiling() -> None:
    """The raise is for the WAIT only; every later statement keeps the 5s ceiling.

    A `SET LOCAL` that is never put back would silently widen the ceiling for
    the sibling scan, the write-back and the terminal commit - a change this
    fix has no reason to make, and one nothing else would notice.
    """
    engine = _production_settings_engine()
    async with engine.connect() as conn:
        session = AsyncSession(bind=conn)
        before = (await session.execute(text("SHOW statement_timeout"))).scalar_one()

        await ghl_subaccount_module._acquire_dedup_lock(
            session, f"solo-{uuid.uuid4().hex}@lock.example.com"
        )

        after = (await session.execute(text("SHOW statement_timeout"))).scalar_one()
        # Compared against the BASELINE this connection actually had, not the
        # literal "5s": the property under test is "the raise did not leak",
        # which holds whatever the environment's default is. Under the
        # pre-fix code `after` is "30s" and this fails in every environment.
        assert after == before, (
            f"the raised ceiling leaked into the rest of the transaction: "
            f"{after} (baseline was {before})"
        )

        await _safe_rollback(session)
    await engine.dispose()


@pytest.mark.db
async def test_set_local_does_not_survive_a_commit() -> None:
    """Why phase 1 and phase 2 each acquire through the helper.

    `SET LOCAL` is transaction-scoped, so the commit between the two
    acquisitions resets it. One raise cannot cover both, and a fix that raised
    the ceiling only at phase 2 (the long holder, the obvious site) would leave
    phase 1 - whose worst-case wait is that same ~20s holder - cancelled at 5s.
    """
    engine = _production_settings_engine()
    async with engine.connect() as conn:
        session = AsyncSession(bind=conn)
        baseline = (await session.execute(text("SHOW statement_timeout"))).scalar_one()
        await session.execute(text("SET LOCAL statement_timeout = '30s'"))
        assert (await session.execute(text("SHOW statement_timeout"))).scalar_one() == "30s"

        await session.commit()

        # Back to whatever this connection had BEFORE, which is the point: the
        # raise is transaction-scoped. Asserting the literal "5s" made this a
        # statement about the deployment instead of about `SET LOCAL`.
        after_commit = (await session.execute(text("SHOW statement_timeout"))).scalar_one()
        assert after_commit == baseline, (
            f"SET LOCAL survived the commit: {after_commit} (baseline was {baseline})"
        )
        await _safe_rollback(session)
    await engine.dispose()


# --------------------------------------------------------------------------
# The ordering contract: a lock failure leaves a VISIBLE action row.
# --------------------------------------------------------------------------


class _RecordingGhlClient:
    """Records every call so a test can prove the run never reached GHL."""

    def __init__(self) -> None:
        self.lookups: list[str] = []
        self.creates: list[dict] = []

    async def find_location_by_email(self, email: str, *, company_id: str) -> GhlLocation | None:
        self.lookups.append(email)
        return None

    async def create_location(self, payload: dict) -> GhlLocation:
        self.creates.append(payload)
        return GhlLocation(
            id="loc_created_1", name=str(payload.get("name", "")), company_id=COMPANY_ID, raw={}
        )


async def _seed_client(
    session: AsyncSession, *, email: str, business_name: str = "Lock Order Gym Ltd"
) -> uuid.UUID:
    """A minimal unprovisioned `clients` row, keyed exactly as S1-26c does.

    `business_name` is overridable because the one test that commits for real
    must not share an `identity_key` with rows a previous run left behind - it
    would find them as siblings and link instead of taking the create path.
    """
    postal_code = "E8 1AA"
    result = await session.execute(
        text(
            "INSERT INTO clients ("
            "  email, business_name, legal_entity, postal_code, identity_key, "
            "  current_step, step_entered_at, created_at"
            ") VALUES ("
            "  :email, :business_name, :business_name, :postal_code, :identity_key, "
            "  'signed', now(), now()"
            ") RETURNING id"
        ),
        {
            "email": email,
            "business_name": business_name,
            "postal_code": postal_code,
            "identity_key": compute_identity_key(
                identity_name(business_name, business_name), postal_code
            ),
        },
    )
    return result.scalar_one()


def _raise_on_nth_acquisition(monkeypatch: pytest.MonkeyPatch, n: int, message: str) -> Callable:
    """Let the real helper run, but blow up on the `n`th acquisition.

    Delegating to the real `_acquire_dedup_lock` for the other calls keeps the
    transaction in the state the code under test expects, so the run reaches
    the phase being tested for the real reason rather than a stubbed one.
    """
    real = ghl_subaccount_module._acquire_dedup_lock
    calls = {"n": 0}

    async def _acquire(session: AsyncSession, email: str) -> None:
        calls["n"] += 1
        if calls["n"] == n:
            raise RuntimeError(message)
        await real(session, email)

    monkeypatch.setattr(ghl_subaccount_module, "_acquire_dedup_lock", _acquire)
    return lambda: calls["n"]


async def _action_row(session: AsyncSession, client_id: uuid.UUID):
    return (
        await session.execute(
            text(
                "SELECT status, last_error, payload FROM platform_actions "
                "WHERE client_id = :client_id AND platform = 'ghl' "
                "  AND action = 'create_subaccount'"
            ),
            {"client_id": client_id},
        )
    ).one_or_none()


@pytest.mark.db
async def test_phase_1_lock_failure_still_records_the_action_row(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 13, P1.5 - the ordering half.

    The phase-1 acquisition used to sit BEFORE `begin_action`, so a lock
    failure propagated out with no `platform_actions` row ever created - a
    silent gap, which this module's contract explicitly forbids ("a crash
    mid-call leaves a visible `in_progress` row rather than a silent gap").

    `payload IS NULL` is what makes this test specific to PHASE 1: the create
    path writes the payload between the two acquisitions, so a run that failed
    at phase 2 instead would carry one. Without it, deleting the phase-1
    acquisition would simply move the failure to phase 2 and this test would
    still pass.
    """
    client_id = await _seed_client(async_session, email="phase1@lock-order.example.com")
    ghl_client = _RecordingGhlClient()
    _raise_on_nth_acquisition(monkeypatch, 1, "phase-1 lock wait cancelled")

    with pytest.raises(RuntimeError, match="phase-1 lock wait cancelled"):
        await create_ghl_subaccount_core(
            async_session,
            ghl_client,
            client_id=client_id,
            onboarding_event_id=None,
            company_id=COMPANY_ID,
        )

    row = await _action_row(async_session, client_id)
    assert row is not None, "the lock failure left NO platform_actions row - the silent gap"
    assert row.status == "failed"
    assert "phase-1 lock wait cancelled" in row.last_error
    assert row.payload is None, "a phase-1 failure cannot have reached the create path's payload"
    assert ghl_client.lookups == []
    assert ghl_client.creates == []


@pytest.mark.db
async def test_phase_2_lock_failure_still_records_the_action_row(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The phase-2 acquisition, pinned the same way.

    `payload IS NOT NULL` is the mirror of the phase-1 test: it proves phase 1
    was acquired and the create path ran up to the point of taking the lock
    again. Deleting the phase-2 acquisition leaves the injected failure
    unreached, the run succeeds, and the `failed` assertion dies.
    """
    client_id = await _seed_client(async_session, email="phase2@lock-order.example.com")
    ghl_client = _RecordingGhlClient()
    calls = _raise_on_nth_acquisition(monkeypatch, 2, "phase-2 lock wait cancelled")

    with pytest.raises(RuntimeError, match="phase-2 lock wait cancelled"):
        await create_ghl_subaccount_core(
            async_session,
            ghl_client,
            client_id=client_id,
            onboarding_event_id=None,
            company_id=COMPANY_ID,
        )

    assert calls() == 2, "the run did not reach the phase-2 acquisition"
    row = await _action_row(async_session, client_id)
    assert row is not None
    assert row.status == "failed"
    assert "phase-2 lock wait cancelled" in row.last_error
    assert row.payload is not None, "the create path commits its payload before the phase-2 lock"
    assert ghl_client.lookups == []
    assert ghl_client.creates == []


@pytest.mark.db
async def test_payload_commit_failure_records_failed_rather_than_stranding(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raise point the reorder CREATED, guarded (round 13, P1.5).

    The payload commit used to be the action row's FIRST commit, so a failure
    there stranded nothing - there was no committed row yet. Hoisting the claim
    above the lock means there now is one, so an unguarded raise here would
    leave a committed `in_progress` row with no `last_error` saying why. A Neon
    connection reset between an UPDATE and its COMMIT is the case the module
    already calls routine.
    """
    client_id = await _seed_client(async_session, email="commit-fail@lock-order.example.com")
    real_commit = async_session.commit
    calls = {"n": 0}

    async def _commit() -> None:
        calls["n"] += 1
        # 1 = the pre-lock `in_progress` claim, 2 = the payload commit.
        if calls["n"] == 2:
            raise RuntimeError("connection reset before the payload commit landed")
        await real_commit()

    monkeypatch.setattr(async_session, "commit", _commit)

    with pytest.raises(RuntimeError, match="connection reset before the payload commit"):
        await create_ghl_subaccount_core(
            async_session,
            _RecordingGhlClient(),
            client_id=client_id,
            onboarding_event_id=None,
            company_id=COMPANY_ID,
        )

    row = await _action_row(async_session, client_id)
    assert row is not None
    assert row.status == "failed", (
        "the payload commit raised outside the guard, stranding a committed "
        "in_progress row with no recorded reason"
    )
    assert "connection reset before the payload commit" in row.last_error


@pytest.mark.db
async def test_a_real_cancelled_lock_wait_leaves_a_committed_failed_action_row() -> None:
    """Round 13, P1.5 - the ordering proven with REAL abort semantics.

    The two tests above inject a `RuntimeError`, which pins that each
    acquisition site is CALLED but leaves the transaction healthy. A real lock
    failure does not: it is a DB error, so Postgres aborts the transaction and
    every uncommitted statement in it is discarded. That is why hoisting
    `begin_action` above the lock is not sufficient on its own - the INSERT has
    to be COMMITTED first, or the very statement that fails throws it away and
    the silent gap is exactly where it was.

    So this runs on a REAL engine (the `async_session` fixture's
    `join_transaction_mode="create_savepoint"` turns every `commit()` into a
    savepoint release, which cannot show the difference), with a holder on a
    second connection and a short `lock_timeout` standing in for the 30s
    ceiling being exceeded - the same 55P03/aborted-transaction shape, without
    a 30s test.
    """
    engine = _production_settings_engine()
    marker = uuid.uuid4().hex[:8]
    email = f"abort-{marker}@lock-order.example.com"
    ghl_client = _RecordingGhlClient()
    async with engine.connect() as runner_conn, engine.connect() as holder_conn:
        session = AsyncSession(bind=runner_conn)
        client_id = await _seed_client(
            session, email=email, business_name=f"Abort Order Gym {marker} Ltd"
        )
        await session.commit()
        try:
            await holder_conn.execute(_DEDUP_LOCK_SQL, {"email": email})
            # Session-scoped (not LOCAL) so it survives the commits the code
            # under test makes; it fires long before the helper's own 30s.
            await session.execute(text("SET lock_timeout = '300ms'"))

            with pytest.raises(DBAPIError):
                await create_ghl_subaccount_core(
                    session,
                    ghl_client,
                    client_id=client_id,
                    onboarding_event_id=None,
                    company_id=COMPANY_ID,
                )
            await holder_conn.rollback()

            row = await _action_row(session, client_id)
            assert row is not None, (
                "a cancelled lock wait left NO platform_actions row - the aborted "
                "transaction discarded an uncommitted begin_action"
            )
            assert row.status == "failed"
            assert "lock timeout" in row.last_error
            assert row.payload is None
            assert ghl_client.creates == []
        finally:
            await session.rollback()
            await session.execute(
                text("DELETE FROM platform_actions WHERE client_id = :id"), {"id": client_id}
            )
            await session.execute(text("DELETE FROM clients WHERE id = :id"), {"id": client_id})
            await session.commit()
    await engine.dispose()


@pytest.mark.db
async def test_create_path_records_the_payload_and_reuse_paths_do_not(
    async_session: AsyncSession,
) -> None:
    """Hoisting `begin_action` must not move PII onto the reuse paths.

    The row is now claimed once, before the lock, with `payload=None`; only the
    create path fills it in. Claiming it with the payload instead would have
    been the smaller diff and would have written the contact block (name,
    email, phone, address) onto every reuse action, which has never held it.
    """
    creator_id = await _seed_client(async_session, email="creates@lock-order.example.com")
    await create_ghl_subaccount_core(
        async_session,
        _RecordingGhlClient(),
        client_id=creator_id,
        onboarding_event_id=None,
        company_id=COMPANY_ID,
    )
    created = await _action_row(async_session, creator_id)
    assert created is not None
    assert created.status == "success"
    assert created.payload is not None
    assert created.payload["companyId"] == COMPANY_ID

    # A second signing for the SAME business links to the first rather than
    # POSTing, so its action row must keep a NULL payload.
    returning_id = await _seed_client(async_session, email="creates@lock-order.example.com")
    await async_session.execute(
        text(
            "UPDATE clients SET contact_first_name = 'Sam', contact_last_name = 'Reed', "
            "phone = '+44 20 7946 0100' WHERE id IN (:a, :b)"
        ),
        {"a": creator_id, "b": returning_id},
    )
    reused = await create_ghl_subaccount_core(
        async_session,
        _RecordingGhlClient(),
        client_id=returning_id,
        onboarding_event_id=None,
        company_id=COMPANY_ID,
    )
    assert reused.skipped is True
    reuse_row = await _action_row(async_session, returning_id)
    assert reuse_row is not None
    assert reuse_row.status == "success"
    assert reuse_row.payload is None, "a reuse action must not record a request body we never sent"
