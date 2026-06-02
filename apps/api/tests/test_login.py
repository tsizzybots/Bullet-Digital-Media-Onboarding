"""Tests for POST /auth/login (S1-13)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.auth.brute_force import BruteForceTracker, get_tracker
from bullet_api.auth.dependencies import SESSION_COOKIE_NAME
from bullet_api.db import get_session
from bullet_api.main import app

# Shared fixed-clock state used by the dependency-overridden tracker.
_CLOCK_NOW: list[datetime] = [datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)]


def _set_clock(when: datetime) -> None:
    _CLOCK_NOW[0] = when


def _advance(delta: timedelta) -> None:
    _CLOCK_NOW[0] = _CLOCK_NOW[0] + delta


@pytest_asyncio.fixture
async def login_client(
    async_session: AsyncSession,
) -> AsyncIterator[tuple[AsyncClient, BruteForceTracker]]:
    """AsyncClient with `get_session` + `get_tracker` overridden so each
    test gets a fresh in-memory tracker driven by `_CLOCK_NOW` and shares
    the test's DB session (savepoint-wrapped by the conftest fixture)."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    _CLOCK_NOW[0] = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
    tracker = BruteForceTracker(clock=lambda: _CLOCK_NOW[0])

    def _tracker_override() -> BruteForceTracker:
        return tracker

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_tracker] = _tracker_override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, tracker
    finally:
        app.dependency_overrides.clear()


async def _make_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    role: str = "founder",
    email_confirmed: bool = True,
) -> uuid.UUID:
    hasher = PasswordHasher()
    password_hash = hasher.hash(password)
    result = await db.execute(
        text(
            "INSERT INTO users "
            "  (email, password_hash, full_name, role, email_confirmed) "
            "VALUES (:e, :h, 'Test', :r, :c) "
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


@pytest.mark.db
async def test_valid_credentials_issue_session_cookie(
    async_session: AsyncSession,
    login_client: tuple[AsyncClient, BruteForceTracker],
) -> None:
    client, _ = login_client
    email = f"login_ok_{uuid.uuid4().hex[:8]}@example.com"
    await _make_user(async_session, email=email, password="correct-horse")

    response = await client.post(
        "/auth/login",
        json={"email": email, "password": "correct-horse"},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}

    # Cookie shape: HttpOnly + Secure + SameSite=Lax per S1-15 plan.
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME + "=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=Lax" in set_cookie or "samesite=lax" in set_cookie.lower()

    # And the session row must exist in the DB.
    count = await async_session.execute(
        text(
            "SELECT count(*) FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE u.email = :e AND s.expires_at > now()"
        ),
        {"e": email},
    )
    assert count.scalar_one() == 1


@pytest.mark.db
async def test_invalid_password_returns_401(
    async_session: AsyncSession,
    login_client: tuple[AsyncClient, BruteForceTracker],
) -> None:
    client, _ = login_client
    email = f"login_bad_{uuid.uuid4().hex[:8]}@example.com"
    await _make_user(async_session, email=email, password="right")

    response = await client.post(
        "/auth/login",
        json={"email": email, "password": "wrong"},
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


@pytest.mark.db
async def test_unknown_email_returns_401(
    login_client: tuple[AsyncClient, BruteForceTracker],
) -> None:
    client, _ = login_client
    response = await client.post(
        "/auth/login",
        json={"email": "nobody@example.com", "password": "anything"},
        headers={"X-Forwarded-For": "10.0.0.3"},
    )
    assert response.status_code == 401


@pytest.mark.db
async def test_unconfirmed_email_returns_403(
    async_session: AsyncSession,
    login_client: tuple[AsyncClient, BruteForceTracker],
) -> None:
    """The S1-14 contract: a user with `email_confirmed=false` cannot log
    in even with the right password."""
    client, _ = login_client
    email = f"login_unconfirmed_{uuid.uuid4().hex[:8]}@example.com"
    await _make_user(
        async_session,
        email=email,
        password="right",
        email_confirmed=False,
    )

    response = await client.post(
        "/auth/login",
        json={"email": email, "password": "right"},
        headers={"X-Forwarded-For": "10.0.0.4"},
    )
    assert response.status_code == 403
    assert "confirm" in response.json()["detail"].lower()


@pytest.mark.db
async def test_five_failures_lock_out_sixth_attempt_with_valid_creds(
    async_session: AsyncSession,
    login_client: tuple[AsyncClient, BruteForceTracker],
) -> None:
    """5 failed attempts in the window trigger a lockout; the 6th
    attempt returns 429 even with valid credentials."""
    client, _ = login_client
    email = f"login_lockout_{uuid.uuid4().hex[:8]}@example.com"
    await _make_user(async_session, email=email, password="right")

    # Five wrong attempts from the same source IP.
    for _ in range(5):
        bad = await client.post(
            "/auth/login",
            json={"email": email, "password": "wrong"},
            headers={"X-Forwarded-For": "10.0.0.5"},
        )
        assert bad.status_code == 401

    # Sixth attempt - now with the *correct* password - must 429.
    locked = await client.post(
        "/auth/login",
        json={"email": email, "password": "right"},
        headers={"X-Forwarded-For": "10.0.0.5"},
    )
    assert locked.status_code == 429
    assert "too many" in locked.json()["detail"].lower()


@pytest.mark.db
async def test_lockout_expires_after_15_minutes(
    async_session: AsyncSession,
    login_client: tuple[AsyncClient, BruteForceTracker],
) -> None:
    """After the window elapses, the same IP can try again. Drives the
    injected clock forward instead of `time.sleep(900)`."""
    client, _ = login_client
    email = f"login_expire_{uuid.uuid4().hex[:8]}@example.com"
    await _make_user(async_session, email=email, password="right")

    for _ in range(5):
        await client.post(
            "/auth/login",
            json={"email": email, "password": "wrong"},
            headers={"X-Forwarded-For": "10.0.0.6"},
        )
    # Confirm we are locked out at this clock-time.
    locked = await client.post(
        "/auth/login",
        json={"email": email, "password": "right"},
        headers={"X-Forwarded-For": "10.0.0.6"},
    )
    assert locked.status_code == 429

    # Advance the clock past the 15-minute window.
    _advance(timedelta(minutes=16))

    # Now the same IP + valid creds must succeed.
    ok = await client.post(
        "/auth/login",
        json={"email": email, "password": "right"},
        headers={"X-Forwarded-For": "10.0.0.6"},
    )
    assert ok.status_code == 200


@pytest.mark.db
async def test_lockout_is_per_ip(
    async_session: AsyncSession,
    login_client: tuple[AsyncClient, BruteForceTracker],
) -> None:
    """A locked-out IP must not lock out a different IP. Confirms the
    tracker keys on caller IP, not user or globally."""
    client, _ = login_client
    email = f"login_peripip_{uuid.uuid4().hex[:8]}@example.com"
    await _make_user(async_session, email=email, password="right")

    for _ in range(5):
        await client.post(
            "/auth/login",
            json={"email": email, "password": "wrong"},
            headers={"X-Forwarded-For": "10.0.0.7"},
        )
    # Same email, different IP - must NOT be locked out.
    ok = await client.post(
        "/auth/login",
        json={"email": email, "password": "right"},
        headers={"X-Forwarded-For": "10.0.0.8"},
    )
    assert ok.status_code == 200
