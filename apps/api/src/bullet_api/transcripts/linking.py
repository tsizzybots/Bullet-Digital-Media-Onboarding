"""Attach a parked transcript to a client - the one shared link path (S1-27).

All three ways a `sales_call_transcripts` row becomes linked funnel through
`link_transcript_to_client`:
- the capture worker's immediate email match (`email_immediate`),
- the signing-time backfill in `create_client_record_core` (`email_signing`),
- the manual assign endpoint (`manual`).

The helper performs the guarded link UPDATE + the guarded `documents` insert in
the caller's transaction, and returns what the caller needs to emit
`transcript.linked` AFTER it commits (the emitter is the caller's concern so the
commit-before-emit ordering stays in one place). It NEVER commits.

Idempotency / replay safety:
- The link UPDATE is `WHERE id = :tid AND client_id IS NULL`, so a replay (or a
  race that already linked the row) updates 0 rows and returns None - the caller
  does not re-link or double-emit.
- The `documents` insert is guarded `WHERE NOT EXISTS` on (client_id, kind,
  r2_key), so a retry after a crash between insert and commit inserts nothing
  the second time. No unique constraint / migration needed (mirrors S1-25b).

A transcript whose capture has not finished (r2_key NULL) is linked but gets NO
`documents` row and NO emit (there is no stored text to summarise yet); the
outcome's `document_id` is None and the caller skips the emit. In practice the
call is captured minutes after it happens and linked days later at signing, so
this window is rare; it is documented rather than special-cased.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.db.enums import DOCUMENT_KIND_TRANSCRIPT_TEXT

LINK_METHOD_EMAIL_IMMEDIATE = "email_immediate"
LINK_METHOD_EMAIL_SIGNING = "email_signing"
LINK_METHOD_MANUAL = "manual"


@dataclass(frozen=True)
class LinkOutcome:
    """Result of a successful link. `document_id` is None when the transcript
    had no stored text yet (r2_key NULL) so no `documents` row was created and
    the caller should not emit `transcript.linked`."""

    transcript_id: uuid.UUID
    client_id: uuid.UUID
    source: str
    r2_key: str | None
    document_id: uuid.UUID | None


async def link_transcript_to_client(
    session: AsyncSession,
    *,
    transcript_id: uuid.UUID,
    client_id: uuid.UUID,
    link_method: str,
    linked_by: uuid.UUID | None = None,
) -> LinkOutcome | None:
    """Link one parked transcript to a client. Returns the outcome, or None if
    the transcript was already linked / does not exist (the guarded UPDATE
    matched nothing). Does NOT commit; does NOT emit."""
    linked = (
        await session.execute(
            text(
                "UPDATE sales_call_transcripts "
                "SET client_id = :client_id, linked_at = now(), "
                "    link_method = :link_method, linked_by = :linked_by "
                "WHERE id = :tid AND client_id IS NULL "
                "RETURNING source, r2_key"
            ),
            {
                "client_id": client_id,
                "link_method": link_method,
                "linked_by": linked_by,
                "tid": transcript_id,
            },
        )
    ).one_or_none()
    if linked is None:
        return None

    source: str = linked.source
    r2_key: str | None = linked.r2_key

    document_id: uuid.UUID | None = None
    if r2_key:
        document_id = await _ensure_transcript_document(
            session,
            client_id=client_id,
            r2_key=r2_key,
            transcript_id=transcript_id,
            source=source,
        )

    return LinkOutcome(
        transcript_id=transcript_id,
        client_id=client_id,
        source=source,
        r2_key=r2_key,
        document_id=document_id,
    )


async def _ensure_transcript_document(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    r2_key: str,
    transcript_id: uuid.UUID,
    source: str,
) -> uuid.UUID:
    """Insert the `documents` row (kind transcript_text) for a linked transcript
    if absent, and return its id.

    Idempotent via the partial UNIQUE index `uq_documents_transcript_text`
    (migration 0010) + `ON CONFLICT DO NOTHING`: two concurrent linkers cannot
    both insert (the constraint serialises them; the loser's INSERT is a no-op).
    On conflict the constraint guarantees the winning row is committed, so the
    fallback SELECT always finds it - no READ-COMMITTED phantom read."""
    inserted = (
        await session.execute(
            text(
                "INSERT INTO documents (client_id, kind, r2_key, metadata) "
                "VALUES (:client_id, :kind, :r2_key, cast(:metadata AS jsonb)) "
                "ON CONFLICT (client_id, kind, r2_key) "
                "  WHERE kind = 'transcript_text' DO NOTHING "
                "RETURNING id"
            ),
            {
                "client_id": client_id,
                "kind": DOCUMENT_KIND_TRANSCRIPT_TEXT,
                "r2_key": r2_key,
                "metadata": json.dumps({"source": source, "transcript_id": str(transcript_id)}),
            },
        )
    ).scalar()
    if inserted is not None:
        return inserted
    # Conflict: a row already exists (committed, per the constraint) - fetch it.
    existing = (
        await session.execute(
            text(
                "SELECT id FROM documents "
                "WHERE client_id = :client_id AND kind = :kind AND r2_key = :r2_key "
                "ORDER BY created_at LIMIT 1"
            ),
            {
                "client_id": client_id,
                "kind": DOCUMENT_KIND_TRANSCRIPT_TEXT,
                "r2_key": r2_key,
            },
        )
    ).scalar_one()
    return existing


__all__ = [
    "LINK_METHOD_EMAIL_IMMEDIATE",
    "LINK_METHOD_EMAIL_SIGNING",
    "LINK_METHOD_MANUAL",
    "LinkOutcome",
    "link_transcript_to_client",
]
