"""Integration tests for the S1-27 `capture_meet_transcript` worker.

Hit Postgres (`sales_call_transcripts` upsert, `documents` insert, the
`onboarding_events.processed_at` stamp) via the transactional `async_session`
fixture, so all are `@pytest.mark.db`. Google Meet / Calendar / R2 are the
Fake* doubles so no network call is made.

The matrix covers the failure/transport/replay/tie-break paths, not just the
happy path:
- capture with no client match parks the transcript unlinked, stamps the audit
  row, and emits nothing;
- a client already on file for an attendee email links immediately + emits;
- an empty transcript and a Meet 404 are hard failures (no row);
- a transport error propagates and leaves processed_at NULL (Inngest retries);
- replay is idempotent (one transcript row, one documents row);
- a multi-sibling email links to the most-recently-created client.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import sqlalchemy.exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.google.calendar_client import CalendarEvent, FakeCalendarClient
from bullet_api.google.meet_client import (
    ConferenceRecord,
    FakeMeetClient,
    GoogleApiNotFound,
    TranscriptEntry,
)
from bullet_api.storage.client import FakeStorageClient
from bullet_api.worker import MEET_TRANSCRIPT_READY_EVENT, TRANSCRIPT_LINKED_EVENT, FakeEventEmitter
from bullet_api.worker.meet_transcript import (
    EmptyTranscriptError,
    build_transcript_key,
    capture_meet_transcript,
    capture_meet_transcript_core,
)

MEETING_CODE = "abc-defg-hij"


def _names() -> tuple[str, str]:
    cid = uuid.uuid4().hex[:8]
    tid = uuid.uuid4().hex[:8]
    conference = f"conferenceRecords/{cid}"
    transcript = f"{conference}/transcripts/{tid}"
    return conference, transcript


async def _seed_meet_event(session: AsyncSession, transcript_name: str) -> uuid.UUID:
    result = await session.execute(
        text(
            "INSERT INTO onboarding_events (event_type, external_id, payload, verified_at) "
            "VALUES ('google_meet.transcript_ready', :eid, cast('{}' AS jsonb), now()) "
            "RETURNING id"
        ),
        {"eid": transcript_name},
    )
    return result.scalar_one()


async def _seed_client(session: AsyncSession, email: str) -> uuid.UUID:
    result = await session.execute(
        text(
            "INSERT INTO clients (email, legal_entity, current_step, step_entered_at) "
            "VALUES (:email, 'Sample Gym Ltd', 'signed', now()) RETURNING id"
        ),
        {"email": email},
    )
    return result.scalar_one()


def _meet_client(conference: str, transcript: str, *, with_code: bool = True) -> FakeMeetClient:
    return FakeMeetClient(
        conference_records={
            conference: ConferenceRecord(
                name=conference,
                space_name="spaces/xyz",
                meeting_code=MEETING_CODE if with_code else None,
                start_time="2026-06-10T10:00:00Z",
                end_time="2026-06-10T10:30:00Z",
            )
        },
        transcript_entries={
            transcript: [
                TranscriptEntry(participant="Rep", text="Welcome to the call."),
                TranscriptEntry(participant="Prospect", text="Thanks, excited to chat."),
            ]
        },
    )


async def _transcript_row(session: AsyncSession, transcript_name: str):
    return (
        await session.execute(
            text(
                "SELECT id, client_id, r2_key, link_method, participant_emails, transcript_chars "
                "FROM sales_call_transcripts WHERE source = 'google_meet' AND external_id = :eid"
            ),
            {"eid": transcript_name},
        )
    ).all()


async def _processed_at(session: AsyncSession, event_id: uuid.UUID):
    return (
        await session.execute(
            text("SELECT processed_at FROM onboarding_events WHERE id = :id"),
            {"id": event_id},
        )
    ).scalar_one()


# --------------------------------------------------------------------------- #
# Happy path - no client yet (the common case): park unlinked, emit nothing
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_capture_parks_unlinked_when_no_client(async_session: AsyncSession) -> None:
    conference, transcript = _names()
    event_id = await _seed_meet_event(async_session, transcript)
    # Calendar finds the invite (attendee email present) but NO client exists yet.
    calendar = FakeCalendarClient(
        events_by_meeting_code={
            MEETING_CODE: CalendarEvent(event_id="evt1", attendee_emails=("prospect@gym.com",))
        }
    )
    storage = FakeStorageClient()
    emitter = FakeEventEmitter()

    result = await capture_meet_transcript_core(
        async_session,
        _meet_client(conference, transcript),
        calendar,
        storage,
        emitter,
        onboarding_event_id=event_id,
        transcript_name=transcript,
        conference_record_name=conference,
    )

    assert result.linked is False
    assert result.client_id is None
    rows = await _transcript_row(async_session, transcript)
    assert len(rows) == 1
    assert rows[0].client_id is None
    assert rows[0].r2_key == build_transcript_key(transcript)
    assert rows[0].participant_emails == ["prospect@gym.com"]
    assert rows[0].transcript_chars > 0
    # one R2 put, audit row stamped processed, no emit (nothing linked)
    assert len(storage.puts) == 1
    assert await _processed_at(async_session, event_id) is not None
    assert emitter.sent == []


# --------------------------------------------------------------------------- #
# Immediate auto-link - a client already on file for an attendee email
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_capture_immediately_links_existing_client(async_session: AsyncSession) -> None:
    conference, transcript = _names()
    event_id = await _seed_meet_event(async_session, transcript)
    # Client email differs in case from the lowercased attendee email - the
    # citext match must still hit.
    client_id = await _seed_client(async_session, "Prospect@Gym.com")
    calendar = FakeCalendarClient(
        events_by_meeting_code={
            MEETING_CODE: CalendarEvent(event_id="evt1", attendee_emails=("prospect@gym.com",))
        }
    )
    emitter = FakeEventEmitter()

    result = await capture_meet_transcript_core(
        async_session,
        _meet_client(conference, transcript),
        calendar,
        FakeStorageClient(),
        emitter,
        onboarding_event_id=event_id,
        transcript_name=transcript,
        conference_record_name=conference,
    )

    assert result.linked is True
    assert result.client_id == client_id
    rows = await _transcript_row(async_session, transcript)
    assert rows[0].client_id == client_id
    assert rows[0].link_method == "email_immediate"

    # documents row created
    docs = (
        await async_session.execute(
            text(
                "SELECT id, r2_key FROM documents "
                "WHERE client_id = :cid AND kind = 'transcript_text'"
            ),
            {"cid": client_id},
        )
    ).all()
    assert len(docs) == 1

    # transcript.linked emitted with the right payload
    assert len(emitter.sent) == 1
    name, data = emitter.sent[0]
    assert name == TRANSCRIPT_LINKED_EVENT
    assert data["client_id"] == str(client_id)
    assert data["transcript_id"] == str(rows[0].id)
    assert data["document_id"] == str(docs[0].id)


# --------------------------------------------------------------------------- #
# Hard failures
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_empty_transcript_raises_no_row(async_session: AsyncSession) -> None:
    conference, transcript = _names()
    event_id = await _seed_meet_event(async_session, transcript)
    meet = FakeMeetClient(
        conference_records={conference: ConferenceRecord(name=conference, meeting_code=None)},
        transcript_entries={transcript: [TranscriptEntry(participant="Rep", text="   ")]},
    )

    with pytest.raises(EmptyTranscriptError):
        await capture_meet_transcript_core(
            async_session,
            meet,
            FakeCalendarClient(),
            FakeStorageClient(),
            FakeEventEmitter(),
            onboarding_event_id=event_id,
            transcript_name=transcript,
            conference_record_name=conference,
        )
    assert await _transcript_row(async_session, transcript) == []


@pytest.mark.db
async def test_conference_404_raises(async_session: AsyncSession) -> None:
    conference, transcript = _names()
    event_id = await _seed_meet_event(async_session, transcript)
    # FakeMeetClient with no conference record -> GoogleApiNotFound.
    meet = FakeMeetClient()

    with pytest.raises(GoogleApiNotFound):
        await capture_meet_transcript_core(
            async_session,
            meet,
            FakeCalendarClient(),
            FakeStorageClient(),
            FakeEventEmitter(),
            onboarding_event_id=event_id,
            transcript_name=transcript,
            conference_record_name=conference,
        )


@pytest.mark.db
async def test_transport_error_propagates_processed_at_null(async_session: AsyncSession) -> None:
    conference, transcript = _names()
    event_id = await _seed_meet_event(async_session, transcript)
    meet = FakeMeetClient(error=httpx.ReadTimeout("meet timed out"))

    with pytest.raises(httpx.ReadTimeout):
        await capture_meet_transcript_core(
            async_session,
            meet,
            FakeCalendarClient(),
            FakeStorageClient(),
            FakeEventEmitter(),
            onboarding_event_id=event_id,
            transcript_name=transcript,
            conference_record_name=conference,
        )
    # audit row stays unprocessed so Inngest's retry is visible, no parked row
    assert await _processed_at(async_session, event_id) is None
    assert await _transcript_row(async_session, transcript) == []


# --------------------------------------------------------------------------- #
# Replay idempotency + multi-sibling tie-break
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_replay_is_idempotent(async_session: AsyncSession) -> None:
    conference, transcript = _names()
    event_id = await _seed_meet_event(async_session, transcript)
    client_id = await _seed_client(async_session, "prospect@gym.com")
    calendar = FakeCalendarClient(
        events_by_meeting_code={
            MEETING_CODE: CalendarEvent(event_id="evt1", attendee_emails=("prospect@gym.com",))
        }
    )

    for _ in range(2):
        await capture_meet_transcript_core(
            async_session,
            _meet_client(conference, transcript),
            calendar,
            FakeStorageClient(),
            FakeEventEmitter(),
            onboarding_event_id=event_id,
            transcript_name=transcript,
            conference_record_name=conference,
        )

    rows = await _transcript_row(async_session, transcript)
    assert len(rows) == 1  # one transcript row despite two runs
    docs = (
        await async_session.execute(
            text(
                "SELECT count(*) FROM documents WHERE client_id = :cid AND kind = 'transcript_text'"
            ),
            {"cid": client_id},
        )
    ).scalar_one()
    assert docs == 1  # one documents row despite two runs


@pytest.mark.db
async def test_multi_invitee_links_only_the_client_attendee(async_session: AsyncSession) -> None:
    """A real invite has the prospect AND internal Bullet people (coordinator,
    moderator). The organiser is dropped by the calendar client; the remaining
    emails (client + internal moderator) are all stored, but only the one that
    is an actual client drives the link - internal teammates are never clients,
    so they never get a transcript attached."""
    conference, transcript = _names()
    event_id = await _seed_meet_event(async_session, transcript)
    client_id = await _seed_client(async_session, "prospect@gym.com")
    # Calendar returns the prospect + an internal moderator (organiser already
    # excluded upstream by _normalise_emails).
    calendar = FakeCalendarClient(
        events_by_meeting_code={
            MEETING_CODE: CalendarEvent(
                event_id="evt1",
                attendee_emails=("prospect@gym.com", "moderator@bulletdigitalmedia.com"),
            )
        }
    )
    emitter = FakeEventEmitter()

    result = await capture_meet_transcript_core(
        async_session,
        _meet_client(conference, transcript),
        calendar,
        FakeStorageClient(),
        emitter,
        onboarding_event_id=event_id,
        transcript_name=transcript,
        conference_record_name=conference,
    )

    assert result.client_id == client_id  # linked to the prospect, not the moderator
    rows = await _transcript_row(async_session, transcript)
    # both emails stored (inert), but exactly one client linked + one emit
    assert set(rows[0].participant_emails) == {
        "prospect@gym.com",
        "moderator@bulletdigitalmedia.com",
    }
    assert rows[0].client_id == client_id
    assert [n for n, _ in emitter.sent] == [TRANSCRIPT_LINKED_EVENT]


def test_capture_worker_inngest_config() -> None:
    """The Inngest wrapper triggers on google_meet.transcript_ready and declares
    the global + per-transcript concurrency caps. No DB needed - pure config."""
    cfg = capture_meet_transcript.get_config("http://localhost:8000/api/inngest").main
    assert [t.event for t in cfg.triggers] == [MEET_TRANSCRIPT_READY_EVENT]
    caps = {(c.key, c.limit) for c in cfg.concurrency}
    assert (None, 5) in caps  # global cap bounds parallel Google/R2 work
    assert ("event.data.transcript_name", 1) in caps  # one run per transcript


@pytest.mark.db
async def test_documents_partial_unique_index_blocks_duplicate(
    async_session: AsyncSession,
) -> None:
    """The partial UNIQUE index `uq_documents_transcript_text` makes the
    no-duplicate guarantee STRUCTURAL: a second transcript_text document for the
    same (client_id, r2_key) is rejected, so concurrent linkers cannot both
    insert. (Other document kinds stay unconstrained - the index is partial.)"""
    client_id = await _seed_client(async_session, "dup@gym.com")
    r2_key = "sales-call-transcripts/conferenceRecords/x/transcripts/y.txt"
    insert = text(
        "INSERT INTO documents (client_id, kind, r2_key) VALUES (:cid, 'transcript_text', :r2)"
    )
    await async_session.execute(insert, {"cid": client_id, "r2": r2_key})
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await async_session.execute(insert, {"cid": client_id, "r2": r2_key})


@pytest.mark.db
async def test_multi_sibling_links_most_recent_client(async_session: AsyncSession) -> None:
    conference, transcript = _names()
    event_id = await _seed_meet_event(async_session, transcript)
    shared = f"returning+{uuid.uuid4().hex[:6]}@gym.com"
    older = await _seed_client(async_session, shared)
    # Force a strictly-later created_at on the newer sibling so the tie-break is
    # deterministic regardless of clock resolution.
    newer = await _seed_client(async_session, shared)
    await async_session.execute(
        text("UPDATE clients SET created_at = now() - interval '1 hour' WHERE id = :id"),
        {"id": older},
    )
    calendar = FakeCalendarClient(
        events_by_meeting_code={
            MEETING_CODE: CalendarEvent(event_id="evt1", attendee_emails=(shared.lower(),))
        }
    )

    result = await capture_meet_transcript_core(
        async_session,
        _meet_client(conference, transcript),
        calendar,
        FakeStorageClient(),
        FakeEventEmitter(),
        onboarding_event_id=event_id,
        transcript_name=transcript,
        conference_record_name=conference,
    )
    assert result.client_id == newer
