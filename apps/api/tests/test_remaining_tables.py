"""Tests for the documents/research_results/client_assets/users/sessions/
audit_log tables (S1-10) and the deferred client_knowledge FK."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def _make_user(session: AsyncSession, *, email: str) -> uuid.UUID:
    result = await session.execute(
        text(
            "INSERT INTO users "
            "  (email, password_hash, full_name, role) "
            "VALUES "
            "  (:e, 'argon2id$placeholder', 'Test User', 'founder') "
            "RETURNING id"
        ),
        {"e": email},
    )
    return result.scalar_one()


async def _make_client(session: AsyncSession) -> uuid.UUID:
    result = await session.execute(
        text(
            "INSERT INTO clients "
            "  (email, legal_entity, current_step, step_entered_at) "
            "VALUES "
            "  ('s1-10@example.com', 'UK', 'sales_call', now()) "
            "RETURNING id"
        )
    )
    return result.scalar_one()


@pytest.mark.db
async def test_users_email_unique_enforced(
    async_session: AsyncSession,
) -> None:
    """Two users sharing an email must violate the UNIQUE constraint."""
    await _make_user(async_session, email="duplicate@example.com")
    with pytest.raises((IntegrityError, DBAPIError)):
        await _make_user(async_session, email="duplicate@example.com")


@pytest.mark.db
async def test_users_email_unique_is_case_insensitive(
    async_session: AsyncSession,
) -> None:
    """`email` is citext so 'X@y.com' and 'x@Y.COM' collide under UNIQUE."""
    await _make_user(async_session, email="Mixed@Example.com")
    with pytest.raises((IntegrityError, DBAPIError)):
        await _make_user(async_session, email="MIXED@example.COM")


@pytest.mark.db
async def test_invalid_user_role_is_rejected(
    async_session: AsyncSession,
) -> None:
    with pytest.raises(DBAPIError):
        await async_session.execute(
            text(
                "INSERT INTO users "
                "  (email, password_hash, full_name, role) "
                "VALUES "
                "  ('badrole@example.com', 'h', 'B', 'NOT_A_ROLE')"
            )
        )


@pytest.mark.db
async def test_sessions_token_hash_unique_enforced(
    async_session: AsyncSession,
) -> None:
    user_id = await _make_user(async_session, email="sess@example.com")
    await async_session.execute(
        text(
            "INSERT INTO sessions "
            "  (user_id, token_hash, expires_at) "
            "VALUES "
            "  (:uid, 'tok_dup_hash', now() + interval '7 days')"
        ),
        {"uid": user_id},
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await async_session.execute(
            text(
                "INSERT INTO sessions "
                "  (user_id, token_hash, expires_at) "
                "VALUES "
                "  (:uid, 'tok_dup_hash', now() + interval '7 days')"
            ),
            {"uid": user_id},
        )


@pytest.mark.db
async def test_invalid_document_kind_is_rejected(
    async_session: AsyncSession,
) -> None:
    client_id = await _make_client(async_session)
    with pytest.raises(DBAPIError):
        await async_session.execute(
            text("INSERT INTO documents (client_id, kind) VALUES (:cid, 'NOT_A_KIND')"),
            {"cid": client_id},
        )


@pytest.mark.db
async def test_invalid_client_asset_status_is_rejected(
    async_session: AsyncSession,
) -> None:
    client_id = await _make_client(async_session)
    with pytest.raises(DBAPIError):
        await async_session.execute(
            text(
                "INSERT INTO client_assets "
                "  (client_id, asset_type, status) "
                "VALUES "
                "  (:cid, 'headshot', 'NOT_A_STATUS')"
            ),
            {"cid": client_id},
        )


@pytest.mark.db
async def test_invalid_research_result_kind_is_rejected(
    async_session: AsyncSession,
) -> None:
    client_id = await _make_client(async_session)
    with pytest.raises(DBAPIError):
        await async_session.execute(
            text(
                "INSERT INTO research_results "
                "  (client_id, kind, payload) "
                "VALUES "
                "  (:cid, 'NOT_A_KIND', cast('{}' AS jsonb))"
            ),
            {"cid": client_id},
        )


@pytest.mark.db
async def test_invalid_client_asset_type_is_rejected(
    async_session: AsyncSession,
) -> None:
    client_id = await _make_client(async_session)
    with pytest.raises(DBAPIError):
        await async_session.execute(
            text(
                "INSERT INTO client_assets "
                "  (client_id, asset_type, status) "
                "VALUES "
                "  (:cid, 'NOT_A_TYPE', 'required')"
            ),
            {"cid": client_id},
        )


@pytest.mark.db
async def test_client_knowledge_captured_by_fk_to_users(
    async_session: AsyncSession,
) -> None:
    """The deferred FK added in 0006 must reject a captured_by UUID that
    does not refer to an existing users row, and accept one that does."""
    client_id = await _make_client(async_session)
    user_id = await _make_user(async_session, email="cap@example.com")

    # Happy path: known user_id is accepted.
    await async_session.execute(
        text(
            "INSERT INTO client_knowledge "
            "  (client_id, source, key, value, captured_by) "
            "VALUES "
            "  (:cid, 'manual', 'note', cast('{}' AS jsonb), :uid)"
        ),
        {"cid": client_id, "uid": user_id},
    )

    # Negative path: random UUID is rejected by the FK constraint.
    bogus = uuid.uuid4()
    with pytest.raises((IntegrityError, DBAPIError)):
        await async_session.execute(
            text(
                "INSERT INTO client_knowledge "
                "  (client_id, source, key, value, captured_by) "
                "VALUES "
                "  (:cid, 'manual', 'note', cast('{}' AS jsonb), :u)"
            ),
            {"cid": client_id, "u": bogus},
        )
