"""Google Meet transcript webhook receiver (S1-27).

Google Workspace Events publishes Meet events to a Cloud Pub/Sub topic; a push
subscription POSTs them here. This handler is the ingest seam for sales-call
transcripts, and mirrors the PandaDoc receiver's correctness rules:

- The push OIDC token is verified BEFORE any DB write, so a forged request
  leaves no row behind. An unconfigured verifier (empty audience / SA email)
  fails closed (401).
- The `(event_type, external_id)` unique constraint makes ingest idempotent:
  Pub/Sub at-least-once redelivery re-runs cleanly and never double-emits for
  the same transcript.
- The session is committed only AFTER the emit succeeds, so a failed emit rolls
  the row back and Pub/Sub's retry re-runs the whole flow.

`external_id` is the transcript resource name (`conferenceRecords/{c}/transcripts/{t}`),
a globally-unique opaque id. `pandadoc_account` is left to its DB default ('uk')
- it is a PandaDoc-only descriptive column, irrelevant to a Meet event and never
read for one (the `event_type` disambiguates). The route is
`include_in_schema=False`: the dashboard never calls it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.config import get_settings
from bullet_api.db import get_session
from bullet_api.google.pubsub import parse_workspace_event, verify_pubsub_push
from bullet_api.worker import MEET_TRANSCRIPT_READY_EVENT, EventEmitter, get_event_emitter

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Verifier callable: takes the push bearer token, returns True iff it is a valid
# Google OIDC token for our subscription. Isolated behind a dependency so tests
# override it with a known-answer stub (mirrors `get_pandadoc_webhook_secrets`).
PubsubVerifier = Callable[[str], bool]


def get_pubsub_verifier() -> PubsubVerifier:
    settings = get_settings()

    def _verify(token: str) -> bool:
        return verify_pubsub_push(
            token,
            audience=settings.google_pubsub_push_audience,
            sa_email=settings.google_pubsub_push_sa_email,
        )

    return _verify


def _bearer_token(request: Request) -> str:
    """Extract the bearer token from the Authorization header (empty if absent).

    Google attaches the push OIDC token as `Authorization: Bearer <jwt>`.
    """
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token if scheme.lower() == "bearer" else ""


@router.post("/google-meet", include_in_schema=False)
async def receive_google_meet_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    verify: Annotated[PubsubVerifier, Depends(get_pubsub_verifier)],
    emitter: Annotated[EventEmitter, Depends(get_event_emitter)],
) -> dict:
    raw = await request.body()
    # Verify BEFORE any DB write: a forged/tampered push must leave no row.
    if not verify(_bearer_token(request)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid Pub/Sub push token"
        )

    try:
        envelope = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body"
        ) from exc

    try:
        event = parse_workspace_event(envelope)
    except ValueError as exc:
        # Structurally-broken Pub/Sub data (bad base64 / non-JSON inner payload).
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if event is None:
        # Verified push, but not a transcript-ready event (recording events,
        # participant joins, lifecycle, or a payload with no transcript name).
        # Ack so Pub/Sub stops redelivering.
        return {"status": "ignored", "events": 0}

    result = await db.execute(
        text(
            "INSERT INTO onboarding_events (event_type, external_id, payload, verified_at) "
            "VALUES (:et, :eid, cast(:p AS jsonb), now()) "
            "ON CONFLICT (event_type, external_id) DO NOTHING "
            "RETURNING id"
        ),
        {
            "et": MEET_TRANSCRIPT_READY_EVENT,
            "eid": event.transcript_name,
            "p": json.dumps(
                {
                    "transcript_name": event.transcript_name,
                    "conference_record_name": event.conference_record_name,
                }
            ),
        },
    )
    new_id = result.scalar()
    if new_id is None:
        # Replay: a prior push already persisted + fanned out this transcript.
        return {"status": "duplicate", "events": 0}

    await emitter.send(
        MEET_TRANSCRIPT_READY_EVENT,
        {
            "onboarding_event_id": str(new_id),
            "transcript_name": event.transcript_name,
            "conference_record_name": event.conference_record_name,
        },
    )
    # Commit AFTER the emit. If emitter.send raised, the exception propagates,
    # get_session rolls back, no row persists, and Pub/Sub's retry re-runs.
    await db.commit()
    return {"status": "accepted", "events": 1}
