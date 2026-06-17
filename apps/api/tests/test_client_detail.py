"""Tests for GET /clients/{id} - the dashboard detail view (S1-32).

Reuses the S1-31 fixture pattern: happy-path tests override `get_current_user`
with a fake founder; the 401 test keeps the real dependency. Seeded rows are
tagged with a per-test run id so the suite never depends on a globally empty
table.

The sales-summary tests seed `client_knowledge` rows in the PINNED S1-30
contract shape (PRD §7.1: source='sales_call', key = top-level field name,
value = the §7.1 JSONB shape, one row per field, batches stamped by
captured_at) - proving the page reads exactly what S1-30 will later write.
"""

from __future__ import annotations

import json
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
async def authed_client(async_session: AsyncSession) -> AsyncIterator[AsyncClient]:
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
async def anon_client(async_session: AsyncSession) -> AsyncIterator[AsyncClient]:
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
    business_name: str | None = None,
    phone: str | None = None,
    ghl_subaccount_id: str | None = None,
    stripe_customer_id: str | None = None,
) -> uuid.UUID:
    result = await db.execute(
        text(
            "INSERT INTO clients "
            "  (email, legal_entity, business_name, contact_first_name, "
            "   contact_last_name, phone, current_step, "
            "   ghl_subaccount_id, stripe_customer_id) "
            "VALUES (:email, :legal, :business, 'Test', 'Owner', :phone, "
            "        'signed', :ghl, :stripe) "
            "RETURNING id"
        ),
        {
            "email": email,
            "legal": business_name or email,
            "business": business_name,
            "phone": phone,
            "ghl": ghl_subaccount_id,
            "stripe": stripe_customer_id,
        },
    )
    return result.scalar_one()


# The full PRD §7.1 summary shape - exactly what S1-30 will write.
def _summary_batch(run: str) -> dict[str, object]:
    return {
        "business_type": f"Boutique strength gym {run}",
        "business_goals": ["Grow memberships", "Launch PT tier"],
        "budget_range_usd": {"min": 1000, "max": 2500, "currency": "USD"},
        "pain_points": ["No-show rate", "Churn after 3 months"],
        "red_flags": [],
        "next_steps": ["Send agreement"],
        "notable_quotes": [
            {"speaker": "Owner", "quote": "We tried ads before", "timestamp_seconds": 312}
        ],
    }


async def _seed_summary_batch(
    db: AsyncSession,
    client_id: uuid.UUID,
    captured_at: datetime,
    fields: dict[str, object],
    *,
    source: str = "sales_call",
) -> None:
    for key, value in fields.items():
        await db.execute(
            text(
                "INSERT INTO client_knowledge "
                "  (client_id, source, key, value, value_text, captured_at) "
                "VALUES (:cid, :source, :key, cast(:value AS jsonb), "
                "        :value_text, :captured_at)"
            ),
            {
                "cid": client_id,
                "source": source,
                "key": key,
                "value": json.dumps(value),
                # Match the seeds + the S1-30 writer contract (JSON for
                # non-strings) so the fixture mirrors real rows faithfully.
                "value_text": json.dumps(value) if not isinstance(value, str) else value,
                "captured_at": captured_at,
            },
        )


async def _seed_action(
    db: AsyncSession,
    client_id: uuid.UUID,
    *,
    platform: str,
    action: str,
    status: str,
    started_at: datetime | None,
    inngest_run_id: str | None = None,
    last_error: str | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO platform_actions "
            "  (client_id, platform, action, idempotency_key, status, "
            "   started_at, inngest_run_id, last_error) "
            "VALUES (:cid, :platform, :action, :key, :status, "
            "        :started_at, :run_id, :last_error)"
        ),
        {
            "cid": client_id,
            "platform": platform,
            "action": action,
            "key": f"{client_id}:{platform}:{action}:{uuid.uuid4().hex}",
            "status": status,
            "started_at": started_at,
            "run_id": inngest_run_id,
            "last_error": last_error,
        },
    )


