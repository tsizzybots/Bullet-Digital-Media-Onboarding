"""Tests for role-based dependencies (S1-12).

Each test inserts a real users + sessions row into the live test session
(via the conftest rollback fixture) and overrides the FastAPI `get_session`
dependency to yield the same `AsyncSession`. The httpx AsyncClient then
issues HTTP requests carrying the matching `session` cookie, and the
assertions check the status code that the `require_founder` / `require_pd`
etc. gates produced.

httpx.AsyncClient + ASGITransport is used here (not fastapi.testclient
TestClient) because TestClient spins up its own anyio thread, and the
asyncpg connection inside async_session is bound to the pytest event
loop - mixing the two surfaces as "Future attached to a different loop".
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.db import get_session
from bullet_api.main import app


async def _seed_user_with_session(
    db: AsyncSession, *, role: str, expires_in: timedelta = timedelta(days=7)
) -> tuple[uuid.UUID, str]:
    """Insert a user + a session row; return (user_id, raw_token).

    Pass `expires_in=timedelta(seconds=-1)` to seed an already-expired
    session for the negative tests.
    """
    email = f"auth_{uuid.uuid4().hex[:8]}@example.com"
    user_id_result = await db.execute(
        text(
            "INSERT INTO users "
            "  (email, password_hash, full_name, role, email_confirmed) "
            "VALUES "
            "  (:e, 'argon2id$placeholder', 'T', :r, true) "
            "RETURNING id"
        ),
        {"e": email, "r": role},
    )
    user_id = user_id_result.scalar_one()

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(UTC) + expires_in
    await db.execute(
        text(
            "INSERT INTO sessions "
            "  (user_id, token_hash, expires_at) "
            "VALUES "
            "  (:u, :h, :exp)"
        ),
        {"u": user_id, "h": token_hash, "exp": expires_at},
    )
    return user_id, raw_token


@pytest_asyncio.fixture
async def client(
    async_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """AsyncClient against the ASGI app, with `get_session` overridden to
    yield the test's `async_session` so DB rows inserted by the test are
    visible to the request handler and rolled back on teardown."""

    async def _override() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = _override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_healthz_still_returns_ok() -> None:
    """Sanity: the public health probe is unaffected by S1-12 wiring."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_version_endpoint_returns_version_string() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/version")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["version"], str)
    assert body["version"]


@pytest.mark.db
async def test_admin_ping_returns_200_for_founder(
    async_session: AsyncSession, client: AsyncClient
) -> None:
    _, token = await _seed_user_with_session(async_session, role="founder")
    response = await client.get(
        "/admin/ping", cookies={"session": token}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "@" in body["email"]


@pytest.mark.db
async def test_admin_ping_returns_403_for_account_manager(
    async_session: AsyncSession, client: AsyncClient
) -> None:
    _, token = await _seed_user_with_session(
        async_session, role="account_manager"
    )
    response = await client.get(
        "/admin/ping", cookies={"session": token}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient role"


@pytest.mark.db
async def test_admin_ping_returns_401_without_session_cookie(
    client: AsyncClient,
) -> None:
    response = await client.get("/admin/ping")
    assert response.status_code == 401


@pytest.mark.db
async def test_admin_ping_returns_401_for_unknown_token(
    client: AsyncClient,
) -> None:
    response = await client.get(
        "/admin/ping", cookies={"session": "this-token-does-not-exist"}
    )
    assert response.status_code == 401


@pytest.mark.db
async def test_admin_ping_returns_401_for_expired_session(
    async_session: AsyncSession, client: AsyncClient
) -> None:
    """sessions.expires_at > now() is the live-session predicate; a
    timestamp in the past must not authenticate."""
    _, raw_token = await _seed_user_with_session(
        async_session,
        role="founder",
        expires_in=timedelta(hours=-1),
    )
    # Confirm the row was stored as expired - guard against clock skew
    # between local + Neon making the seed look "live" by accident.
    check = await async_session.execute(
        text(
            "SELECT expires_at > now() AS is_live FROM sessions "
            "WHERE token_hash = :h"
        ),
        {"h": hashlib.sha256(raw_token.encode()).hexdigest()},
    )
    assert check.scalar_one() is False
    response = await client.get(
        "/admin/ping", cookies={"session": raw_token}
    )
    assert response.status_code == 401


@pytest.mark.db
async def test_me_returns_profile_for_authenticated_user(
    async_session: AsyncSession, client: AsyncClient
) -> None:
    user_id, token = await _seed_user_with_session(
        async_session, role="performance_director"
    )
    response = await client.get("/me", cookies={"session": token})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user_id)
    assert body["role"] == "performance_director"


@pytest.mark.asyncio
async def test_openapi_document_is_openapi_3_1() -> None:
    """FastAPI emits OpenAPI 3.1.0+ by default since FastAPI 0.99 - guard
    against an accidental downgrade. Check structural invariants without
    pulling a schema validator dep just for this."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/openapi.json")
    assert response.status_code == 200
    doc = response.json()
    assert isinstance(doc.get("openapi"), str)
    assert doc["openapi"].startswith("3.1")
    assert "info" in doc and doc["info"].get("title") == "bullet-api"
    assert "paths" in doc
    assert "/healthz" in doc["paths"]
    assert "/version" in doc["paths"]
    assert "/me" in doc["paths"]
    assert "/admin/ping" in doc["paths"]
