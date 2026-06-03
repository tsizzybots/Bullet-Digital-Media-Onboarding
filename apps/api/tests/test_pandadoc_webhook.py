"""Integration tests for the PandaDoc webhook receiver (S1-22).

These hit Postgres (the `onboarding_events` upsert) via the transactional
`async_session` fixture, so they are all marked `@pytest.mark.db` and skip
when no DATABASE_URL is reachable. The signature key and event emitter are
overridden to known test doubles so signing is deterministic and emitted
events are captured rather than sent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.db import get_session
from bullet_api.main import app
from bullet_api.webhooks.pandadoc import get_pandadoc_secret
from bullet_api.worker import PANDADOC_SIGNED_EVENT, FakeEventEmitter, get_event_emitter

TEST_SECRET = "test-shared-key"


def _sign(raw: bytes) -> str:
    return hmac.new(TEST_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _completed_payload(doc_id: str) -> list[dict]:
    return [
        {
            "event": "document_state_changed",
            "data": {"id": doc_id, "name": "Agreement", "status": "document.completed"},
        }
    ]


def _sent_payload(doc_id: str) -> list[dict]:
    return [
        {
            "event": "document_state_changed",
            "data": {"id": doc_id, "name": "Agreement", "status": "document.sent"},
        }
    ]


@pytest_asyncio.fixture
async def webhook_client(
    async_session: AsyncSession,
) -> AsyncIterator[tuple[AsyncClient, FakeEventEmitter]]:
    """AsyncClient with DB session, PandaDoc secret, and event emitter
    overridden to known test doubles."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    def _secret_override() -> str:
        return TEST_SECRET

    fake_emitter = FakeEventEmitter()

    def _emitter_override() -> FakeEventEmitter:
        return fake_emitter

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_pandadoc_secret] = _secret_override
    app.dependency_overrides[get_event_emitter] = _emitter_override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, fake_emitter
    finally:
        app.dependency_overrides.clear()


async def _count_events(db: AsyncSession, doc_id: str) -> int:
    result = await db.execute(
        text(
            "SELECT count(*) FROM onboarding_events WHERE event_type = :et AND external_id = :eid"
        ),
        {"et": PANDADOC_SIGNED_EVENT, "eid": doc_id},
    )
    return result.scalar_one()


@pytest.mark.db
async def test_valid_new_document_persists_and_emits(
    async_session: AsyncSession,
    webhook_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, fake = webhook_client
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    raw = json.dumps(_completed_payload(doc_id)).encode()

    response = await client.post(
        "/webhooks/pandadoc",
        params={"signature": _sign(raw)},
        content=raw,
        headers={"content-type": "application/json"},
    )

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

    assert len(fake.sent) == 1
    name, data = fake.sent[0]
    assert name == PANDADOC_SIGNED_EVENT
    assert data["document_id"] == doc_id
    assert data["onboarding_event_id"]


@pytest.mark.db
async def test_replay_is_idempotent(
    async_session: AsyncSession,
    webhook_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, fake = webhook_client
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    raw = json.dumps(_completed_payload(doc_id)).encode()
    sig = _sign(raw)

    first = await client.post(
        "/webhooks/pandadoc",
        params={"signature": sig},
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert first.status_code == 200
    assert first.json() == {"status": "accepted", "events": 1}

    second = await client.post(
        "/webhooks/pandadoc",
        params={"signature": sig},
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate", "events": 0}

    assert await _count_events(async_session, doc_id) == 1
    assert len(fake.sent) == 1


@pytest.mark.db
async def test_tampered_signature_rejected_no_row_no_emit(
    async_session: AsyncSession,
    webhook_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, fake = webhook_client
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    raw = json.dumps(_completed_payload(doc_id)).encode()
    # Corrupt the leading hex while keeping a valid-length digest.
    tampered = "deadbeef" + _sign(raw)[8:]

    response = await client.post(
        "/webhooks/pandadoc",
        params={"signature": tampered},
        content=raw,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 401
    assert await _count_events(async_session, doc_id) == 0
    assert fake.sent == []


@pytest.mark.db
async def test_missing_signature_rejected(
    async_session: AsyncSession,
    webhook_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, fake = webhook_client
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    raw = json.dumps(_completed_payload(doc_id)).encode()

    response = await client.post(
        "/webhooks/pandadoc",
        content=raw,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 401
    assert await _count_events(async_session, doc_id) == 0
    assert fake.sent == []


@pytest.mark.db
async def test_non_completed_event_ignored(
    async_session: AsyncSession,
    webhook_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, fake = webhook_client
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    raw = json.dumps(_sent_payload(doc_id)).encode()

    response = await client.post(
        "/webhooks/pandadoc",
        params={"signature": _sign(raw)},
        content=raw,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "events": 0}
    assert await _count_events(async_session, doc_id) == 0
    assert fake.sent == []
