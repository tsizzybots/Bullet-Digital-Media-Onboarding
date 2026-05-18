"""Tests for the onboarding_events table (S1-08)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.db
async def test_insert_event_succeeds(async_session: AsyncSession) -> None:
    """Minimum-shape webhook ingestion row."""
    result = await async_session.execute(
        text(
            "INSERT INTO onboarding_events "
            "  (event_type, external_id, payload) "
            "VALUES "
            "  ('pandadoc.signed', 'doc_abc123', '{\"foo\": 1}'::jsonb) "
            "RETURNING id"
        )
    )
    assert result.scalar_one() is not None


@pytest.mark.db
async def test_duplicate_event_type_external_id_is_rejected(
    async_session: AsyncSession,
) -> None:
    """`(event_type, external_id)` UNIQUE keeps webhook retries idempotent.

    PandaDoc / GHL / Stripe replays must hit the existing row, not create
    a parallel one - otherwise the orchestrator fires duplicate fan-outs.
    """
    await async_session.execute(
        text(
            "INSERT INTO onboarding_events "
            "  (event_type, external_id, payload) "
            "VALUES "
            "  ('pandadoc.signed', 'doc_duplicate', '{}'::jsonb)"
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await async_session.execute(
            text(
                "INSERT INTO onboarding_events "
                "  (event_type, external_id, payload) "
                "VALUES "
                "  ('pandadoc.signed', 'doc_duplicate', '{}'::jsonb)"
            )
        )


@pytest.mark.db
async def test_null_external_id_does_not_collide(
    async_session: AsyncSession,
) -> None:
    """Two rows with the same event_type but NULL external_id must coexist.

    Postgres treats NULLs as distinct under UNIQUE constraints, which is
    the behaviour we rely on for events whose source platform doesn't
    issue a stable id (e.g. internal synthetic events).
    """
    for _ in range(2):
        await async_session.execute(
            text(
                "INSERT INTO onboarding_events "
                "  (event_type, external_id, payload) "
                "VALUES "
                "  ('internal.synthetic', NULL, '{}'::jsonb)"
            )
        )
    count = await async_session.execute(
        text(
            "SELECT count(*) FROM onboarding_events "
            "WHERE event_type = 'internal.synthetic' AND external_id IS NULL"
        )
    )
    assert count.scalar_one() >= 2