@pytest.mark.db
async def test_detail_returns_metadata_summary_and_actions(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    run = uuid.uuid4().hex[:8]
    client_id = await _seed_client(
        async_session,
        email=f"detail_{run}@gym.example",
        business_name=f"Detail Gym {run}",
        phone="+44 7700 900000",
        ghl_subaccount_id="ghl-loc-123",
    )
    captured = datetime(2026, 6, 5, 10, 0, 0, tzinfo=UTC)
    await _seed_summary_batch(async_session, client_id, captured, _summary_batch(run))
    await _seed_action(
        async_session,
        client_id,
        platform="ghl",
        action="create_subaccount",
        status="success",
        started_at=datetime(2026, 6, 5, 10, 5, 0, tzinfo=UTC),
        inngest_run_id="01RUN_GHL",
    )

    resp = await authed_client.get(f"/clients/{client_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["id"] == str(client_id)
    assert body["business_name"] == f"Detail Gym {run}"
    assert body["contact_name"] == "Test Owner"
    assert body["phone"] == "+44 7700 900000"
    assert body["current_step"] == "signed"
    assert body["ghl_subaccount_id"] == "ghl-loc-123"
    assert body["stripe_customer_id"] is None

    # All 7 §7.1 keys come back, JSONB shapes intact.
    summary = {entry["key"]: entry for entry in body["sales_summary"]}
    assert set(summary) == {
        "business_type",
        "business_goals",
        "budget_range_usd",
        "pain_points",
        "red_flags",
        "next_steps",
        "notable_quotes",
    }
    assert summary["budget_range_usd"]["value"] == {
        "min": 1000,
        "max": 2500,
        "currency": "USD",
    }
    assert summary["notable_quotes"]["value"][0]["timestamp_seconds"] == 312

    assert len(body["actions"]) == 1
    action = body["actions"][0]
    assert action["platform"] == "ghl"
    assert action["status"] == "success"
    assert action["inngest_run_id"] == "01RUN_GHL"


@pytest.mark.db
async def test_latest_capture_batch_wins(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    run = uuid.uuid4().hex[:8]
    client_id = await _seed_client(async_session, email=f"batch_{run}@gym.example")
    old = datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC)
    new = old + timedelta(days=2)
    await _seed_summary_batch(async_session, client_id, old, {"business_type": "OLD value"})
    await _seed_summary_batch(
        async_session, client_id, new, {"business_type": "NEW value", "red_flags": ["x"]}
    )

    resp = await authed_client.get(f"/clients/{client_id}")
    assert resp.status_code == 200, resp.text
    summary = {e["key"]: e for e in resp.json()["sales_summary"]}
    # Only the newer batch is returned - never a mix of two captures.
    assert set(summary) == {"business_type", "red_flags"}
    assert summary["business_type"]["value"] == "NEW value"


@pytest.mark.db
async def test_no_summary_returns_empty_list(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    run = uuid.uuid4().hex[:8]
    client_id = await _seed_client(async_session, email=f"nosummary_{run}@gym.example")

    resp = await authed_client.get(f"/clients/{client_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sales_summary"] == []
    assert body["actions"] == []


@pytest.mark.db
async def test_non_sales_call_sources_excluded(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    """The summary panel is sales_call-only: knowledge rows from sibling
    sources (portal, research, kickoff...) must never leak into it - even
    when they are NEWER than the sales-call batch."""
    run = uuid.uuid4().hex[:8]
    client_id = await _seed_client(async_session, email=f"sources_{run}@gym.example")
    base = datetime(2026, 6, 6, 9, 0, 0, tzinfo=UTC)
    await _seed_summary_batch(async_session, client_id, base, {"business_type": "From the call"})
    await _seed_summary_batch(
        async_session,
        client_id,
        base + timedelta(hours=1),
        {"opening_hours": "06:00-21:00"},
        source="portal",
    )

    resp = await authed_client.get(f"/clients/{client_id}")
    assert resp.status_code == 200, resp.text
    summary = {e["key"]: e for e in resp.json()["sales_summary"]}
    assert set(summary) == {"business_type"}
    assert summary["business_type"]["value"] == "From the call"


@pytest.mark.db
async def test_no_cross_client_leakage(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    """Client A's payload must contain none of client B's summary or actions."""
    run = uuid.uuid4().hex[:8]
    client_a = await _seed_client(async_session, email=f"leak_a_{run}@gym.example")
    client_b = await _seed_client(async_session, email=f"leak_b_{run}@gym.example")
    await _seed_summary_batch(
        async_session,
        client_b,
        datetime(2026, 6, 6, 10, 0, 0, tzinfo=UTC),
        {"business_type": "B only"},
    )
    await _seed_action(
        async_session,
        client_b,
        platform="stripe",
        action="create_customer",
        status="success",
        started_at=datetime(2026, 6, 6, 10, 5, 0, tzinfo=UTC),
    )

    resp = await authed_client.get(f"/clients/{client_a}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sales_summary"] == []
    assert body["actions"] == []


@pytest.mark.db
async def test_actions_ordered_newest_first_and_capped(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    run = uuid.uuid4().hex[:8]
    client_id = await _seed_client(async_session, email=f"actions_{run}@gym.example")
    base = datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC)
    for i in range(22):
        await _seed_action(
            async_session,
            client_id,
            platform="ghl",
            action=f"step_{i}",
            status="success",
            started_at=base + timedelta(minutes=i),
        )

    resp = await authed_client.get(f"/clients/{client_id}")
    assert resp.status_code == 200, resp.text
    actions = resp.json()["actions"]
    assert len(actions) == 20  # capped
    assert actions[0]["action"] == "step_21"  # newest first
    assert actions[-1]["action"] == "step_2"  # oldest two fell off the cap


@pytest.mark.db
async def test_null_started_at_sorts_last_with_error_passthrough(
    async_session: AsyncSession,
    authed_client: AsyncClient,
) -> None:
    """The NULLS LAST branch: a never-started action still appears (last),
    and last_error passes through to the payload."""
    run = uuid.uuid4().hex[:8]
    client_id = await _seed_client(async_session, email=f"nulls_{run}@gym.example")
    await _seed_action(
        async_session,
        client_id,
        platform="ghl",
        action="started_one",
        status="success",
        started_at=datetime(2026, 6, 7, 9, 0, 0, tzinfo=UTC),
    )
    await _seed_action(
        async_session,
        client_id,
        platform="xero",
        action="never_started",
        status="failed",
        started_at=None,
        last_error="boom before start",
    )

    resp = await authed_client.get(f"/clients/{client_id}")
    assert resp.status_code == 200, resp.text
    actions = resp.json()["actions"]
    assert [a["action"] for a in actions] == ["started_one", "never_started"]
    assert actions[1]["started_at"] is None
    assert actions[1]["last_error"] == "boom before start"


@pytest.mark.db
async def test_unknown_uuid_404(authed_client: AsyncClient) -> None:
    resp = await authed_client.get(f"/clients/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.db
async def test_malformed_id_404_not_500(authed_client: AsyncClient) -> None:
    resp = await authed_client.get("/clients/not-a-uuid")
    assert resp.status_code == 404


@pytest.mark.db
async def test_requires_authentication(anon_client: AsyncClient) -> None:
    resp = await anon_client.get(f"/clients/{uuid.uuid4()}")
    assert resp.status_code == 401
