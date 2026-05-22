"""Tests for POST /auth/logout and session lifecycle (S1-15)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.db import get_session
from bullet_api.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _make_user(
    db: AsyncSession,
    *,
    email: str,
    password: str = "test-password",
    role: str = "founder",
    email_confirmed: bool = True,
) -> uuid.UUID:
    hasher = PasswordHasher()
    password_hash = hasher.hash(password)
    result = await db.execute(
        text(
            "INSERT INTO users "
            "  (email, password_hash, full_name, role, email_confirmed) "
            "VALUES (:e, :h, 'Test User', :r, :c) "
            "RETURNING id"
        ),
        {
            "e": email,
            "h": password_hash,
            "r": role,
            "c": email_confirmed,
        },
    )
    return result.scalar_one()


async def _make_session(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    raw_token: str,
    expires_at: datetime | None = None,
) -> None:
    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(days=7)
    await db.execute(
        text(
            "INSERT INTO sessions (user_id, token_hash, expires_at, ip, user_agent) "
            "VALUES (:u, :h, :e, cast(:ip AS inet), :ua)"
        ),
        {
            "u": user_id,
            "h": _hash_token(raw_token),
            "e": expires_at,
            "ip": "127.0.0.1",
            "ua": "test-agent",
        },
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_client(
    async_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """AsyncClient with ``get_session`` overridden to use the test session."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = _session_override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_logout_deletes_session_row(
    async_session: AsyncSession,
    session_client: AsyncClient,
) -> None:
    """Successful logout removes the session row from the DB."""
    email = f"logout_delete_{uuid.uuid4().hex[:8]}@example.com"
    user_id = await _make_user(async_session, email=email)
    raw_token = f"test-token-{uuid.uuid4().hex}"
    await _make_session(async_session, user_id=user_id, raw_token=raw_token)

    response = await session_client.post(
        "/auth/logout",
        cookies={"session": raw_token},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}

    # The session row must be gone.
    count = await async_session.execute(
        text("SELECT count(*) FROM sessions WHERE token_hash = :h"),
        {"h": _hash_token(raw_token)},
    )
    assert count.scalar_one() == 0


@pytest.mark.db
async def test_same_cookie_rejected_after_logout(
    async_session: AsyncSession,
    session_client: AsyncClient,
) -> None:
    """After logout, the same cookie value must be refused by GET /me."""
    email = f"logout_rejected_{uuid.uuid4().hex[:8]}@example.com"
    user_id = await _make_user(async_session, email=email)
    raw_token = f"test-token-{uuid.uuid4().hex}"
    await _make_session(async_session, user_id=user_id, raw_token=raw_token)

    # Logout first.
    logout_resp = await session_client.post(
        "/auth/logout",
        cookies={"session": raw_token},
    )
    assert logout_resp.status_code == 200

    # Same cookie must now be rejected.
    me_resp = await session_client.get(
        "/me",
        cookies={"session": raw_token},
    )
    assert me_resp.status_code == 401


@pytest.mark.db
async def test_expired_session_rejected(
    async_session: AsyncSession,
    session_client: AsyncClient,
) -> None:
    """A session whose expires_at is in the past must 401 on GET /me."""
    email = f"expired_session_{uuid.uuid4().hex[:8]}@example.com"
    user_id = await _make_user(async_session, email=email)
    raw_token = f"test-token-{uuid.uuid4().hex}"
    expired_at = datetime.now(UTC) - timedelta(minutes=1)
    await _make_session(
        async_session,
        user_id=user_id,
        raw_token=raw_token,
        expires_at=expired_at,
    )

    response = await session_client.get(
        "/me",
        cookies={"session": raw_token},
    )
    assert response.status_code == 401


@pytest.mark.db
async def test_logout_requires_auth(
    session_client: AsyncClient,
) -> None:
    """POST /auth/logout with no cookie must return 401."""
    response = await session_client.post("/auth/logout")
    assert response.status_code == 401
