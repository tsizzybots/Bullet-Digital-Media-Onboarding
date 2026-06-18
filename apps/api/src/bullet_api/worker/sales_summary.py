"""S1-29: AI sales-call summary generator.

`summarise_sales_call` is triggered by `transcript.linked` (from S1-27). It
reads the transcript text from R2, asks Claude for the PRD §7.1 structured
summary (validated against `SalesCallSummary`), records the attempt in
`platform_actions`, and emits `sales_summary.ready` carrying the validated
summary for S1-30 to write into `client_knowledge`.

Correctness (mirrors S1-25b `store_signed_pdf` / S1-27 `capture_meet_transcript`):

- **Idempotent** via `platform_actions.idempotency_key`
  (`{client_id}:anthropic:summarise_sales_call:{transcript_id}`). The in_progress
  row is committed BEFORE the LLM call, so a crash mid-call leaves a visible row.
- **Re-emit on retry.** The validated summary is stored on
  `platform_actions.response`. On a replay that short-circuits at
  `already_succeeded` (e.g. a crash after the success commit but before the
  emit), the summary is re-derived from that row and `sales_summary.ready` is
  re-emitted - so S1-30 is never silently skipped. S1-30 must be idempotent per
  transcript.
- **Record `failed` on ANY exception** around the external work (R2 read + LLM
  call), then re-raise so the wrapper classifies retriable-vs-not. DB writes are
  OUTSIDE that try so a failing UPDATE never runs in an aborted transaction.
- **Classification:** missing transcript (R2 404), empty transcript, refusal,
  schema-invalid output, a 400, and an empty API key are NonRetriable (won't
  self-heal). Transport errors, 5xx/429/overloaded, and output truncation
  propagate so Inngest retries.
- **Concurrency:** a global cap bounds parallel Anthropic calls; a
  per-transcript cap of 1 prevents duplicate work for one transcript.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import anthropic
import inngest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.db.session import AsyncSessionLocal
from bullet_api.storage.client import ObjectNotFound, StorageClient, get_storage_client
from bullet_api.worker._inngest import inngest_client
from bullet_api.worker.events import (
    SALES_SUMMARY_READY_EVENT,
    TRANSCRIPT_LINKED_EVENT,
    EventEmitter,
    InngestEventEmitter,
)
from bullet_api.worker.platform_actions import (
    begin_action,
    build_idempotency_key,
    complete_action,
    fail_action,
)
from bullet_api.worker.summary_client import (
    SummaryClient,
    SummaryConfigError,
    SummaryRefusedError,
    get_summary_client,
)

log = logging.getLogger(__name__)

PLATFORM = "anthropic"
SUMMARISE_ACTION = "summarise_sales_call"


class EmptyTranscriptForSummaryError(ValueError):
    """The transcript at the R2 key is empty / whitespace. Summarising it would
    waste a model call and produce nothing, so it is a hard (non-retriable)
    failure."""

    def __init__(self, r2_key: str) -> None:
        self.r2_key = r2_key
        super().__init__(f"Transcript at {r2_key} is empty; nothing to summarise.")


@dataclass(frozen=True)
class SummariseResult:
    """Outcome of one run. `summarised` is True when a fresh LLM summary was
    produced this run; `skipped` is True on a replay short-circuit."""

    summarised: bool
    skipped: bool


async def _emit_summary_ready(
    emitter: EventEmitter,
    *,
    client_id: uuid.UUID,
    transcript_id: uuid.UUID,
    document_id: str | None,
    summary: dict,
) -> None:
    await emitter.send(
        SALES_SUMMARY_READY_EVENT,
        {
            "client_id": str(client_id),
            "transcript_id": str(transcript_id),
            "document_id": document_id,
            "summary": summary,
        },
    )


async def summarise_sales_call_core(
    session: AsyncSession,
    storage: StorageClient,
    summary_client: SummaryClient,
    emitter: EventEmitter,
    *,
    client_id: uuid.UUID,
    transcript_id: uuid.UUID,
    document_id: str | None,
    r2_key: str,
    inngest_run_id: str | None = None,
) -> SummariseResult:
    """Summarise the transcript and emit `sales_summary.ready`.

    Raises (recorded `failed` + committed, then re-raised for the wrapper):
        ObjectNotFound / EmptyTranscriptForSummaryError / SummaryRefusedError /
        pydantic.ValidationError / anthropic.BadRequestError -> non-retriable;
        transport errors / 5xx / SummaryTruncatedError -> retriable.
    """
    # The idempotency key carries the transcript_id so a replay of
    # transcript.linked for the same transcript short-circuits. event_id stays
    # NULL: the column FK-references onboarding_events, and transcript.linked is
    # not backed by an onboarding_events row (transcript_id is a
    # sales_call_transcripts id). The unique idempotency_key is the real guard.
    idempotency_key = build_idempotency_key(client_id, PLATFORM, SUMMARISE_ACTION, transcript_id)
    begun = await begin_action(
        session,
        client_id=client_id,
        event_id=None,
        platform=PLATFORM,
        action=SUMMARISE_ACTION,
        idempotency_key=idempotency_key,
        payload={"transcript_id": str(transcript_id), "r2_key": r2_key},
        inngest_run_id=inngest_run_id,
    )
    # Commit the in_progress row before the (slow) external work so a crash
    # leaves a visible row; the commit also releases the pooled connection.
    await session.commit()

    if begun.already_succeeded:
        # Re-derive the stored summary and re-emit, so a crash between the
        # success commit and the emit on a prior run still drives S1-30.
        stored = (
            await session.execute(
                text("SELECT response FROM platform_actions WHERE id = :id"),
                {"id": begun.action_id},
            )
        ).scalar()
        summary = (stored or {}).get("summary") if isinstance(stored, dict) else None
        if summary is not None:
            await _emit_summary_ready(
                emitter,
                client_id=client_id,
                transcript_id=transcript_id,
                document_id=document_id,
                summary=summary,
            )
        else:
            # A success row with no stored summary is an invariant violation
            # (every complete_action writes response={"summary": ...}). Don't
            # silently skip - surface it so it's visible rather than a quiet
            # no-emit that strands S1-30.
            log.warning(
                "S1-29 replay short-circuit found a success row with no stored summary",
                extra={
                    "client_id": str(client_id),
                    "transcript_id": str(transcript_id),
                    "action_id": str(begun.action_id),
                },
            )
        return SummariseResult(summarised=False, skipped=True)

    # Only the EXTERNAL work is wrapped: an R2 read error or an LLM error flips
    # the row to `failed` (visible) rather than leaving it stuck `in_progress`.
    # DB writes are after the try (same reason as S1-25b).
    try:
        raw = await storage.get_object(r2_key)
        transcript_text = raw.decode("utf-8")
        if not transcript_text.strip():
            raise EmptyTranscriptForSummaryError(r2_key)
        summary_obj = await summary_client.summarise(transcript_text)
    except Exception as exc:
        await fail_action(session, action_id=begun.action_id, last_error=str(exc))
        await session.commit()
        log.warning(
            "S1-29 sales-summary failed",
            extra={
                "client_id": str(client_id),
                "transcript_id": str(transcript_id),
                "action_id": str(begun.action_id),
                "error": str(exc),
            },
        )
        raise

    summary_json = summary_obj.model_dump(mode="json")
    await complete_action(
        session,
        action_id=begun.action_id,
        external_id=str(transcript_id),
        response={"summary": summary_json},
    )
    await session.commit()

    await _emit_summary_ready(
        emitter,
        client_id=client_id,
        transcript_id=transcript_id,
        document_id=document_id,
        summary=summary_json,
    )

    log.info(
        "S1-29 sales-summary produced",
        extra={
            "client_id": str(client_id),
            "transcript_id": str(transcript_id),
            "action_id": str(begun.action_id),
        },
    )
    return SummariseResult(summarised=True, skipped=False)


@inngest_client.create_function(
    fn_id="summarise-sales-call",
    trigger=inngest.TriggerEvent(event=TRANSCRIPT_LINKED_EVENT),
    concurrency=[
        inngest.Concurrency(limit=5, scope="fn"),
        inngest.Concurrency(key="event.data.transcript_id", limit=1, scope="fn"),
    ],
)
async def summarise_sales_call(ctx: inngest.Context, step: inngest.Step) -> dict:
    """Inngest wrapper: build production deps, run the core, classify failures.

    Raises:
        inngest.NonRetriableError: structural / non-self-healing failures
            (R2 404, empty transcript, refusal, schema-invalid, 400, empty key).
        Other exceptions (httpx/5xx/429/overloaded, truncation): propagate so
            Inngest retries.
    """
    client_id = uuid.UUID(ctx.event.data["client_id"])
    transcript_id = uuid.UUID(ctx.event.data["transcript_id"])
    document_id = ctx.event.data.get("document_id")
    r2_key = str(ctx.event.data["r2_key"])

    storage = get_storage_client()
    summary_client = get_summary_client()

    async with AsyncSessionLocal() as session:
        try:
            result = await summarise_sales_call_core(
                session,
                storage,
                summary_client,
                InngestEventEmitter(inngest_client),
                client_id=client_id,
                transcript_id=transcript_id,
                document_id=document_id,
                r2_key=r2_key,
                inngest_run_id=ctx.run_id,
            )
        except (
            ObjectNotFound,
            EmptyTranscriptForSummaryError,
            SummaryRefusedError,
            ValidationError,
            anthropic.BadRequestError,
            SummaryConfigError,
        ) as exc:
            raise inngest.NonRetriableError(str(exc)) from exc
        # SummaryTruncatedError, anthropic.APIStatusError (>=500/429/overloaded),
        # httpx transport errors, and any UNEXPECTED RuntimeError (e.g. a closed
        # event loop) propagate -> Inngest retries. Only the typed
        # SummaryConfigError (empty key) is dead-lettered as non-retriable, so a
        # genuinely transient RuntimeError is never silently swallowed.

    return {
        "client_id": str(client_id),
        "transcript_id": str(transcript_id),
        "summarised": result.summarised,
        "skipped": result.skipped,
    }


__all__ = [
    "PLATFORM",
    "SUMMARISE_ACTION",
    "EmptyTranscriptForSummaryError",
    "SummariseResult",
    "summarise_sales_call",
    "summarise_sales_call_core",
]
