"""Tests for GET /clients - the dashboard list view (S1-31).

Auth is exercised two ways: the happy-path tests override `get_current_user`
with a fake founder (the endpoint only uses the user to gate, not its data),
while the 401 test leaves the real dependency in place and calls with no
cookie. Every test tags its seeded rows with a per-test run id (unique email /
business name) and asserts only on its own rows, so the suite is robust against
any clients already committed in the test database rather than depending on a
globally empty table.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.auth import CurrentUser, get_current_user
from bullet_api.db import get_session
from bullet_api.main import app

_FAKE_FOUNDER = CurrentUser(
    id=uuid.uuid4(),
    email="dash@example.com",
    full_name="Dash Board",
    role="founder",
)


@pytest_asyncio.fixture
async def authed_client(
    async_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """AsyncClient sharing the test session, authenticated as a founder."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_current_user] = lambda: _FAKE_FOUNDER
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def anon_client(
    async_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """AsyncClient with only `get_session` overridden - real auth dependency,
    so a request without a session cookie 401s."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = _session_override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


async def _seed_client(
    db: AsyncSession,
    *,
    email: str,
    current_step: str,
    step_entered_at: datetime,
    business_name: str | None = None,
    contact_first_name: str | None = None,
    contact_last_name: str | None = None,
) -> uuid.UUID:
    result = await db.execute(
        text(
            "INSERT INTO clients "
            "  (email, legal_entity, business_name, contact_first_name, "
            "   contact_last_name, current_step, step_entered_at) "
            "VALUES (:email, :legal_entity, :business_name, :first, :last, "
            "        :current_step, :step_entered_at) "
            "RETURNING id"
        ),
        {
            "email": email,
            "legal_entity": business_name or email,
            "business_name": business_name,
            "first": contact_first_name,
            "last": contact_last_name,
            "current_step": current_step,
            "step_entered_at": step_entered_at,
        },
    )
    return result.scalar_one()


async def _seed_action(
    db: AsyncSession,
    *,
    client_id: uuid.UUID,
    platform: str,
    action: str,
    status: str,
    started_at: datetime | None,
    action_id: uuid.UUID | None = None,
) -> uuid.UUID:
    # `action_id` is explicit when a test needs to control the `pa.id DESC`
    # tiebreak deterministically (the column defaults to a random
    # gen_random_uuid(), so insertion order does not imply id order).
    action_id = action_id or uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO platform_actions "
            "  (id, client_id, platform, action, idempotency_key, status, started_at) "
            "VALUES (:id, :client_id, :platform, :action, :key, :status, :started_at)"
        ),
        {
            "id": action_id,
            "client_id": client_id,
            "platform": platform,
            "action": action,
            "key": f"{client_id}:{platform}:{action}:{uuid.uuid4().hex}",
            "status": status,
            "started_at": started_at,
        },
    )
    return action_id


def _by_email(payload: dict, email: str) -> dict | None:
    for row in payload["clients"]:
        if row["email"] == email:
            return row
    return None


@pytest.mark.db
async def test_lists_clients_with_step_and_ordering(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    run = uuid.uuid4().hex[:8]
    base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    # Three clients in different steps; step_entered_at ascending a -> b -> c,
    # so the DESC ordering must surface them c, b, a.
    a = f"a_{run}@gym.example"
    b = f"b_{run}@gym.example"
    c = f"c_{run}@gym.example"
    await _seed_client(
        async_session,
        email=a,
        current_step="sales_call",
        step_entered_at=base,
        business_name=f"Iron A {run}",
        contact_first_name="Jo",
        contact_last_name="Lifter",
    )
    await _seed_client(
        async_session,
        email=b,
        current_step="signed",
        step_entered_at=base + timedelta(hours=1),
        business_name=f"Iron B {run}",
    )
    await _seed_client(
        async_session,
        email=c,
        current_step="live",
        step_entered_at=base + timedelta(hours=2),
        business_name=f"Iron C {run}",
    )

    resp = await authed_client.get("/clients")
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    row_a = _by_email(payload, a)
    row_b = _by_email(payload, b)
    row_c = _by_email(payload, c)
    assert row_a and row_b and row_c
    assert row_a["current_step"] == "sales_call"
    assert row_b["current_step"] == "signed"
    assert row_c["current_step"] == "live"
    # contact_name is composed; absent parts collapse to None.
    assert row_a["contact_name"] == "Jo Lifter"
    assert row_b["contact_name"] is None
    assert row_a["business_name"] == f"Iron A {run}"

    # DESC by step_entered_at: among our three, c precedes b precedes a.
    emails_in_order = [r["email"] for r in payload["clients"] if r["email"] in {a, b, c}]
    assert emails_in_order == [c, b, a]


@pytest.mark.db
async def test_last_action_reflects_most_recently_started(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    run = uuid.uuid4().hex[:8]
    email = f"actions_{run}@gym.example"
    client_id = await _seed_client(
        async_session,
        email=email,
        current_step="signed",
        step_entered_at=datetime(2026, 6, 2, 9, 0, 0, tzinfo=UTC),
    )
    # Earlier action succeeded; later action failed -> the list must report the
    # later (failed) one, ordered by started_at DESC.
    await _seed_action(
        async_session,
        client_id=client_id,
        platform="ghl",
        action="create_subaccount",
        status="success",
        started_at=datetime(2026, 6, 2, 9, 1, 0, tzinfo=UTC),
    )
    await _seed_action(
        async_session,
        client_id=client_id,
        platform="stripe",
        action="create_customer",
        status="failed",
        started_at=datetime(2026, 6, 2, 9, 5, 0, tzinfo=UTC),
    )

    resp = await authed_client.get("/clients")
    assert resp.status_code == 200, resp.text
    row = _by_email(resp.json(), email)
    assert row is not None
    assert row["last_action_status"] == "failed"
    assert row["last_action_platform"] == "stripe"
    assert row["last_action"] == "create_customer"


@pytest.mark.db
async def test_last_action_tiebreaks_on_id_when_started_at_equal(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    # Two actions sharing an identical started_at (the same-now() fan-out case):
    # the LATERAL must break the tie on `pa.id DESC` so the reported action is
    # stable between polls. Postgres orders uuid by its big-endian value, which
    # matches Python's UUID ordering, so the higher of the two sorted ids wins.
    run = uuid.uuid4().hex[:8]
    email = f"tie_{run}@gym.example"
    client_id = await _seed_client(
        async_session,
        email=email,
        current_step="signed",
        step_entered_at=datetime(2026, 6, 4, 9, 0, 0, tzinfo=UTC),
    )
    same = datetime(2026, 6, 4, 9, 0, 0, tzinfo=UTC)
    id_low, id_high = sorted([uuid.uuid4(), uuid.uuid4()])
    # Lower id seeded as the "loser" - if the id tiebreak were dropped, ordering
    # would be non-deterministic and this assertion would flap.
    await _seed_action(
        async_session,
        client_id=client_id,
        platform="ghl",
        action="loser_action",
        status="success",
        started_at=same,
        action_id=id_low,
    )
    await _seed_action(
        async_session,
        client_id=client_id,
        platform="stripe",
        action="winner_action",
        status="failed",
        started_at=same,
        action_id=id_high,
    )

    resp = await authed_client.get("/clients")
    assert resp.status_code == 200, resp.text
    row = _by_email(resp.json(), email)
    assert row is not None
    assert row["last_action"] == "winner_action"
    assert row["last_action_platform"] == "stripe"
    assert row["last_action_status"] == "failed"


@pytest.mark.db
async def test_last_action_orders_non_null_started_at_before_null(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    # NULLS LAST must win over the id tiebreak: a timestamped action outranks a
    # never-started (NULL started_at) one even when the NULL row has the higher
    # id. Guards against the all-NULL/partial-NULL flicker the ordering fixes.
    run = uuid.uuid4().hex[:8]
    email = f"nulls_{run}@gym.example"
    client_id = await _seed_client(
        async_session,
        email=email,
        current_step="signed",
        step_entered_at=datetime(2026, 6, 5, 9, 0, 0, tzinfo=UTC),
    )
    id_low, id_high = sorted([uuid.uuid4(), uuid.uuid4()])
    # NULL-started_at row gets the HIGHER id - so if NULLS LAST were dropped it
    # would wrongly win on the id tiebreak.
    await _seed_action(
        async_session,
        client_id=client_id,
        platform="ghl",
        action="never_started",
        status="pending",
        started_at=None,
        action_id=id_high,
    )
    await _seed_action(
        async_session,
        client_id=client_id,
        platform="stripe",
        action="real_started",
        status="failed",
        started_at=datetime(2026, 6, 5, 9, 2, 0, tzinfo=UTC),
        action_id=id_low,
    )

    resp = await authed_client.get("/clients")
    assert resp.status_code == 200, resp.text
    row = _by_email(resp.json(), email)
    assert row is not None
    assert row["last_action"] == "real_started"
    assert row["last_action_platform"] == "stripe"
    assert row["last_action_status"] == "failed"


@pytest.mark.db
async def test_client_without_actions_has_null_last_action(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    run = uuid.uuid4().hex[:8]
    email = f"noaction_{run}@gym.example"
    await _seed_client(
        async_session,
        email=email,
        current_step="agreement",
        step_entered_at=datetime(2026, 6, 3, 9, 0, 0, tzinfo=UTC),
    )

    resp = await authed_client.get("/clients")
    assert resp.status_code == 200, resp.text
    row = _by_email(resp.json(), email)
    assert row is not None
    assert row["last_action_status"] is None
    assert row["last_action_platform"] is None
    assert row["last_action"] is None


@pytest.mark.db
async def test_envelope_shape_is_a_list(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    # Shape contract: `{"clients": [...]}` validated by the response_model.
    # (We assert shape rather than global emptiness so the test is robust to
    # any clients already committed in the database.)
    resp = await authed_client.get("/clients")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert isinstance(payload, dict)
    assert isinstance(payload["clients"], list)


@pytest.mark.db
async def test_requires_authentication(
    anon_client: AsyncClient,
) -> None:
    resp = await anon_client.get("/clients")
    assert resp.status_code == 401
