"""S1-27: capture a Google Meet sales-call transcript + park it (always-capture).

`capture_meet_transcript` is triggered by `google_meet.transcript_ready`
(emitted by the `/webhooks/google-meet` receiver). It is the "always capture"
layer of the three-layer design: fetch the transcript, store the text in R2,
and upsert a `sales_call_transcripts` row - matched to a client or not - so no
call is ever lost.

It then attempts the immediate auto-link (layer 2a): if a client already exists
for one of the meeting's invite-attendee emails, attach the transcript now and
emit `transcript.linked` for S1-29. Most calls precede the client record, so
this usually finds nothing and the transcript stays parked for the signing-time
backfill (layer 2b) or the manual fallback (layer 3).

Correctness (mirrors S1-25b `store_signed_pdf`, minus the per-client
`platform_actions` row, which cannot exist before the client does - idempotency
here is the `sales_call_transcripts (source, external_id)` unique upsert plus
the `onboarding_events (event_type, external_id)` webhook key):

- **Idempotent**: the transcript row is upserted ON CONFLICT(source,external_id)
  DO UPDATE (never touching `client_id`, so a replay cannot unlink), the R2 key
  is deterministic (re-upload overwrites), and the link path is guarded.
- **Failure visibility**: `onboarding_events.processed_at` is stamped only on a
  fully-successful run. A transport error (Meet/Calendar/R2) propagates with
  `processed_at` left NULL, so the audit row shows "received, not processed" and
  Inngest retries; a typed structural failure (404 / empty transcript) is
  dead-lettered by the wrapper.
- **Emit after commit**: `transcript.linked` is emitted only after the DB commit
  and is re-derived from the row's linked state, so an Inngest retry re-emits
  (S1-29 must be idempotent per transcript).
- **Concurrency**: a global cap bounds parallel Google/R2 calls; a per-transcript
  cap of 1 prevents concurrent duplicate work for one transcript.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

import inngest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.config import get_settings
from bullet_api.db.enums import (
    DOCUMENT_KIND_TRANSCRIPT_TEXT,
    SALES_CALL_TRANSCRIPT_SOURCE_GOOGLE_MEET,
)
from bullet_api.db.session import AsyncSessionLocal
from bullet_api.google.calendar_client import (
    CalendarClient,
    HttpCalendarClient,
)
from bullet_api.google.credentials import google_bearer_token
from bullet_api.google.meet_client import (
    ConferenceRecord,
    GoogleApiNotFound,
    HttpMeetClient,
    MeetClient,
)
from bullet_api.storage.client import StorageClient, get_storage_client
from bullet_api.transcripts.linking import (
    LINK_METHOD_EMAIL_IMMEDIATE,
    link_transcript_to_client,
)
from bullet_api.worker._inngest import inngest_client
from bullet_api.worker.events import (
    MEET_TRANSCRIPT_READY_EVENT,
    TRANSCRIPT_LINKED_EVENT,
    EventEmitter,
    InngestEventEmitter,
)

log = logging.getLogger(__name__)

SOURCE = SALES_CALL_TRANSCRIPT_SOURCE_GOOGLE_MEET
TRANSCRIPT_CONTENT_TYPE = "text/plain; charset=utf-8"


class EmptyTranscriptError(ValueError):
    """The Meet transcript had no usable text. Storing an empty object + row
    would be worse than failing, so this is a hard (non-retriable) failure."""

    def __init__(self, transcript_name: str) -> None:
        self.transcript_name = transcript_name
        super().__init__(f"Google Meet transcript {transcript_name} produced no text.")


@dataclass(frozen=True)
class CaptureResult:
    """Outcome of one capture run. `transcript_id` is the parked row; `linked`
    is True when the immediate email match attached it to a client this run."""

    transcript_id: uuid.UUID
    r2_key: str
    linked: bool
    client_id: uuid.UUID | None


def build_transcript_key(transcript_name: str) -> str:
    """Deterministic R2 key for a transcript. Deterministic so a retry overwrites
    the same object instead of creating a duplicate. The resource name already
    contains slashes (`conferenceRecords/{c}/transcripts/{t}`), which become R2
    path segments."""
    return f"sales-call-transcripts/{transcript_name}.txt"


def _parse_rfc3339(value: str | None) -> datetime | None:
    """Parse a Meet RFC3339 timestamp string to an aware datetime (or None).

    The Meet API returns `startTime`/`endTime` as RFC3339 strings; asyncpg binds
    a timestamptz param from a `datetime`, not a string, so we parse here. A
    malformed value is treated as absent rather than failing the whole capture
    (the timestamps are descriptive metadata, not the idempotency key).
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def assemble_transcript_text(entries: list) -> str:
    """Join transcript entries into a readable `participant: text` document."""
    lines = []
    for entry in entries:
        speaker = entry.participant or "unknown"
        body = (entry.text or "").strip()
        if body:
            lines.append(f"{speaker}: {body}")
    return "\n".join(lines)


