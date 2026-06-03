"""PandaDoc webhook receiver (S1-22).

PandaDoc fires an HTTP POST at this endpoint whenever a subscribed
document changes state. This handler is the ingest seam: it verifies the
HMAC signature, persists every newly-completed document as an
`onboarding_events` row, and emits a `pandadoc.signed` event so the
background worker can fan the signing out to Slack, Asana, Stripe, etc.

Correctness rules baked into the flow:

- The signature is checked BEFORE any database write, so a forged or
  tampered request leaves no row behind.
- The `(event_type, external_id)` unique constraint makes ingest
  idempotent: PandaDoc retries (and at-least-once delivery) re-run
  cleanly and never double-emit for the same document.
- The session is committed only AFTER the emit succeeds. If `emitter.send`
  raises, the exception propagates, `get_session` rolls back, no row
  persists, and PandaDoc's retry re-runs the whole flow.
- `verified_at` is stamped (`now()`) in the INSERT, on a path only
  reachable once the signature check has passed.

The route is `include_in_schema=False`: the dashboard never calls it, so
keeping it out of the OpenAPI document avoids drifting the generated TS
client.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.config import get_settings
from bullet_api.db import get_session
from bullet_api.webhooks.pandadoc_core import (
    SignedDocument,
    extract_signed_documents,
    verify_pandadoc_signature,
)
from bullet_api.worker import PANDADOC_SIGNED_EVENT, EventEmitter, get_event_emitter

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def get_pandadoc_secret() -> str:
    """FastAPI dependency returning the configured PandaDoc shared key.

    Isolated so tests can override it with a known value."""
    return get_settings().pandadoc_webhook_secret


async def persist_and_emit_signed_documents(
    documents: list[SignedDocument],
    db: AsyncSession,
    emitter: EventEmitter,
) -> dict:
    """Upsert each signed document into onboarding_events and emit a
    pandadoc.signed event for every newly-inserted row, then commit.

    Idempotent via the (event_type, external_id) unique constraint: a
    document already persisted hits ON CONFLICT DO NOTHING, is not
    re-emitted, and counts as a duplicate. Commits only AFTER the emit(s)
    succeed so a failed emit rolls the whole batch back (caller's
    get_session dependency handles rollback) and the source can retry.

    Returns {"status": "accepted"|"duplicate", "events": <int>}.
    Assumes `documents` is non-empty (callers handle the empty/"ignored"
    case before calling)."""
    accepted = 0
    for doc in documents:
        result = await db.execute(
            text(
                "INSERT INTO onboarding_events "
                "  (event_type, external_id, payload, verified_at) "
                "VALUES (:et, :eid, cast(:p AS jsonb), now()) "
                "ON CONFLICT (event_type, external_id) DO NOTHING "
                "RETURNING id"
            ),
            {"et": PANDADOC_SIGNED_EVENT, "eid": doc.document_id, "p": json.dumps(doc.event)},
        )
        new_id = result.scalar()
        if new_id is None:
            continue  # replay: a prior request already persisted + fanned out this document
        await emitter.send(
            PANDADOC_SIGNED_EVENT,
            {"document_id": doc.document_id, "onboarding_event_id": str(new_id)},
        )
        accepted += 1

    # Commit AFTER the emit(s) succeed. If emitter.send raised, the exception
    # propagates here, get_session rolls back, no row persists, and PandaDoc's
    # retry re-runs cleanly. verified_at is stamped (now()) only on this path,
    # which is only reached after the signature check passed.
    await db.commit()
    return {"status": "accepted" if accepted else "duplicate", "events": accepted}


@router.post("/pandadoc", include_in_schema=False)
async def receive_pandadoc_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    secret: Annotated[str, Depends(get_pandadoc_secret)],
    emitter: Annotated[EventEmitter, Depends(get_event_emitter)],
) -> dict:
    raw = await request.body()
    signature = request.query_params.get("signature")
    if not verify_pandadoc_signature(raw, signature, secret):
        # 401 BEFORE any DB write: a forged/tampered request must leave no row.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body"
        ) from exc

    documents = extract_signed_documents(payload)
    if not documents:
        # Valid signature but no signed/completed document in the batch -> ack and ignore
        # (PandaDoc fires many non-signed states at the same endpoint).
        return {"status": "ignored", "events": 0}

    return await persist_and_emit_signed_documents(documents, db, emitter)
