"""S1-25b: store the signed PDF in R2 + `documents` on `client.created`.

`store_signed_pdf` is the second onboarding fan-out (sibling of S1-25's
GHL sub-account creation, runs in parallel off the same `client.created`
event). It downloads the signed PDF from PandaDoc, stores the bytes in
Cloudflare R2, and writes a `documents` row (kind `pandadoc_signed_pdf`)
pointing at the R2 object. Fills the gap where the signed PDF was never
persisted (the S1-22 webhook only kept the raw event JSON).

Correctness rules (mirrors S1-25, with the S1-25 review lessons baked in):

- **Idempotent via `platform_actions.idempotency_key`** plus a deterministic
  R2 key (re-upload overwrites, no duplicate object) plus a guarded
  `documents` insert (`WHERE NOT EXISTS`, no duplicate row, no migration). A
  replay short-circuits on the already-succeeded action.
- **Commit the `in_progress` row BEFORE the download/upload**, then commit
  the terminal state after - partial failures stay visible, never silent.
- **Record `failed` on ANY exception**, not just typed `PandaDocNotFound`:
  the download/upload are wrapped in `except Exception` so a transport-level
  error (httpx timeout / connection reset, a botocore error) flips the row to
  `failed` rather than leaving it stuck `in_progress`, then re-raises
  unchanged so the wrapper classifies retriable-vs-not. (This is the S1-25
  hardening lesson applied up-front.)
- **Insert the `documents` row only AFTER a successful upload**, so a failed
  upload never leaves a row pointing at a missing object. An empty/zero-byte
  download is treated as a hard failure for the same reason.
- **Concurrency**: a global cap bounds parallel downloads/uploads; a
  per-client cap of 1 prevents concurrent duplicate work for one client.

Pure testable core `store_signed_pdf_core` (session + clients + ids) plus a
thin Inngest-bound wrapper, matching `create_ghl_subaccount`.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

import inngest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.config import get_settings
from bullet_api.db.enums import DOCUMENT_KIND_PANDADOC_SIGNED_PDF
from bullet_api.db.session import AsyncSessionLocal
from bullet_api.pandadoc.accounts import PANDADOC_ACCOUNT_UK, api_key_for
from bullet_api.pandadoc.client import HttpPandaDocClient, PandaDocClient, PandaDocNotFound
from bullet_api.storage.client import StorageClient, get_storage_client
from bullet_api.worker._inngest import inngest_client
from bullet_api.worker.events import CLIENT_CREATED_EVENT
from bullet_api.worker.platform_actions import (
    begin_action,
    build_idempotency_key,
    complete_action,
    fail_action,
)

log = logging.getLogger(__name__)

PANDADOC_PLATFORM = "pandadoc"
STORE_SIGNED_PDF_ACTION = "store_signed_pdf"
SIGNED_PDF_CONTENT_TYPE = "application/pdf"


class ClientNotFoundError(LookupError):
    """The `client.created` event references a `clients` row that does not exist.

    The orchestrator commits the client row before emitting `client.created`,
    so a missing row is a data-consistency bug that cannot self-heal on
    retry; the Inngest wrapper translates it to `inngest.NonRetriableError`.
    """

    def __init__(self, client_id: uuid.UUID) -> None:
        self.client_id = client_id
        super().__init__(f"clients row {client_id} not found; cannot store signed PDF.")


class EmptyPdfError(ValueError):
    """PandaDoc returned an empty body for the signed PDF.

    Storing a zero-byte object + a `documents` row pointing at it would be
    worse than failing, so this is a hard (non-retriable) failure.
    """

    def __init__(self, document_id: str) -> None:
        self.document_id = document_id
        super().__init__(f"PandaDoc returned an empty PDF body for document {document_id}.")


@dataclass(frozen=True)
class StoreSignedPdfResult:
    """Outcome of one `store_signed_pdf_core` run.

    `r2_key` is the stored object key (also the `platform_actions.external_id`).
    `stored` is True when a fresh download+upload+row happened on this run;
    `skipped` is True on a replay that short-circuited.
    """

    r2_key: str | None
    stored: bool
    skipped: bool


def build_signed_pdf_key(client_id: uuid.UUID, document_id: str) -> str:
    """Deterministic R2 key for a client's signed PDF.

    Deterministic so a retried run overwrites the same object instead of
    creating a duplicate.
    """
    return f"signed-agreements/{client_id}/{document_id}.pdf"


async def store_signed_pdf_core(
    session: AsyncSession,
    pandadoc_client: PandaDocClient,
    storage: StorageClient,
    *,
    client_id: uuid.UUID,
    onboarding_event_id: uuid.UUID | None,
    document_id: str,
    inngest_run_id: str | None = None,
) -> StoreSignedPdfResult:
    """Download the signed PDF, store it in R2, write a `documents` row.

    Raises:
        ClientNotFoundError: no `clients` row for `client_id`. Rolls back.
        PandaDocNotFound / EmptyPdfError / any download or upload error:
            recorded `failed` + committed, then re-raised so the wrapper
            decides retriable-vs-not.
    """
    client = (
        await session.execute(
            text("SELECT id FROM clients WHERE id = :client_id"),
            {"client_id": client_id},
        )
    ).one_or_none()
    if client is None:
        raise ClientNotFoundError(client_id)

    idempotency_key = build_idempotency_key(
        client_id, PANDADOC_PLATFORM, STORE_SIGNED_PDF_ACTION, onboarding_event_id
    )
    r2_key = build_signed_pdf_key(client_id, document_id)

    begun = await begin_action(
        session,
        client_id=client_id,
        event_id=onboarding_event_id,
        platform=PANDADOC_PLATFORM,
        action=STORE_SIGNED_PDF_ACTION,
        idempotency_key=idempotency_key,
        payload={"document_id": document_id, "r2_key": r2_key},
        inngest_run_id=inngest_run_id,
    )
    # COMMIT the in_progress row before the external calls so a crash mid
    # download/upload leaves a visible row rather than a silent gap.
    await session.commit()

    if begun.already_succeeded:
        return StoreSignedPdfResult(r2_key=r2_key, stored=False, skipped=True)

    # Only the EXTERNAL work (download + upload) is wrapped here, so a broad
    # `except` records `failed` for a transport-level error (httpx timeout /
    # reset on download, botocore error on upload) that is NOT a typed
    # PandaDocNotFound - rather than leaving the row stuck `in_progress`. The DB
    # writes below are deliberately OUTSIDE this block: a DB write that failed
    # *inside* the try would abort the transaction, and the `except`'s own
    # `fail_action` UPDATE would then error on the aborted transaction and never
    # record. Keeping the realistic failure surface (the external calls) in the
    # try and the DB writes after mirrors S1-25 and sidesteps that.
    try:
        body = await pandadoc_client.download_document(document_id)
        if not body:
            raise EmptyPdfError(document_id)
        external_url = await storage.put_object(r2_key, body, SIGNED_PDF_CONTENT_TYPE)
    except Exception as exc:
        await fail_action(session, action_id=begun.action_id, last_error=str(exc))
        await session.commit()
        log.warning(
            "S1-25b signed-PDF storage failed",
            extra={
                "client_id": str(client_id),
                "document_id": document_id,
                "action_id": str(begun.action_id),
                "error": str(exc),
            },
        )
        raise

    # DB writes after a successful upload. Guarded insert => idempotent without a
    # migration (a replay or a retry after a crash between upload and commit
    # inserts nothing the second time); insert runs only after a successful
    # upload, so no row ever points at a missing object. If these DB writes fail
    # (rare - DB down), the exception propagates uncaught, the row stays
    # `in_progress`, and Inngest retries: begin_action sees not-success ->
    # re-download/upload (same key, idempotent) -> guarded insert (no dup) ->
    # converges. (Same posture as S1-25's post-call DB writes.)
    await session.execute(
        text(
            "INSERT INTO documents (client_id, kind, external_url, r2_key, metadata) "
            "SELECT :client_id, :kind, :external_url, :r2_key, cast(:metadata AS jsonb) "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM documents "
            "  WHERE client_id = :client_id AND kind = :kind AND r2_key = :r2_key"
            ")"
        ),
        {
            "client_id": client_id,
            "kind": DOCUMENT_KIND_PANDADOC_SIGNED_PDF,
            "external_url": external_url,
            "r2_key": r2_key,
            "metadata": json.dumps(
                {
                    "document_id": document_id,
                    "content_type": SIGNED_PDF_CONTENT_TYPE,
                    "size_bytes": len(body),
                    "source": "pandadoc",
                }
            ),
        },
    )
    await complete_action(
        session,
        action_id=begun.action_id,
        external_id=r2_key,
        response={"r2_key": r2_key, "external_url": external_url, "size_bytes": len(body)},
    )
    await session.commit()

    log.info(
        "S1-25b signed PDF stored",
        extra={
            "client_id": str(client_id),
            "document_id": document_id,
            "r2_key": r2_key,
            "action_id": str(begun.action_id),
        },
    )
    return StoreSignedPdfResult(r2_key=r2_key, stored=True, skipped=False)


@inngest_client.create_function(
    fn_id="store-signed-pdf",
    trigger=inngest.TriggerEvent(event=CLIENT_CREATED_EVENT),
    concurrency=[
        # Global cap: bound parallel PandaDoc downloads + R2 uploads. Enforced
        # server-side across all worker processes/instances.
        inngest.Concurrency(limit=5, scope="fn"),
        # Per-client cap: one storage run in flight per client.
        inngest.Concurrency(key="event.data.client_id", limit=1, scope="fn"),
    ],
)
async def store_signed_pdf(ctx: inngest.Context, step: inngest.Step) -> dict:
    """Inngest wrapper: build production deps, run the core, classify failures.

    Raises:
        inngest.NonRetriableError: structural / non-self-healing failures
            (missing client row, PandaDoc 404, empty PDF, empty API key /
            R2 misconfig). Inngest dead-letters.
        Other exceptions (httpx 5xx/429/timeout, botocore transient errors):
            propagate so Inngest retries.
    """
    client_id = uuid.UUID(ctx.event.data["client_id"])
    document_id = str(ctx.event.data["document_id"])
    raw_event_id = ctx.event.data.get("onboarding_event_id")
    onboarding_event_id = uuid.UUID(raw_event_id) if raw_event_id else None
    # PandaDoc account this signing came from (S1-25c), propagated on
    # client.created. Default UK for events emitted before S1-25c.
    account = ctx.event.data.get("account", PANDADOC_ACCOUNT_UK)

    settings = get_settings()
    pandadoc_client = HttpPandaDocClient(
        api_key=api_key_for(account, settings),
        base_url=settings.pandadoc_api_base_url,
    )
    storage = get_storage_client()

    async with AsyncSessionLocal() as session:
        try:
            result = await store_signed_pdf_core(
                session,
                pandadoc_client,
                storage,
                client_id=client_id,
                onboarding_event_id=onboarding_event_id,
                document_id=document_id,
                inngest_run_id=ctx.run_id,
            )
        except (ClientNotFoundError, PandaDocNotFound, EmptyPdfError) as exc:
            raise inngest.NonRetriableError(str(exc)) from exc
        except RuntimeError as exc:
            # Empty PANDADOC_API_KEY or unconfigured R2 - a misconfigured
            # deploy that cannot self-heal on retry.
            raise inngest.NonRetriableError(str(exc)) from exc

    return {
        "client_id": str(client_id),
        "document_id": document_id,
        "r2_key": result.r2_key,
        "stored": result.stored,
        "skipped": result.skipped,
        "account": account,
    }


__all__ = [
    "PANDADOC_PLATFORM",
    "STORE_SIGNED_PDF_ACTION",
    "ClientNotFoundError",
    "EmptyPdfError",
    "StoreSignedPdfResult",
    "build_signed_pdf_key",
    "store_signed_pdf",
    "store_signed_pdf_core",
]
