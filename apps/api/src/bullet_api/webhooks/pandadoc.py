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
from bullet_api.pandadoc.accounts import webhook_secrets
from bullet_api.webhooks.pandadoc_core import (
    SignedDocument,
    extract_signed_documents,
    resolve_pandadoc_account,
)
from bullet_api.worker import PANDADOC_SIGNED_EVENT, EventEmitter, get_event_emitter

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def get_pandadoc_webhook_secrets() -> dict[str, str]:
    """FastAPI dependency returning the configured PandaDoc shared keys per
    account (S1-25c), as `{account: secret}` for accounts with a non-empty
    secret. Isolated so tests can override it with known values."""
    return webhook_secrets(get_settings())


async def persist_and_emit_signed_documents(
    documents: list[SignedDocument],
    db: AsyncSession,
    emitter: EventEmitter,
    *,
    account: str,
) -> dict:
    """Upsert each signed document into onboarding_events and emit a
    pandadoc.signed event for every newly-inserted row, then commit.

    `account` is the PandaDoc account the documents came from (S1-25c). It is
    stamped on the `onboarding_events.pandadoc_account` column AND carried on
    the emitted `pandadoc.signed` event so the async workers fetch / download
    with the matching account's API key.

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
        # Idempotency key stays (event_type, external_id) - account is NOT part
        # of it. PandaDoc document ids are globally unique opaque ids (not
        # per-account sequence numbers), so a UK doc and an INT doc can never
        # collide on external_id; `pandadoc_account` is descriptive metadata,
        # not identity. Deliberately excluded from the conflict key: if it were
        # in the key, a replay of the same document with the wrong `?account=`
        # would create a SECOND row for one signing - the opposite of what we
        # want. One document -> exactly one row, whichever account stamped it.
        result = await db.execute(
            text(
                "INSERT INTO onboarding_events "
                "  (event_type, external_id, payload, pandadoc_account, verified_at) "
                "VALUES (:et, :eid, cast(:p AS jsonb), :acct, now()) "
                "ON CONFLICT (event_type, external_id) DO NOTHING "
                "RETURNING id"
            ),
            {
                "et": PANDADOC_SIGNED_EVENT,
                "eid": doc.document_id,
                "p": json.dumps(doc.event),
                "acct": account,
            },
        )
        new_id = result.scalar()
        if new_id is None:
            continue  # replay: a prior request already persisted + fanned out this document
        await emitter.send(
            PANDADOC_SIGNED_EVENT,
            {
                "document_id": doc.document_id,
                "onboarding_event_id": str(new_id),
                "account": account,
            },
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
    secrets: Annotated[dict[str, str], Depends(get_pandadoc_webhook_secrets)],
    emitter: Annotated[EventEmitter, Depends(get_event_emitter)],
) -> dict:
    raw = await request.body()
    signature = request.query_params.get("signature")
    # Try each configured account's shared key; the account is whichever
    # verifies (S1-25c). None -> no key matched (or none configured) -> 401.
    account = resolve_pandadoc_account(raw, signature, secrets)
    if account is None:
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

    return await persist_and_emit_signed_documents(documents, db, emitter, account=account)
