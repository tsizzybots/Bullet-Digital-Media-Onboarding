"""Sales-call transcript read + manual-attach API (S1-27).

Two endpoints, both behind `get_current_user` (any authenticated internal user,
same posture as `/clients`):

- `GET /transcripts/unlinked` - the parked transcripts the auto-link-by-email
  path missed (the ~10%). The S1-27a dashboard renders this list.
- `POST /transcripts/{id}/link` - attach one to a client by hand (layer 3). On
  success it links the row, creates the `documents` row, and emits
  `transcript.linked` for S1-29, mirroring the auto-link paths.

A malformed transcript id is treated as not-found (404), never a 500; a missing
transcript is 404 and an already-linked one is 409, so the dashboard can tell a
race (someone else just attached it) from a typo.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.auth import CurrentUser, get_current_user
from bullet_api.db import get_session
from bullet_api.db.enums import (
    DOCUMENT_KIND_TRANSCRIPT_TEXT,
    SALES_CALL_TRANSCRIPT_SOURCE_GOOGLE_MEET,
)
from bullet_api.schemas import (
    LinkTranscriptRequest,
    LinkTranscriptResponse,
    UnlinkedTranscriptItem,
    UnlinkedTranscriptsResponse,
)
from bullet_api.transcripts.linking import LINK_METHOD_MANUAL, link_transcript_to_client
from bullet_api.worker import TRANSCRIPT_LINKED_EVENT, EventEmitter, get_event_emitter

transcripts_router = APIRouter(tags=["transcripts"])


@transcripts_router.get("/transcripts/unlinked", response_model=UnlinkedTranscriptsResponse)
async def list_unlinked_transcripts(
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> UnlinkedTranscriptsResponse:
    """List parked transcripts with no client yet, newest capture first. 401
    without a live session."""
    rows = (
        (
            await db.execute(
                text(
                    "SELECT id, source, participant_emails, meeting_start, "
                    "       transcript_chars, captured_at "
                    "FROM sales_call_transcripts "
                    "WHERE client_id IS NULL "
                    "ORDER BY captured_at DESC"
                )
            )
        )
        .mappings()
        .all()
    )
    return UnlinkedTranscriptsResponse(
        transcripts=[
            UnlinkedTranscriptItem(
                id=str(row["id"]),
                source=row["source"],
                participant_emails=list(row["participant_emails"] or []),
                meeting_start=row["meeting_start"],
                transcript_chars=row["transcript_chars"],
                captured_at=row["captured_at"],
            )
            for row in rows
        ]
    )


@transcripts_router.post("/transcripts/{transcript_id}/link", response_model=LinkTranscriptResponse)
async def link_transcript(
    transcript_id: str,
    body: LinkTranscriptRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    emitter: Annotated[EventEmitter, Depends(get_event_emitter)],
) -> LinkTranscriptResponse:
    """Manually attach a parked transcript to a client. 404 unknown transcript /
    malformed id, 409 already linked, 400 unknown client."""
    try:
        tid = uuid.UUID(transcript_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="transcript not found"
        ) from exc
    try:
        client_uuid = uuid.UUID(body.client_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid client_id"
        ) from exc

    existing = (
        await db.execute(
            text("SELECT client_id, r2_key, source FROM sales_call_transcripts WHERE id = :tid"),
            {"tid": tid},
        )
    ).one_or_none()
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transcript not found")
    if existing.client_id is not None:
        # Already linked at the initial check: apply the ONE shared
        # same-vs-different-client rule (see _linked_conflict_or_idempotent).
        return await _linked_conflict_or_idempotent(
            db,
            emitter,
            tid,
            requested_client_id=client_uuid,
            linked_client_id=existing.client_id,
            r2_key=existing.r2_key,
        )

    client_exists = (
        await db.execute(text("SELECT 1 FROM clients WHERE id = :cid"), {"cid": client_uuid})
    ).scalar()
    if client_exists is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown client")

    outcome = await link_transcript_to_client(
        db,
        transcript_id=tid,
        client_id=client_uuid,
        link_method=LINK_METHOD_MANUAL,
        linked_by=user.id,
    )
    if outcome is None:
        # Lost a race: another request linked this transcript between our initial
        # check (saw it unlinked) and the guarded UPDATE (matched 0 rows). Re-read
        # who actually won: the SAME client we asked for -> idempotent re-emit so
        # the event is never dropped; a DIFFERENT client -> genuine conflict (409),
        # NOT a silent 200 echoing the requested client (which would report a
        # wrong-client success while the transcript is linked elsewhere - S1-27b).
        await db.commit()
        winner = (
            await db.execute(
                text("SELECT client_id, r2_key FROM sales_call_transcripts WHERE id = :tid"),
                {"tid": tid},
            )
        ).one_or_none()
        if winner is None or winner.client_id is None:
            # The UPDATE matched 0 rows yet the re-read finds no link. Two ways
            # here: the transcript row was deleted mid-request (winner is None),
            # or the winner's CLIENT was deleted between their commit and our
            # re-read - `clients` FK is ON DELETE SET NULL (migration 0010), so
            # that resets the transcript to unlinked rather than deleting it.
            # Neither is reachable today (no transcript- or client-delete
            # endpoint exists); both surface as 404 so the caller re-fetches
            # the list and retries against current state.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="transcript not found"
            )
        # Linked after the race: apply the same shared rule as the initial check.
        return await _linked_conflict_or_idempotent(
            db,
            emitter,
            tid,
            requested_client_id=client_uuid,
            linked_client_id=winner.client_id,
            r2_key=winner.r2_key,
        )
    await db.commit()

    if outcome.document_id is not None:
        await emitter.send(
            TRANSCRIPT_LINKED_EVENT,
            {
                "client_id": str(outcome.client_id),
                "transcript_id": str(outcome.transcript_id),
                "r2_key": outcome.r2_key,
                "source": outcome.source,
                "document_id": str(outcome.document_id),
            },
        )

    return LinkTranscriptResponse(
        transcript_id=str(outcome.transcript_id),
        client_id=str(outcome.client_id),
        document_id=str(outcome.document_id) if outcome.document_id else None,
    )


async def _linked_conflict_or_idempotent(
    db: AsyncSession,
    emitter: EventEmitter,
    transcript_id: uuid.UUID,
    *,
    requested_client_id: uuid.UUID,
    linked_client_id: uuid.UUID,
    r2_key: str | None,
) -> LinkTranscriptResponse:
    """The ONE same-vs-different-client rule for a transcript observed already
    linked - whether at the initial check or on the lost-race re-read (S1-27b),
    so the two sites cannot drift. Linked to the SAME client the caller asked
    for -> idempotent success with the recovery re-emit (a prior attempt's link
    committed but its post-commit emit may have failed; the event is never
    dropped). Linked to a DIFFERENT client -> 409, never a silent 200 echoing
    the requested client."""
    if linked_client_id == requested_client_id:
        return await _idempotent_link_response(
            db, emitter, transcript_id, requested_client_id, r2_key
        )
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="transcript already linked")


async def _idempotent_link_response(
    db: AsyncSession,
    emitter: EventEmitter,
    transcript_id: uuid.UUID,
    client_id: uuid.UUID,
    r2_key: str | None,
) -> LinkTranscriptResponse:
    """Return the success response for an already-linked (same client) transcript,
    re-emitting transcript.linked from committed state. Closes the manual-path
    at-least-once gap (a prior request committed the link but its emit failed),
    symmetric with the auto-link paths' re-derive-on-retry."""
    document_id: uuid.UUID | None = None
    if r2_key:
        document_id = (
            await db.execute(
                text(
                    "SELECT id FROM documents "
                    "WHERE client_id = :cid AND kind = :kind AND r2_key = :r2_key "
                    "ORDER BY created_at LIMIT 1"
                ),
                {"cid": client_id, "kind": DOCUMENT_KIND_TRANSCRIPT_TEXT, "r2_key": r2_key},
            )
        ).scalar()
    if document_id is not None:
        await emitter.send(
            TRANSCRIPT_LINKED_EVENT,
            {
                "client_id": str(client_id),
                "transcript_id": str(transcript_id),
                "r2_key": r2_key,
                "source": SALES_CALL_TRANSCRIPT_SOURCE_GOOGLE_MEET,
                "document_id": str(document_id),
            },
        )
    return LinkTranscriptResponse(
        transcript_id=str(transcript_id),
        client_id=str(client_id),
        document_id=str(document_id) if document_id else None,
    )
