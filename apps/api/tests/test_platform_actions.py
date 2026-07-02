"""Unit tests for the shared platform_actions recorder's atomic stale-reclaim.

`reclaim_stale_action` is the compare-and-swap that closes the double-reclaim
window: two overlapping runs that both observe the same stale `started_at` must
not both proceed. Only the run whose CAS matches wins; the other backs off.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.worker.platform_actions import reclaim_stale_action


async def _seed_client(session: AsyncSession) -> uuid.UUID:
    return (
        await session.execute(
            text(
                "INSERT INTO clients (email, legal_entity, current_step, step_entered_at) "
                "VALUES (:e, 'X Ltd', 'signed', now()) RETURNING id"
            ),
            {"e": f"s+{uuid.uuid4().hex[:6]}@x.com"},
        )
    ).scalar_one()


async def _seed_in_progress(session: AsyncSession, client_id: uuid.UUID, *, age_sql: str):
    return (
        (
            await session.execute(
                text(
                    "INSERT INTO platform_actions "
                    "(client_id, platform, action, idempotency_key, status, started_at) "
                    f"VALUES (:cid, 'openai', 'x', :k, 'in_progress', {age_sql}) "
                    "RETURNING id, started_at"
                ),
                {"cid": client_id, "k": f"k-{uuid.uuid4()}"},
            )
        )
        .mappings()
        .one()
    )


@pytest.mark.db
async def test_reclaim_wins_once_then_loses(async_session: AsyncSession) -> None:
    """The first reclaim with the observed started_at wins; a second reclaim with
    the SAME (now stale) observed value loses - the row advanced to now()."""
    client_id = await _seed_client(async_session)
    row = await _seed_in_progress(async_session, client_id, age_sql="now() - interval '20 minutes'")
    action_id, seen = row["id"], row["started_at"]
    await async_session.commit()

    won = await reclaim_stale_action(async_session, action_id=action_id, seen_started_at=seen)
    await async_session.commit()
    assert won is True

    lost = await reclaim_stale_action(async_session, action_id=action_id, seen_started_at=seen)
    await async_session.commit()
    assert lost is False


@pytest.mark.db
async def test_reclaim_returns_false_for_non_in_progress(async_session: AsyncSession) -> None:
    """A reclaim must not resurrect an action that is no longer in_progress
    (e.g. a concurrent run already completed it)."""
    client_id = await _seed_client(async_session)
    row = await _seed_in_progress(async_session, client_id, age_sql="now() - interval '20 minutes'")
    action_id, seen = row["id"], row["started_at"]
    await async_session.execute(
        text("UPDATE platform_actions SET status = 'success' WHERE id = :id"), {"id": action_id}
    )
    await async_session.commit()

    won = await reclaim_stale_action(async_session, action_id=action_id, seen_started_at=seen)
    assert won is False
