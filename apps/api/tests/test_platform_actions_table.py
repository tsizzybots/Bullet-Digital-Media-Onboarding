"""Tests for the platform_actions table (S1-09)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def _make_client(session: AsyncSession) -> uuid.UUID:
    result = await session.execute(
        text(
            "INSERT INTO clients "
            "  (email, legal_entity, current_step, step_entered_at) "
            "VALUES "
            "  ('pa-test@example.com', 'UK', 'sales_call', now()) "
            "RETURNING id"
        )
    )
    return result.scalar_one()


@pytest.mark.db
async def test_insert_pending_action_succeeds(
    async_session: AsyncSession,
) -> None:
    client_id = await _make_client(async_session)
    result = await async_session.execute(
        text(
            "INSERT INTO platform_actions "
            "  (client_id, platform, action, idempotency_key, status) "
            "VALUES "
            "  (:cid, 'hubspot', 'create_contact', "
            "   :idk, 'pending') "
            "RETURNING id, retry_count, status"
        ),
        {"cid": client_id, "idk": f"hs:create_contact:{client_id}"},
    )
    pa_id, retry_count, status = result.one()
    assert pa_id is not None
    # retry_count defaults to 0 server-side.
    assert retry_count == 0
    assert status == "pending"


@pytest.mark.db
async def test_duplicate_idempotency_key_is_rejected(
    async_session: AsyncSession,
) -> None:
    """Two inserts with the same idempotency_key must violate UNIQUE."""
    client_id = await _make_client(async_session)
    shared_key = f"dup:{client_id}"
    await async_session.execute(
        text(
            "INSERT INTO platform_actions "
            "  (client_id, platform, action, idempotency_key, status) "
            "VALUES "
            "  (:cid, 'ghl', 'create_subaccount', :idk, 'pending')"
        ),
        {"cid": client_id, "idk": shared_key},
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await async_session.execute(
            text(
                "INSERT INTO platform_actions "
                "  (client_id, platform, action, idempotency_key, status) "
                "VALUES "
                "  (:cid, 'ghl', 'create_subaccount', :idk, 'pending')"
            ),
            {"cid": client_id, "idk": shared_key},
        )


@pytest.mark.db
async def test_status_transitions_through_lifecycle(
    async_session: AsyncSession,
) -> None:
    """Pending → in_progress → success must round-trip without constraint
    violations. Confirms the status enum accepts every transition the
    orchestrator will drive."""
    client_id = await _make_client(async_session)
    result = await async_session.execute(
        text(
            "INSERT INTO platform_actions "
            "  (client_id, platform, action, idempotency_key, status) "
            "VALUES "
            "  (:cid, 'stripe', 'create_subscription', "
            "   :idk, 'pending') "
            "RETURNING id"
        ),
        {"cid": client_id, "idk": f"stripe:{client_id}"},
    )
    pa_id = result.scalar_one()

    for next_status in ("in_progress", "success"):
        await async_session.execute(
            text(
                "UPDATE platform_actions SET status = :s, "
                "  started_at = COALESCE(started_at, now()) "
                "WHERE id = :id"
            ),
            {"s": next_status, "id": pa_id},
        )

    final = await async_session.execute(
        text("SELECT status FROM platform_actions WHERE id = :id"),
        {"id": pa_id},
    )
    assert final.scalar_one() == "success"


@pytest.mark.db
async def test_invalid_platform_enum_is_rejected(
    async_session: AsyncSession,
) -> None:
    client_id = await _make_client(async_session)
    with pytest.raises(DBAPIError):
        await async_session.execute(
            text(
                "INSERT INTO platform_actions "
                "  (client_id, platform, action, idempotency_key, status) "
                "VALUES "
                "  (:cid, 'NOT_A_PLATFORM', 'noop', "
                "   :idk, 'pending')"
            ),
            {"cid": client_id, "idk": f"bad:{client_id}"},
        )
