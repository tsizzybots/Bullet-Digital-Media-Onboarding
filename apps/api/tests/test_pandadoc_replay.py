"""Integration tests for the PandaDoc manual replay endpoint (S1-24).

`POST /admin/pandadoc/replay/{document_id}` fetches the document from the
PandaDoc API (a `FakePandaDocClient` here) and pushes it through the SAME
persist + emit path as the S1-22 webhook. These hit Postgres (the
`onboarding_events` upsert + the sessions/users role lookup) via the
transactional `async_session` fixture, so they are all `@pytest.mark.db`
and skip when no DATABASE_URL is reachable. The PandaDoc client and event
emitter are overridden with test doubles so no network call is made and
emitted events are captured rather than sent.
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
from bullet_api.pandadoc import FakePandaDocClient, PandaDocDocument, get_pandadoc_client
from bullet_api.worker import PANDADOC_SIGNED_EVENT, FakeEventEmitter, get_event_emitter


async def _seed_user_with_session(
    db: AsyncSession, *, role: str, expires_in: timedelta = timedelta(days=7)
) -> tuple[uuid.UUID, str]:
    """Insert a user + a live session row; return (user_id, raw_token).

    Mirrors the helper in test_auth_dependencies.py (kept self-contained so
    this test module does not import another test module)."""
    email = f"replay_{uuid.uuid4().hex[:8]}@example.com"
    user_id = (
        await db.execute(
            text(
                "INSERT INTO users "
                "  (email, password_hash, full_name, role, email_confirmed) "
                "VALUES (:e, 'argon2id$placeholder', 'T', :r, true) "
                "RETURNING id"
            ),
            {"e": email, "r": role},
        )
    ).scalar_one()

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await db.execute(
        text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:u, :h, :exp)"),
        {"u": user_id, "h": token_hash, "exp": datetime.now(UTC) + expires_in},
    )
    return user_id, raw_token


@pytest_asyncio.fixture
async def replay_client(
    async_session: AsyncSession,
) -> AsyncIterator[tuple[AsyncClient, FakeEventEmitter, FakePandaDocClient]]:
    """AsyncClient with get_session, get_pandadoc_client, and get_event_emitter
    overridden to test doubles. Tests preload documents onto the returned
    FakePandaDocClient and read FakeEventEmitter.sent for assertions."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    fake_emitter = FakeEventEmitter()
    fake_client = FakePandaDocClient()

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_event_emitter] = lambda: fake_emitter
    app.dependency_overrides[get_pandadoc_client] = lambda: fake_client
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, fake_emitter, fake_client
    finally:
        app.dependency_overrides.clear()


async def _count_events(db: AsyncSession, doc_id: str) -> int:
    return (
        await db.execute(
            text(
                "SELECT count(*) FROM onboarding_events "
                "WHERE event_type = :et AND external_id = :eid"
            ),
            {"et": PANDADOC_SIGNED_EVENT, "eid": doc_id},
        )
    ).scalar_one()


def _completed(doc_id: str) -> PandaDocDocument:
    return PandaDocDocument(id=doc_id, name="Agreement", status="document.completed")


@pytest.mark.db
async def test_replay_known_signed_document_persists_and_emits(
    async_session: AsyncSession,
    replay_client: tuple[AsyncClient, FakeEventEmitter, FakePandaDocClient],
) -> None:
    """Card test 1: replay of a known signed document creates the event row
    and triggers Inngest."""
    client, fake_emitter, fake_pandadoc = replay_client
    _, token = await _seed_user_with_session(async_session, role="founder")
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    fake_pandadoc.documents[doc_id] = _completed(doc_id)

    response = await client.post(f"/admin/pandadoc/replay/{doc_id}", cookies={"session": token})

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "events": 1}

    row = await async_session.execute(
        text(
            "SELECT count(*), bool_and(verified_at IS NOT NULL) FROM onboarding_events "
            "WHERE event_type = 'pandadoc.signed' AND external_id = :doc_id"
        ),
        {"doc_id": doc_id},
    )
    count, all_verified = row.one()
    assert count == 1
    assert all_verified is True

    assert len(fake_emitter.sent) == 1
    name, data = fake_emitter.sent[0]
    assert name == PANDADOC_SIGNED_EVENT
    assert data["document_id"] == doc_id
    assert data["onboarding_event_id"]


