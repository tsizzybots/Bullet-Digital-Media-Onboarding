"""Integration tests for the Google Meet webhook receiver (S1-27).

Hit Postgres (the `onboarding_events` upsert) via the transactional
`async_session` fixture, so all are `@pytest.mark.db`. The Pub/Sub verifier and
the event emitter are overridden to known doubles so verification is
deterministic and emitted events are captured rather than sent.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.db import get_session
from bullet_api.google.pubsub import MEET_TRANSCRIPT_READY_CE_TYPE
from bullet_api.main import app
from bullet_api.webhooks.google_meet import get_pubsub_verifier
from bullet_api.worker import MEET_TRANSCRIPT_READY_EVENT, FakeEventEmitter, get_event_emitter

GOOD_TOKEN = "good-push-token"


def _envelope(transcript_name: str, ce_type: str = MEET_TRANSCRIPT_READY_CE_TYPE) -> bytes:
    payload = {"transcript": {"name": transcript_name}}
    return json.dumps(
        {
            "message": {
                "attributes": {"ce-type": ce_type},
                "data": base64.b64encode(json.dumps(payload).encode()).decode(),
            }
        }
    ).encode()


@pytest_asyncio.fixture
async def webhook_client(
    async_session: AsyncSession,
) -> AsyncIterator[tuple[AsyncClient, FakeEventEmitter]]:
    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    fake_emitter = FakeEventEmitter()

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_pubsub_verifier] = lambda: lambda token: token == GOOD_TOKEN
    app.dependency_overrides[get_event_emitter] = lambda: fake_emitter
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, fake_emitter
    finally:
        app.dependency_overrides.clear()


async def _count_events(db: AsyncSession, transcript_name: str) -> int:
    result = await db.execute(
        text(
            "SELECT count(*) FROM onboarding_events WHERE event_type = :et AND external_id = :eid"
        ),
        {"et": MEET_TRANSCRIPT_READY_EVENT, "eid": transcript_name},
    )
    return result.scalar_one()


@pytest.mark.db
async def test_valid_transcript_persists_and_emits(
    async_session: AsyncSession,
    webhook_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, fake = webhook_client
    name = f"conferenceRecords/{uuid.uuid4().hex[:8]}/transcripts/{uuid.uuid4().hex[:8]}"

    response = await client.post(
        "/webhooks/google-meet",
        content=_envelope(name),
        headers={"authorization": f"Bearer {GOOD_TOKEN}", "content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "events": 1}
    assert await _count_events(async_session, name) == 1

    assert len(fake.sent) == 1
    event_name, data = fake.sent[0]
    assert event_name == MEET_TRANSCRIPT_READY_EVENT
    assert data["transcript_name"] == name
    assert data["conference_record_name"] == name.split("/transcripts/")[0]
    assert data["onboarding_event_id"]


@pytest.mark.db
async def test_invalid_token_401_no_row(
    async_session: AsyncSession,
    webhook_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, fake = webhook_client
    name = f"conferenceRecords/{uuid.uuid4().hex[:8]}/transcripts/{uuid.uuid4().hex[:8]}"

    response = await client.post(
        "/webhooks/google-meet",
        content=_envelope(name),
        headers={"authorization": "Bearer WRONG", "content-type": "application/json"},
    )

    assert response.status_code == 401
    assert await _count_events(async_session, name) == 0
    assert fake.sent == []


@pytest.mark.db
async def test_missing_token_401(
    webhook_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, _ = webhook_client
    name = f"conferenceRecords/{uuid.uuid4().hex[:8]}/transcripts/{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/webhooks/google-meet",
        content=_envelope(name),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 401


@pytest.mark.db
async def test_non_transcript_event_ignored_no_row(
    async_session: AsyncSession,
    webhook_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, fake = webhook_client
    name = f"conferenceRecords/{uuid.uuid4().hex[:8]}/transcripts/{uuid.uuid4().hex[:8]}"

    response = await client.post(
        "/webhooks/google-meet",
        content=_envelope(name, ce_type="google.workspace.meet.recording.v2.fileGenerated"),
        headers={"authorization": f"Bearer {GOOD_TOKEN}", "content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "events": 0}
    assert await _count_events(async_session, name) == 0
    assert fake.sent == []


@pytest.mark.db
async def test_invalid_json_body_400(
    webhook_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, _ = webhook_client
    response = await client.post(
        "/webhooks/google-meet",
        content=b"{not json",
        headers={"authorization": f"Bearer {GOOD_TOKEN}", "content-type": "application/json"},
    )
    assert response.status_code == 400


@pytest_asyncio.fixture
async def unconfigured_webhook_client(
    async_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """Receiver with the REAL `get_pubsub_verifier` (not overridden) and the
    default empty audience / SA email - so it must fail closed."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = _session_override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