async def _gather_attendee_emails(
    calendar_client: CalendarClient,
    record: ConferenceRecord,
) -> tuple[tuple[str, ...], str | None]:
    """Return (lowercased attendee emails, calendar_event_id) for the meeting.

    The calendar invite is the reliable email source (the prospect is "always
    invited by email"). Returns an empty tuple + None when there is no meeting
    code or no matching event - the transcript then parks unlinked for the
    manual fallback.
    """
    if not record.meeting_code:
        return (), None
    event = await calendar_client.find_event_by_meeting_code(
        meeting_code=record.meeting_code,
        time_min=record.start_time,
        time_max=record.end_time,
    )
    if event is None:
        return (), None
    return event.attendee_emails, event.event_id


async def capture_meet_transcript_core(
    session: AsyncSession,
    meet_client: MeetClient,
    calendar_client: CalendarClient,
    storage: StorageClient,
    emitter: EventEmitter,
    *,
    onboarding_event_id: uuid.UUID,
    transcript_name: str,
    conference_record_name: str,
) -> CaptureResult:
    """Fetch + store the transcript, park it, attempt the immediate email link.

    Raises:
        GoogleApiNotFound: the conference record / transcript is gone (404).
        EmptyTranscriptError: the transcript produced no text.
        Any Meet/Calendar/R2 transport error: propagates (processed_at stays
            NULL, Inngest retries).
    """
    record = await meet_client.get_conference_record(conference_record_name)
    entries = await meet_client.fetch_transcript_entries(transcript_name)
    transcript_text = assemble_transcript_text(entries)
    if not transcript_text.strip():
        raise EmptyTranscriptError(transcript_name)

    attendee_emails, calendar_event_id = await _gather_attendee_emails(calendar_client, record)

    r2_key = build_transcript_key(transcript_name)
    await storage.put_object(r2_key, transcript_text.encode("utf-8"), TRANSCRIPT_CONTENT_TYPE)

    # Upsert the parking row. DO UPDATE refreshes the capture fields but never
    # touches client_id / linked_at, so a replay after a link cannot unlink.
    upserted = (
        await session.execute(
            text(
                "INSERT INTO sales_call_transcripts ("
                "  source, external_id, r2_key, calendar_event_id,"
                "  meeting_start, meeting_end, participant_emails,"
                "  transcript_chars, metadata"
                ") VALUES ("
                "  :source, :external_id, :r2_key, :calendar_event_id,"
                "  :meeting_start, :meeting_end, cast(:emails AS jsonb),"
                "  :chars, cast(:metadata AS jsonb)"
                ") "
                "ON CONFLICT (source, external_id) DO UPDATE SET "
                "  r2_key = EXCLUDED.r2_key,"
                "  calendar_event_id = EXCLUDED.calendar_event_id,"
                "  meeting_start = EXCLUDED.meeting_start,"
                "  meeting_end = EXCLUDED.meeting_end,"
                "  participant_emails = EXCLUDED.participant_emails,"
                "  transcript_chars = EXCLUDED.transcript_chars,"
                "  metadata = EXCLUDED.metadata "
                "RETURNING id, client_id"
            ),
            {
                "source": SOURCE,
                "external_id": transcript_name,
                "r2_key": r2_key,
                "calendar_event_id": calendar_event_id,
                "meeting_start": _parse_rfc3339(record.start_time),
                "meeting_end": _parse_rfc3339(record.end_time),
                "emails": json.dumps(list(attendee_emails)),
                "chars": len(transcript_text),
                "metadata": json.dumps(
                    {
                        "transcript_name": transcript_name,
                        "conference_record_name": conference_record_name,
                        "meeting_code": record.meeting_code,
                        "entry_count": len(entries),
                    }
                ),
            },
        )
    ).one()
    transcript_id: uuid.UUID = upserted.id
    already_linked = upserted.client_id is not None

    # Stamp the audit row processed (only reached on a fully-successful capture).
    # No rowcount guard here (unlike S1-25a's load-bearing backfill): this stamp
    # is best-effort audit. If the row is missing (e.g. the webhook's emit landed
    # but its commit rolled back, so Pub/Sub retried under a new event id), the
    # transcript - the artefact that matters - is still captured + parked, and the
    # retried webhook stamps its own row.
    await session.execute(
        text(
            "UPDATE onboarding_events "
            "SET processed_at = COALESCE(processed_at, now()) WHERE id = :eid"
        ),
        {"eid": onboarding_event_id},
    )

    # Immediate auto-link: only when the row is not already linked and we have
    # candidate emails. Most calls precede the client, so this usually no-ops.
    # `ORDER BY created_at DESC, id DESC` picks the most-recent client when an
    # email maps to several rows (returning client / second site per S1-26); the
    # `id DESC` tiebreak keeps it deterministic on an exact created_at collision.
    linked_client_id: uuid.UUID | None = upserted.client_id
    if not already_linked and attendee_emails:
        match = (
            await session.execute(
                text(
                    "SELECT id FROM clients WHERE email = ANY(:emails) "
                    "ORDER BY created_at DESC, id DESC LIMIT 1"
                ),
                {"emails": list(attendee_emails)},
            )
        ).scalar()
        if match is not None:
            outcome = await link_transcript_to_client(
                session,
                transcript_id=transcript_id,
                client_id=match,
                link_method=LINK_METHOD_EMAIL_IMMEDIATE,
            )
            if outcome is not None:
                linked_client_id = outcome.client_id

    await session.commit()

    # Emit AFTER commit, re-derived from the row's linked state so an Inngest
    # retry re-emits (S1-29 is idempotent per transcript). Skips the emit when
    # the linked row has no document yet (r2_key was NULL - not the case here
    # since we just stored it, but the linking helper is the source of truth).
    if linked_client_id is not None:
        await _emit_linked_if_documented(emitter, session, transcript_id, r2_key, linked_client_id)

    log.info(
        "S1-27 transcript captured",
        extra={
            "transcript_id": str(transcript_id),
            "transcript_name": transcript_name,
            "r2_key": r2_key,
            "linked": linked_client_id is not None,
            "attendee_email_count": len(attendee_emails),
        },
    )
    return CaptureResult(
        transcript_id=transcript_id,
        r2_key=r2_key,
        linked=linked_client_id is not None and not already_linked,
        client_id=linked_client_id,
    )


