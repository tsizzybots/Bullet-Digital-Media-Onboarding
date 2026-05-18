"""Tests for the client_knowledge table (S1-07)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.db
async def test_gin_index_on_value_exists(async_session: AsyncSession) -> None:
    """The GIN-on-jsonb index that supports `value @> '...'` lookups must be
    present after upgrading; without it `value` queries fall back to a seq
    scan and the dashboard becomes unusable at the row counts we expect."""
    result = await async_session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'client_knowledge' "
            "  AND indexname = 'ix_client_knowledge_value_gin'"
        )
    )
    definition = result.scalar_one()
    assert "using gin" in definition.lower()
    assert "jsonb_path_ops" in definition


@pytest.mark.db
async def test_ivfflat_index_on_embedding_exists(
    async_session: AsyncSession,
) -> None:
    """The ANN index on the vector(1536) embedding column must be present."""
    result = await async_session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'client_knowledge' "
            "  AND indexname = 'ix_client_knowledge_embedding_ivfflat'"
        )
    )
    definition = result.scalar_one()
    assert "using ivfflat" in definition.lower()
    assert "vector_cosine_ops" in definition


@pytest.mark.db
async def test_insert_with_valid_embedding_succeeds(
    async_session: AsyncSession,
) -> None:
    """A row with a real 1536-dim vector embedding and jsonb value inserts
    cleanly. The vector literal is `'[0.0, 0.0, ...]'::vector(1536)`."""
    parent_result = await async_session.execute(
        text(
            "INSERT INTO clients "
            "  (email, legal_entity, current_step, step_entered_at) "
            "VALUES "
            "  ('kn-test@example.com', 'UK', 'sales_call', now()) "
            "RETURNING id"
        )
    )
    client_id = parent_result.scalar_one()

    # 1536 dimensions; cast to vector(1536) so Postgres accepts the literal.
    vector_literal = "[" + ", ".join(["0.0"] * 1536) + "]"
    insert_result = await async_session.execute(
        text(
            "INSERT INTO client_knowledge "
            "  (client_id, source, key, value, value_text, embedding) "
            "VALUES "
            "  (:cid, 'sales_call', 'business_goals', "
            "   cast(:val AS jsonb), "
            "   'grow MRR', "
            "   cast(:vec AS vector(1536))) "
            "RETURNING id, embedding IS NOT NULL AS has_vec"
        ),
        {
            "cid": client_id,
            "val": '{"goal": "grow MRR"}',
            "vec": vector_literal,
        },
    )
    knowledge_id, has_vec = insert_result.one()
    assert knowledge_id is not None
    assert has_vec is True


@pytest.mark.db
async def test_invalid_source_enum_is_rejected(
    async_session: AsyncSession,
) -> None:
    """`source` enum must reject unknown labels."""
    parent_result = await async_session.execute(
        text(
            "INSERT INTO clients "
            "  (email, legal_entity, current_step, step_entered_at) "
            "VALUES "
            "  ('kn-enum@example.com', 'UK', 'sales_call', now()) "
            "RETURNING id"
        )
    )
    client_id = parent_result.scalar_one()

    with pytest.raises(DBAPIError):
        await async_session.execute(
            text(
                "INSERT INTO client_knowledge "
                "  (client_id, source, key, value) "
                "VALUES "
                "  (:cid, 'NOT_A_SOURCE', 'k', cast('{}' AS jsonb))"
            ),
            {"cid": client_id},
        )