@pytest.mark.db
async def test_receiver_fails_closed_when_verifier_unconfigured(
    async_session: AsyncSession,
    unconfigured_webhook_client: AsyncClient,
) -> None:
    """With no GOOGLE_PUBSUB_PUSH_AUDIENCE / _SA_EMAIL configured (the default),
    verify_pubsub_push returns False for ANY token, so the receiver 401s and
    writes no row - fail closed, never open."""
    name = f"conferenceRecords/{uuid.uuid4().hex[:8]}/transcripts/{uuid.uuid4().hex[:8]}"
    response = await unconfigured_webhook_client.post(
        "/webhooks/google-meet",
        content=_envelope(name),
        headers={"authorization": "Bearer anything", "content-type": "application/json"},
    )
    assert response.status_code == 401
    assert await _count_events(async_session, name) == 0


@pytest.mark.db
async def test_emit_failure_rolls_back_no_row(
    async_session: AsyncSession,
) -> None:
    """If the emit raises, the commit never runs: the REAL get_session rolls
    back, no onboarding_events row persists, and Pub/Sub retries. Locks the
    emit-then-commit ordering against regression.

    NOTE: get_session is deliberately NOT overridden here - the route uses its
    own real session so its on-exception rollback actually fires (a shared test
    session would keep the route's uncommitted INSERT visible). `async_session`
    is a separate transaction used only to assert nothing committed.
    """

    class _RaisingEmitter:
        async def send(self, name: str, data: dict) -> None:
            raise RuntimeError("inngest down")

    name = f"conferenceRecords/{uuid.uuid4().hex[:8]}/transcripts/{uuid.uuid4().hex[:8]}"
    app.dependency_overrides[get_pubsub_verifier] = lambda: lambda token: token == GOOD_TOKEN
    app.dependency_overrides[get_event_emitter] = lambda: _RaisingEmitter()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with pytest.raises(RuntimeError, match="inngest down"):
                await ac.post(
                    "/webhooks/google-meet",
                    content=_envelope(name),
                    headers={
                        "authorization": f"Bearer {GOOD_TOKEN}",
                        "content-type": "application/json",
                    },
                )
    finally:
        app.dependency_overrides.clear()

    assert await _count_events(async_session, name) == 0


@pytest.mark.db
async def test_replay_is_idempotent_no_second_emit(
    async_session: AsyncSession,
    webhook_client: tuple[AsyncClient, FakeEventEmitter],
) -> None:
    client, fake = webhook_client
    name = f"conferenceRecords/{uuid.uuid4().hex[:8]}/transcripts/{uuid.uuid4().hex[:8]}"
    body = _envelope(name)
    headers = {"authorization": f"Bearer {GOOD_TOKEN}", "content-type": "application/json"}

    first = await client.post("/webhooks/google-meet", content=body, headers=headers)
    second = await client.post("/webhooks/google-meet", content=body, headers=headers)

    assert first.json() == {"status": "accepted", "events": 1}
    assert second.json() == {"status": "duplicate", "events": 0}
    assert await _count_events(async_session, name) == 1
    assert len(fake.sent) == 1  # only the first request emitted
