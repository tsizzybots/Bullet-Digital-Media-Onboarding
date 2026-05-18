"""Tests for the clients table (S1-06).

Live-DB assertions for the PRD section 4.1 contract:
  * a `legal_entity='UK'` row inserts cleanly;
  * an invalid `current_step` enum value is rejected by Postgres;
  * the self-FK on `parent_client_id` accepts a real id and rejects a
    UUID that does not refer to an existing row.

Each test runs inside the per-test transaction provided by the
`async_session` fixture in `conftest.py`, which rolls back on teardown.
That keeps the Neon staging database free of test artefacts even when
tests assert success paths.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.db
async def test_insert_uk_client_succeeds(async_session: AsyncSession) -> None:
    """A minimal row with the required NOT NULL columns inserts cleanly."""
    result = await async_session.execute(
        text(
            "INSERT INTO clients "
            "  (email, legal_entity, current_step, step_entered_at) "
            "VALUES "
            "  ('uk-test@example.com', 'UK', 'sales_call', now()) "
            "RETURNING id"
        )
    )
    inserted_id = result.scalar_one()
    assert isinstance(inserted_id, uuid.UUID)


@pytest.mark.db
async def test_insert_invalid_current_step_is_rejected(
    async_session: AsyncSession,
) -> None:
    """`current_step` is a Postgres enum; an unknown label must fail."""
    with pytest.raises(DBAPIError):
        await async_session.execute(
            text(
                "INSERT INTO clients "
                "  (email, legal_entity, current_step, step_entered_at) "
                "VALUES "
                "  ('bad-enum@example.com', 'UK', "
                "   'NOT_A_REAL_STEP', now())"
            )
        )


@pytest.mark.db
async def test_self_fk_accepts_existing_parent(
    async_session: AsyncSession,
) -> None:
    """Returning-client second-site link: child row references a parent id."""
    parent_result = await async_session.execute(
        text(
            "INSERT INTO clients "
            "  (email, legal_entity, current_step, step_entered_at) "
            "VALUES "
            "  ('parent@example.com', 'UK', 'sales_call', now()) "
            "RETURNING id"
        )
    )
    parent_id = parent_result.scalar_one()

    child_result = await async_session.execute(
        text(
            "INSERT INTO clients "
            "  (email, legal_entity, current_step, step_entered_at, "
            "   parent_client_id) "
            "VALUES "
            "  ('child@example.com', 'UK', 'sales_call', now(), :pid) "
            "RETURNING id, parent_client_id"
        ),
        {"pid": parent_id},
    )
    child_id, linked_parent_id = child_result.one()
    assert linked_parent_id == parent_id
    assert child_id != parent_id


@pytest.mark.db
async def test_self_fk_rejects_unknown_parent(
    async_session: AsyncSession,
) -> None:
    """`parent_client_id` referencing a nonexistent row must violate FK."""
    bogus_parent = uuid.uuid4()
    with pytest.raises(DBAPIError):
        await async_session.execute(
            text(
                "INSERT INTO clients "
                "  (email, legal_entity, current_step, step_entered_at, "
                "   parent_client_id) "
                "VALUES "
                "  ('orphan@example.com', 'UK', 'sales_call', now(), "
                "   :pid)"
            ),
            {"pid": bogus_parent},
        )
