"""Integration tests for the S1-27 signing-time transcript backfill (layer 2b).

When `create_client_record_core` creates a client (S1-25a), it must claim any
sales-call transcript parked for that client's email - the common case, since
the call precedes signing by days. These tests hit Postgres via the
transactional `async_session` fixture (`@pytest.mark.db`) and use
`FakeEventEmitter` to capture emissions.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.worker import (
    CLIENT_CREATED_EVENT,
    PANDADOC_SIGNED_EVENT,
    TRANSCRIPT_LINKED_EVENT,
    FakeEventEmitter,
)
from bullet_api.worker.client_record import create_client_record_core


def _detail_body(document_id: str, client_email: str) -> dict:
    return {
        "id": document_id,
        "name": "Agreement",
        "status": "document.completed",
        "tokens": [
            {"name": "Client.Email", "value": client_email},
            {"name": "Client.FirstName", "value": "Sample"},
            {"name": "Client.LastName", "value": "Signer"},
            {"name": "Company.Name", "value": "Sample Gym Ltd"},
        ],
    }


async def _seed_onboarding_event(session: AsyncSession, document_id: str) -> uuid.UUID:
    return (
        await session.execute(
            text(
                "INSERT INTO onboarding_events (event_type, external_id, payload, verified_at) "
                "VALUES (:et, :eid, cast('{}' AS jsonb), now()) RETURNING id"
            ),
            {"et": PANDADOC_SIGNED_EVENT, "eid": document_id},
        )
    ).scalar_one()


async def _park_transcript(session: AsyncSession, emails: list[str]) -> uuid.UUID:
    external_id = f"conferenceRecords/{uuid.uuid4().hex[:8]}/transcripts/{uuid.uuid4().hex[:8]}"
    return (
        await session.execute(
            text(
                "INSERT INTO sales_call_transcripts "
                "  (source, external_id, r2_key, participant_emails) "
                "VALUES ('google_meet', :eid, :r2, cast(:emails AS jsonb)) RETURNING id"
            ),
            {
                "eid": external_id,
                "r2": f"sales-call-transcripts/{external_id}.txt",
                "emails": json.dumps(emails),
            },
        )
    ).scalar_one()


async def _transcript(session: AsyncSession, transcript_id: uuid.UUID):
    return (
        await session.execute(
            text("SELECT client_id, link_method FROM sales_call_transcripts WHERE id = :id"),
            {"id": transcript_id},
        )
    ).one()


@pytest.mark.db
async def test_signing_links_parked_transcript_and_emits(async_session: AsyncSession) -> None:
    email = f"signer+{uuid.uuid4().hex[:6]}@gym.com"
    transcript_id = await _park_transcript(async_session, [email.lower()])
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    event_id = await _seed_onboarding_event(async_session, document_id)
    emitter = FakeEventEmitter()

    result = await create_client_record_core(
        async_session,
        onboarding_event_id=event_id,
        document_id=document_id,
        document=_detail_body(document_id, email),
        emitter=emitter,
    )

    row = await _transcript(async_session, transcript_id)
    assert row.client_id == result.client_id
    assert row.link_method == "email_signing"

    # a documents row exists for the linked transcript
    docs = (
        await async_session.execute(
            text(
                "SELECT count(*) FROM documents WHERE client_id = :cid AND kind = 'transcript_text'"
            ),
            {"cid": result.client_id},
        )
    ).scalar_one()
    assert docs == 1

    names = [n for n, _ in emitter.sent]
    assert CLIENT_CREATED_EVENT in names
    assert TRANSCRIPT_LINKED_EVENT in names
    linked = next(d for n, d in emitter.sent if n == TRANSCRIPT_LINKED_EVENT)
    assert linked["transcript_id"] == str(transcript_id)
    assert linked["client_id"] == str(result.client_id)


@pytest.mark.db
async def test_signing_leaves_non_matching_transcript_parked(async_session: AsyncSession) -> None:
    transcript_id = await _park_transcript(async_session, ["someone-else@other.com"])
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    event_id = await _seed_onboarding_event(async_session, document_id)
    emitter = FakeEventEmitter()

    await create_client_record_core(
        async_session,
        onboarding_event_id=event_id,
        document_id=document_id,
        document=_detail_body(document_id, f"signer+{uuid.uuid4().hex[:6]}@gym.com"),
        emitter=emitter,
    )

    row = await _transcript(async_session, transcript_id)
    assert row.client_id is None  # untouched
    assert TRANSCRIPT_LINKED_EVENT not in [n for n, _ in emitter.sent]


@pytest.mark.db
async def test_signing_replay_no_dup_doc_but_re_emits(async_session: AsyncSession) -> None:
    """A replay must NOT create a second documents row (the link guard + the
    documents ON CONFLICT), but it MUST still re-emit transcript.linked: the
    emit is re-derived from the committed rows so an Inngest retry after a
    post-commit-pre-emit crash never silently drops the S1-29 trigger."""
    email = f"signer+{uuid.uuid4().hex[:6]}@gym.com"
    await _park_transcript(async_session, [email.lower()])
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    event_id = await _seed_onboarding_event(async_session, document_id)

    first_emitter = FakeEventEmitter()
    result = await create_client_record_core(
        async_session,
        onboarding_event_id=event_id,
        document_id=document_id,
        document=_detail_body(document_id, email),
        emitter=first_emitter,
    )
    # Replay the same signing (simulates an Inngest retry).
    second_emitter = FakeEventEmitter()
    await create_client_record_core(
        async_session,
        onboarding_event_id=event_id,
        document_id=document_id,
        document=_detail_body(document_id, email),
        emitter=second_emitter,
    )

    docs = (
        await async_session.execute(
            text(
                "SELECT count(*) FROM documents WHERE client_id = :cid AND kind = 'transcript_text'"
            ),
            {"cid": result.client_id},
        )
    ).scalar_one()
    assert docs == 1  # no duplicate documents row despite two runs
    # the replay re-derives + re-emits (at-least-once delivery; S1-29 dedupes)
    assert TRANSCRIPT_LINKED_EVENT in [n for n, _ in second_emitter.sent]