async def _emit_linked_if_documented(
    emitter: EventEmitter,
    session: AsyncSession,
    transcript_id: uuid.UUID,
    r2_key: str,
    client_id: uuid.UUID,
) -> None:
    """Emit `transcript.linked` for a linked transcript that has a documents row."""
    document_id = (
        await session.execute(
            text(
                "SELECT id FROM documents "
                "WHERE client_id = :cid AND kind = :kind AND r2_key = :r2_key "
                "ORDER BY created_at LIMIT 1"
            ),
            {"cid": client_id, "kind": DOCUMENT_KIND_TRANSCRIPT_TEXT, "r2_key": r2_key},
        )
    ).scalar()
    if document_id is None:
        return
    await emitter.send(
        TRANSCRIPT_LINKED_EVENT,
        {
            "client_id": str(client_id),
            "transcript_id": str(transcript_id),
            "r2_key": r2_key,
            "source": SOURCE,
            "document_id": str(document_id),
        },
    )


@inngest_client.create_function(
    fn_id="capture-meet-transcript",
    trigger=inngest.TriggerEvent(event=MEET_TRANSCRIPT_READY_EVENT),
    concurrency=[
        inngest.Concurrency(limit=5, scope="fn"),
        inngest.Concurrency(key="event.data.transcript_name", limit=1, scope="fn"),
    ],
)
async def capture_meet_transcript(ctx: inngest.Context) -> dict:
    """Inngest wrapper: build production deps, run the core, classify failures.

    Raises:
        inngest.NonRetriableError: structural / non-self-healing failures
            (Meet 404, empty transcript, empty Google creds / R2 misconfig).
        Other exceptions (Google/R2 5xx/timeout): propagate so Inngest retries.
    """
    onboarding_event_id = uuid.UUID(ctx.event.data["onboarding_event_id"])
    transcript_name = str(ctx.event.data["transcript_name"])
    conference_record_name = str(ctx.event.data["conference_record_name"])

    settings = get_settings()
    meet_client = HttpMeetClient(
        token_provider=google_bearer_token,
        base_url=settings.google_meet_api_base_url,
    )
    calendar_client = HttpCalendarClient(
        token_provider=google_bearer_token,
        base_url=settings.google_calendar_api_base_url,
    )
    storage = get_storage_client()

    # The session is opened here but a SQLAlchemy AsyncSession acquires its
    # pooled connection LAZILY on first execute() - and the core issues no DB
    # statement until after the Meet/Calendar/R2 calls complete. So no pooled
    # connection is held across that external latency (the S1-25a connection-
    # hygiene rule, reached here via no-statement-before-external rather than
    # commit-before-external).
    async with AsyncSessionLocal() as session:
        try:
            result = await capture_meet_transcript_core(
                session,
                meet_client,
                calendar_client,
                storage,
                InngestEventEmitter(inngest_client),
                onboarding_event_id=onboarding_event_id,
                transcript_name=transcript_name,
                conference_record_name=conference_record_name,
            )
        except (GoogleApiNotFound, EmptyTranscriptError) as exc:
            raise inngest.NonRetriableError(str(exc)) from exc
        except RuntimeError as exc:
            # Empty Google creds or unconfigured R2 - a misconfigured deploy
            # that cannot self-heal on retry.
            raise inngest.NonRetriableError(str(exc)) from exc

    return {
        "transcript_id": str(result.transcript_id),
        "r2_key": result.r2_key,
        "linked": result.linked,
        "client_id": str(result.client_id) if result.client_id else None,
    }


__all__ = [
    "SOURCE",
    "CaptureResult",
    "EmptyTranscriptError",
    "assemble_transcript_text",
    "build_transcript_key",
    "capture_meet_transcript",
    "capture_meet_transcript_core",
]