@pytest.mark.db
async def test_replay_engineer_allowed(
    async_session: AsyncSession,
    replay_client: tuple[AsyncClient, FakeEventEmitter, FakePandaDocClient],
) -> None:
    """The second allowed role (izzyagents_engineer) can also replay."""
    client, fake_emitter, fake_pandadoc = replay_client
    _, token = await _seed_user_with_session(async_session, role="izzyagents_engineer")
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    fake_pandadoc.documents[doc_id] = _completed(doc_id)

    response = await client.post(f"/admin/pandadoc/replay/{doc_id}", cookies={"session": token})

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "events": 1}
    assert len(fake_emitter.sent) == 1


@pytest.mark.db
async def test_replay_unknown_document_returns_404(
    async_session: AsyncSession,
    replay_client: tuple[AsyncClient, FakeEventEmitter, FakePandaDocClient],
) -> None:
    """Card test 2: replay of an unknown document returns 404 (the fake
    raises PandaDocNotFound for an id it was not preloaded with), with no row
    and no emit."""
    client, fake_emitter, _fake_pandadoc = replay_client
    _, token = await _seed_user_with_session(async_session, role="founder")
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"  # never preloaded -> PandaDocNotFound

    response = await client.post(f"/admin/pandadoc/replay/{doc_id}", cookies={"session": token})

    assert response.status_code == 404
    assert await _count_events(async_session, doc_id) == 0
    assert fake_emitter.sent == []


@pytest.mark.db
async def test_replay_account_manager_forbidden(
    async_session: AsyncSession,
    replay_client: tuple[AsyncClient, FakeEventEmitter, FakePandaDocClient],
) -> None:
    """Card test 3: an account_manager gets 403 - even with a valid signed
    document loaded, the role gate fires before the fetch, so no row, no emit."""
    client, fake_emitter, fake_pandadoc = replay_client
    _, token = await _seed_user_with_session(async_session, role="account_manager")
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    fake_pandadoc.documents[doc_id] = _completed(doc_id)

    response = await client.post(f"/admin/pandadoc/replay/{doc_id}", cookies={"session": token})

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient role"
    assert await _count_events(async_session, doc_id) == 0
    assert fake_emitter.sent == []


@pytest.mark.db
async def test_replay_no_session_returns_401(
    replay_client: tuple[AsyncClient, FakeEventEmitter, FakePandaDocClient],
) -> None:
    """No session cookie -> 401, before any fetch / row / emit."""
    client, fake_emitter, fake_pandadoc = replay_client
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    fake_pandadoc.documents[doc_id] = _completed(doc_id)

    response = await client.post(f"/admin/pandadoc/replay/{doc_id}")

    assert response.status_code == 401
    assert fake_emitter.sent == []


@pytest.mark.db
async def test_replay_is_idempotent(
    async_session: AsyncSession,
    replay_client: tuple[AsyncClient, FakeEventEmitter, FakePandaDocClient],
) -> None:
    """Replaying the same signed document twice persists one row and emits
    once; the second replay is a no-op duplicate (ON CONFLICT DO NOTHING)."""
    client, fake_emitter, fake_pandadoc = replay_client
    _, token = await _seed_user_with_session(async_session, role="founder")
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    fake_pandadoc.documents[doc_id] = _completed(doc_id)

    first = await client.post(f"/admin/pandadoc/replay/{doc_id}", cookies={"session": token})
    assert first.status_code == 200
    assert first.json() == {"status": "accepted", "events": 1}

    second = await client.post(f"/admin/pandadoc/replay/{doc_id}", cookies={"session": token})
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate", "events": 0}

    assert await _count_events(async_session, doc_id) == 1
    assert len(fake_emitter.sent) == 1


@pytest.mark.db
async def test_replay_found_but_unsigned_is_ignored(
    async_session: AsyncSession,
    replay_client: tuple[AsyncClient, FakeEventEmitter, FakePandaDocClient],
) -> None:
    """A document that exists but is not yet completed is acked and ignored
    (no row, no emit), mirroring the webhook's non-signed-event behaviour."""
    client, fake_emitter, fake_pandadoc = replay_client
    _, token = await _seed_user_with_session(async_session, role="founder")
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    fake_pandadoc.documents[doc_id] = PandaDocDocument(
        id=doc_id, name="Agreement", status="document.sent"
    )

    response = await client.post(f"/admin/pandadoc/replay/{doc_id}", cookies={"session": token})

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "events": 0}
    assert await _count_events(async_session, doc_id) == 0
    assert fake_emitter.sent == []
