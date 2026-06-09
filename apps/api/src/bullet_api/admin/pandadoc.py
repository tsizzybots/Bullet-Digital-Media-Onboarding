"""PandaDoc manual replay endpoint (S1-24).

An authenticated escape hatch to re-drive the signing fan-out for a
document that is already completed in PandaDoc but whose webhook was missed
(a PandaDoc outage, a deploy window, a subscription mis-config, or a
document signed before the S1-22 webhook existed).

The replay FETCHES the document from the PandaDoc REST API, builds a
synthetic webhook event from its current state, and pushes it through the
EXACT same persist + emit path as the live webhook
(`persist_and_emit_signed_documents`), so the `onboarding_events` audit row
and the `pandadoc.signed` fan-out trigger are produced identically and
idempotently. There is no HMAC here - the caller is an authenticated
founder / engineer, not PandaDoc - and no internal HTTP self-call.

Correctness mirrors the webhook:

- 401 with no live session, 403 for any role other than founder /
  izzyagents_engineer - both raised by the role gate BEFORE the document is
  fetched, so a forbidden caller triggers no API call, no row, no emit.
- 404 when PandaDoc has no such document.
- A document that exists but is not yet `document.completed` is acked and
  ignored (no row, no emit), exactly like a non-signed webhook event.
- A completed document is persisted + fanned out exactly once; a second
  replay hits the `(event_type, external_id)` unique constraint and returns
  `duplicate` with no second emit.

The route is `include_in_schema=False` for the same reason as the webhook:
the dashboard does not call it in Phase 1, so it stays out of the generated
TS client.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.auth import CurrentUser, require_role
from bullet_api.db import get_session
from bullet_api.pandadoc import PandaDocClient, PandaDocNotFound, get_pandadoc_client
from bullet_api.pandadoc.accounts import PANDADOC_ACCOUNT_UK, PandaDocAccount
from bullet_api.webhooks.pandadoc import persist_and_emit_signed_documents
from bullet_api.webhooks.pandadoc_core import extract_signed_documents
from bullet_api.worker import EventEmitter, get_event_emitter

router = APIRouter(prefix="/admin", tags=["admin"])

# Founder + IzzyAgents engineer only. Any other role (account_manager,
# performance_director) gets 403; no/expired session gets 401.
require_founder_or_engineer = require_role("founder", "izzyagents_engineer")


@router.post("/pandadoc/replay/{document_id}", include_in_schema=False)
async def replay_pandadoc_document(
    document_id: str,
    _user: Annotated[CurrentUser, Depends(require_founder_or_engineer)],
    db: Annotated[AsyncSession, Depends(get_session)],
    client: Annotated[PandaDocClient, Depends(get_pandadoc_client)],
    emitter: Annotated[EventEmitter, Depends(get_event_emitter)],
    account: Annotated[PandaDocAccount, Query()] = PANDADOC_ACCOUNT_UK,
) -> dict:
    """Re-drive the signing fan-out for an already-completed PandaDoc document.

    `account` (S1-25c, query param, default `uk`) selects which PandaDoc
    account to fetch from - the injected `client` is bound to that account's
    API key (see `get_pandadoc_client`), and the account is stamped on the
    `onboarding_events` row + carried on the emitted `pandadoc.signed` event so
    the fan-out uses the matching key. FastAPI validates `account` (422 for any
    value other than `uk`/`int`).

    Returns the same envelope as the webhook:
    `{"status": "accepted"|"duplicate"|"ignored", "events": N}`.
    """
    try:
        document = await client.fetch_document(document_id)
    except PandaDocNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PandaDoc document {document_id} not found",
        ) from exc

    # Build a synthetic webhook event from the fetched document's current
    # state and run it through the same extractor the webhook uses, so the
    # completed-status gate and the SignedDocument shape stay identical.
    synthetic_event = {
        "event": "replay",
        "data": {"id": document.id, "name": document.name, "status": document.status},
    }
    documents = extract_signed_documents([synthetic_event])
    if not documents:
        # Found but not signed/completed -> ack and ignore, like the webhook.
        return {"status": "ignored", "events": 0}

    return await persist_and_emit_signed_documents(documents, db, emitter, account=account)
