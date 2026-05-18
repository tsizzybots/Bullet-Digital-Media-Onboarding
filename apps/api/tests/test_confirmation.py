"""Tests for the email confirmation flow (S1-14)."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.auth.confirmation import (
    _TOKEN_SALT,
    CONFIRMATION_TOKEN_MAX_AGE,
    generate_confirmation_token,
    send_confirmation_email,
)
from bullet_api.config import get_settings
from bullet_api.db import get_session
from bullet_api.email import FakeEmailClient, get_email_client
from bullet_api.main import app


@pytest_asyncio.fixture
async def confirm_client(
    async_session: AsyncSession,
) -> AsyncIterator[tuple[AsyncClient, FakeEmailClient]]:
    """AsyncClient with DB session + email client overridden to a Fake."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    fake_email = FakeEmailClient()

    def _email_override() -> FakeEmailClient:
        return fake_email

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_email_client] = _email_override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            yield ac, fake_email
    finally:
        app.dependency_overrides.clear()


async def _make_user(
    db: AsyncSession,
    *,
    email: str,
    email_confirmed: bool = False,
) -> uuid.UUID:
    result = await db.execute(
        text(
            "INSERT INTO users "
            "  (email, password_hash, full_name, role, email_confirmed) "
            "VALUES (:e, 'argon2id$placeholder', 'Test', 'founder', :c) "
            "RETURNING id"
        ),
        {"e": email, "c": email_confirmed},
    )
    return result.scalar_one()


@pytest.mark.db
async def test_confirm_within_24h_flips_email_confirmed(
    async_session: AsyncSession,
    confirm_client: tuple[AsyncClient, FakeEmailClient],
) -> None:
    client, _ = confirm_client
    email = f"confirm_{uuid.uuid4().hex[:8]}@example.com"
    user_id = await _make_user(async_session, email=email, email_confirmed=False)

    token = generate_confirmation_token(user_id)
    response = await client.post(f"/auth/confirm/{token}")

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"

    row = await async_session.execute(
        text(
            "SELECT email_confirmed, email_confirmed_at IS NOT NULL "
            "FROM users WHERE id = :u"
        ),
        {"u": user_id},
    )
    confirmed, has_timestamp = row.one()
    assert confirmed is True
    assert has_timestamp is True


@pytest.mark.db
async def test_already_confirmed_user_is_idempotent(
    async_session: AsyncSession,
    confirm_client: tuple[AsyncClient, FakeEmailClient],
) -> None:
    client, _ = confirm_client
    email = f"confirm_idem_{uuid.uuid4().hex[:8]}@example.com"
    user_id = await _make_user(async_session, email=email, email_confirmed=True)

    token = generate_confirmation_token(user_id)
    response = await client.post(f"/auth/confirm/{token}")
    assert response.status_code == 200
    assert response.json()["status"] == "already_confirmed"


@pytest.mark.db
async def test_expired_token_returns_410(
    async_session: AsyncSession,
    confirm_client: tuple[AsyncClient, FakeEmailClient],
) -> None:
    """A token older than 24h returns 410 Gone.

    itsdangerous embeds the issued-at timestamp in the token and the
    serializer's `loads(max_age=...)` compares against it; setting the
    timestamp to "25 hours ago" simulates an expired token without
    waiting.
    """
    client, _ = confirm_client
    email = f"confirm_exp_{uuid.uuid4().hex[:8]}@example.com"
    user_id = await _make_user(async_session, email=email, email_confirmed=False)

    # Build a serializer with the same secret + salt, then sign with a
    # timestamp 25 hours in the past. itsdangerous' `dumps` doesn't expose
    # a custom-timestamp hook, but we can fabricate one via the internal
    # signer's `sign` API and then re-encode it as a TimedSerializer would.
    # Simpler: monkey-patch `time.time()` for the serializer's call.
    settings = get_settings()
    fake_serializer = URLSafeTimedSerializer(
        secret_key=settings.email_token_secret,
        salt=_TOKEN_SALT,
    )
    real_time = time.time
    max_age_seconds = int(CONFIRMATION_TOKEN_MAX_AGE.total_seconds())
    try:
        # Pretend it's 25h ago when issuing the token.
        time.time = lambda: real_time() - (max_age_seconds + 3600)
        expired_token = fake_serializer.dumps(str(user_id))
    finally:
        time.time = real_time

    response = await client.post(f"/auth/confirm/{expired_token}")
    assert response.status_code == 410
    assert "expired" in response.json()["detail"].lower()

    # Confirm the row was NOT flipped.
    row = await async_session.execute(
        text("SELECT email_confirmed FROM users WHERE id = :u"),
        {"u": user_id},
    )
    assert row.scalar_one() is False


@pytest.mark.db
async def test_invalid_token_returns_400(
    confirm_client: tuple[AsyncClient, FakeEmailClient],
) -> None:
    client, _ = confirm_client
    response = await client.post("/auth/confirm/this-is-not-a-valid-token")
    assert response.status_code == 400


@pytest.mark.db
async def test_resend_confirmation_sends_via_email_client(
    async_session: AsyncSession,
    confirm_client: tuple[AsyncClient, FakeEmailClient],
) -> None:
    """The Resend client is mockable: the FakeEmailClient should record
    one outbound message containing the confirmation link."""
    client, fake_email = confirm_client
    email = f"confirm_resend_{uuid.uuid4().hex[:8]}@example.com"
    await _make_user(async_session, email=email, email_confirmed=False)

    response = await client.post(
        "/auth/resend-confirmation", json={"email": email}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "sent_if_unconfirmed"

    assert len(fake_email.sent) == 1
    sent = fake_email.sent[0]
    assert sent.to == email
    assert "confirm" in sent.subject.lower()
    # The signed token is in the link body; assert the base URL is there.
    base = get_settings().email_confirmation_base_url.rstrip("/")
    assert base in sent.html


@pytest.mark.db
async def test_resend_for_already_confirmed_user_sends_nothing(
    async_session: AsyncSession,
    confirm_client: tuple[AsyncClient, FakeEmailClient],
) -> None:
    """An already-confirmed user must not receive another confirmation."""
    client, fake_email = confirm_client
    email = f"confirm_done_{uuid.uuid4().hex[:8]}@example.com"
    await _make_user(async_session, email=email, email_confirmed=True)

    response = await client.post(
        "/auth/resend-confirmation", json={"email": email}
    )
    assert response.status_code == 200
    assert len(fake_email.sent) == 0


@pytest.mark.db
async def test_resend_for_unknown_email_returns_same_status_no_send(
    confirm_client: tuple[AsyncClient, FakeEmailClient],
) -> None:
    """Endpoint must not leak existence: unknown email gets the same
    response shape as the unconfirmed case."""
    client, fake_email = confirm_client
    response = await client.post(
        "/auth/resend-confirmation",
        json={"email": "nobody@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "sent_if_unconfirmed"
    assert len(fake_email.sent) == 0


@pytest.mark.db
async def test_send_confirmation_email_uses_injected_client(
    async_session: AsyncSession,
) -> None:
    """Unit-style assertion: `send_confirmation_email()` calls into the
    injected `EmailClient.send()` and returns the provider message id."""
    email = f"confirm_unit_{uuid.uuid4().hex[:8]}@example.com"
    user_id = await _make_user(async_session, email=email)
    fake = FakeEmailClient()

    message_id = await send_confirmation_email(
        user_id=user_id, email=email, email_client=fake
    )
    assert message_id.startswith("fake-")
    assert len(fake.sent) == 1
    assert fake.sent[0].to == email
