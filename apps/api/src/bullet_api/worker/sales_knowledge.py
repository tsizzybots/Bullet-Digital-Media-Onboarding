"""S1-30: write the sales-call summary into client_knowledge with embeddings.

`store_sales_knowledge` is triggered by `sales_summary.ready` (from S1-29). For
each of the seven PRD §7.1 fields it writes one `client_knowledge` row
(`source='sales_call'`) - key = the field name, `value` = the field's JSONB
shape, `value_text` = a prose rendering - and stores an OpenAI embedding of each
non-empty `value_text` in the `embedding vector(1536)` column. All seven rows
are inserted in ONE transaction with ONE shared `captured_at`.

Correctness (mirrors S1-29 `summarise_sales_call`):

- **One atomic batch.** S1-32's read (`_GET_SUMMARY_SQL`) returns only the latest
  `captured_at` batch, so a per-row-committing writer could expose a PARTIAL
  batch. All seven rows share one `captured_at` and commit together.
- **Idempotent** via `platform_actions.idempotency_key`
  (`{client_id}:openai:store_sales_knowledge:{transcript_id}:{summary_hash}`). The
  in_progress row is committed BEFORE the embedding call. Keying on a hash of the
  summary means an at-least-once re-emit of the SAME summary dedupes, but a
  corrected re-link that produces a DIFFERENT summary writes a fresh batch.
- **Record `failed` on ANY exception** around the external work (summary
  validation + embedding), then re-raise so the wrapper classifies
  retriable-vs-not. The client_knowledge INSERT is after that try.
- **Classification:** a malformed event, a schema-invalid summary, an empty
  OPENAI_API_KEY, and a structural OpenAI 4xx (bad/expired key 401, disabled
  403, unknown model 404) are NonRetriable. Transport errors, 429, and 5xx
  propagate so Inngest retries.
- **Concurrency:** a global cap + a per-transcript cap of 1; plus a back-off
  guard so a redeploy / at-least-once race does not double-spend embeddings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import inngest
import openai
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.db.enums import CLIENT_KNOWLEDGE_SOURCE_SALES_CALL
from bullet_api.db.session import AsyncSessionLocal
from bullet_api.summary.models import SalesCallSummary
from bullet_api.worker._inngest import inngest_client
from bullet_api.worker.embedding_client import (
    EMBEDDING_DIM,
    EmbeddingClient,
    EmbeddingConfigError,
    get_embedding_client,
)
from bullet_api.worker.events import SALES_SUMMARY_READY_EVENT
from bullet_api.worker.platform_actions import (
    STATUS_IN_PROGRESS,
    begin_action,
    build_idempotency_key,
    complete_action,
    fail_action,
    reclaim_stale_action,
)

log = logging.getLogger(__name__)

PLATFORM = "openai"
STORE_ACTION = "store_sales_knowledge"

# A run older than this is presumed dead, so a concurrent run may reclaim its
# in_progress row rather than backing off forever. Sized well above the embedding
# call's duration (sub-second) with generous margin.
STALE_IN_PROGRESS = timedelta(minutes=15)

_INSERT_KNOWLEDGE_SQL = text(
    "INSERT INTO client_knowledge "
    "(client_id, source, key, value, value_text, embedding, captured_at) "
    "VALUES ("
    "  :client_id, :source, :key, cast(:value AS jsonb), :value_text, "
    "  cast(:embedding AS vector(1536)), :captured_at"
    ")"
)


class ConcurrentKnowledgeInProgress(RuntimeError):
    """Another live run already holds this summary's action `in_progress`.

    RETRIABLE by design: back off instead of double-spending the embedding call.
    On the retry the winning run has committed `success`, so the replay
    short-circuits."""

    def __init__(self, transcript_id: uuid.UUID) -> None:
        self.transcript_id = transcript_id
        super().__init__(
            f"Knowledge for transcript {transcript_id} is already in progress; backing off."
        )


@dataclass(frozen=True)
class StoreKnowledgeResult:
    """Outcome of one run. `stored` is True when a fresh batch was written this
    run; `skipped` is True on a replay short-circuit. `rows_written` is the row
    count on a fresh write (0 on a skip)."""

    stored: bool
    skipped: bool
    rows_written: int


def _budget_text(summary: SalesCallSummary) -> str:
    budget = summary.budget_range_usd
    if budget is None:
        return ""
    return f"{budget.min:g}-{budget.max:g} {budget.currency} / month"


def render_knowledge_fields(summary: SalesCallSummary) -> list[tuple[str, object, str]]:
    """Map the §7.1 summary to seven (key, value, value_text) rows, in a fixed
    order. `value` is the field's JSONB shape (a None budget -> JSON null, still
    a valid NOT NULL value); `value_text` is the prose we embed + the dashboard's
    fallback (empty when the field carries nothing)."""
    budget = summary.budget_range_usd
    return [
        ("business_type", summary.business_type, summary.business_type),
        ("business_goals", summary.business_goals, "; ".join(summary.business_goals)),
        (
            "budget_range_usd",
            budget.model_dump(mode="json") if budget is not None else None,
            _budget_text(summary),
        ),
        ("pain_points", summary.pain_points, "; ".join(summary.pain_points)),
        ("red_flags", summary.red_flags, "; ".join(summary.red_flags)),
        ("next_steps", summary.next_steps, "; ".join(summary.next_steps)),
        (
            "notable_quotes",
            [q.model_dump(mode="json") for q in summary.notable_quotes],
            "\n".join(f"{q.speaker}: {q.quote}" for q in summary.notable_quotes),
        ),
    ]


def _vector_literal(vector: list[float]) -> str:
    """pgvector text form `[f1,f2,...]` for a `cast(:v AS vector(1536))` bind."""
    return "[" + ",".join(str(float(x)) for x in vector) + "]"


def _summary_hash(summary: object) -> str:
    """Stable short hash of the summary payload for the idempotency key. Canonical
    (sorted-key) JSON so key order does not change the hash; a CHANGED summary
    (corrected re-link) hashes differently and writes a fresh batch."""
    canonical = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


async def store_sales_knowledge_core(
    session: AsyncSession,
    embedder: EmbeddingClient,
    *,
    client_id: uuid.UUID,
    transcript_id: uuid.UUID,
    document_id: str | None,
    summary: object,
    inngest_run_id: str | None = None,
) -> StoreKnowledgeResult:
    """Write the summary into client_knowledge with embeddings.

    Raises:
        pydantic.ValidationError - schema-invalid summary; raised BEFORE the
            platform_actions row (a contract violation, like a malformed event)
            -> wrapper dead-letters (non-retriable).
        EmbeddingConfigError - empty key OR a wrong-dimension model; recorded
            `failed` -> non-retriable.
        openai transport / 429 / 5xx, a DB persist error - recorded `failed`
            -> retriable.
    """
    # Validate + canonicalise BEFORE building the idempotency key so the dedupe
    # hash is a function of the SEMANTIC content, not the emitter's wire
    # representation - a re-emit of an equal summary always dedupes even if the
    # serialization differs (float vs int, key order, dropped defaults). A
    # schema-invalid summary raises here (no platform_actions row yet) and the
    # wrapper dead-letters it, same as a malformed event - S1-29 validates before
    # emitting, so this is a contract violation, not an expected path.
    summary_obj = SalesCallSummary.model_validate(summary)
    canonical = summary_obj.model_dump(mode="json")
    idempotency_key = build_idempotency_key(
        client_id, PLATFORM, STORE_ACTION, transcript_id, _summary_hash(canonical)
    )
    begun = await begin_action(
        session,
        client_id=client_id,
        event_id=None,
        platform=PLATFORM,
        action=STORE_ACTION,
        idempotency_key=idempotency_key,
        payload={"transcript_id": str(transcript_id), "document_id": document_id},
        inngest_run_id=inngest_run_id,
    )
    await session.commit()

    if begun.already_succeeded:
        # The batch is already written; nothing downstream consumes this, so a
        # replay is a clean no-op (unlike S1-29 there is no event to re-emit).
        return StoreKnowledgeResult(stored=False, skipped=True, rows_written=0)

    # Concurrency guard: a fresh in_progress we did not insert means another live
    # run holds it -> back off (retriable) rather than double-spending embeddings.
    # A stale in_progress (prior run crashed) is reclaimed.
    if not begun.inserted and begun.status == STATUS_IN_PROGRESS:
        age = datetime.now(UTC) - begun.started_at
        if age < STALE_IN_PROGRESS:
            log.info(
                "S1-30 backing off; knowledge write already in progress",
                extra={
                    "client_id": str(client_id),
                    "transcript_id": str(transcript_id),
                    "action_id": str(begun.action_id),
                    "in_progress_age_seconds": age.total_seconds(),
                },
            )
            raise ConcurrentKnowledgeInProgress(transcript_id)
        # Stale: ATOMICALLY claim it (compare-and-swap on started_at). Only one
        # concurrent reclaimer wins; a loser gets False and backs off, so two
        # overlapping runs cannot both re-do the work + double-spend embeddings.
        claimed = await reclaim_stale_action(
            session, action_id=begun.action_id, seen_started_at=begun.started_at
        )
        await session.commit()
        if not claimed:
            log.info(
                "S1-30 lost the stale-reclaim race; backing off",
                extra={
                    "client_id": str(client_id),
                    "transcript_id": str(transcript_id),
                    "action_id": str(begun.action_id),
                },
            )
            raise ConcurrentKnowledgeInProgress(transcript_id)
        log.warning(
            "S1-30 reclaimed a stale in_progress knowledge write (prior run presumed dead)",
            extra={
                "client_id": str(client_id),
                "transcript_id": str(transcript_id),
                "action_id": str(begun.action_id),
                "in_progress_age_seconds": age.total_seconds(),
            },
        )

    fields = render_knowledge_fields(summary_obj)
    # Embed only the non-empty value_texts; empty fields get a NULL embedding
    # (PRD: populated when value_text is non-empty). One batch call.
    embed_idx = [i for i, (_k, _v, vt) in enumerate(fields) if vt.strip()]

    # The EXTERNAL embedding call is guarded so a failure flips the row to
    # `failed` (visible) rather than leaving it stuck in_progress.
    try:
        vectors = await embedder.embed([fields[i][2] for i in embed_idx])
        # A misconfigured model (wrong dimension) is DETERMINISTIC: it would fail
        # opaquely at the `vector(1536)` cast on INSERT and retry forever. Catch
        # it here as a config error so it dead-letters with a clear message.
        if any(len(vector) != EMBEDDING_DIM for vector in vectors):
            raise EmbeddingConfigError(
                f"embedding model returned a non-{EMBEDDING_DIM}-dimension vector; "
                "check openai_embedding_model matches the vector(1536) column"
            )
    except Exception as exc:
        await fail_action(session, action_id=begun.action_id, last_error=str(exc))
        await session.commit()
        log.warning(
            "S1-30 knowledge embedding failed",
            extra={
                "client_id": str(client_id),
                "transcript_id": str(transcript_id),
                "action_id": str(begun.action_id),
                "error": str(exc),
            },
        )
        raise

    embeddings: list[str | None] = [None] * len(fields)
    for pos, i in enumerate(embed_idx):
        embeddings[i] = _vector_literal(vectors[pos])

    # One shared captured_at across all rows so S1-32 reads the batch whole.
    captured_at = datetime.now(UTC)
    params = [
        {
            "client_id": client_id,
            "source": CLIENT_KNOWLEDGE_SOURCE_SALES_CALL,
            "key": key,
            "value": json.dumps(value),
            "value_text": value_text if value_text.strip() else None,
            "embedding": embeddings[i],
            "captured_at": captured_at,
        }
        for i, (key, value, value_text) in enumerate(fields)
    ]

    # The batch write is ALSO guarded: a DB error here (FK / constraint /
    # transport) must record the action `failed`, never leave a zombie
    # in_progress. Roll back the aborted INSERT txn first, then fail_action in a
    # clean one (a failing UPDATE cannot run inside an aborted transaction).
    try:
        await session.execute(_INSERT_KNOWLEDGE_SQL, params)
        await complete_action(
            session,
            action_id=begun.action_id,
            external_id=str(transcript_id),
            response={
                "keys": [key for key, _v, _vt in fields],
                "rows": len(fields),
                "captured_at": captured_at.isoformat(),
            },
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        await fail_action(session, action_id=begun.action_id, last_error=str(exc))
        await session.commit()
        log.warning(
            "S1-30 knowledge persist failed",
            extra={
                "client_id": str(client_id),
                "transcript_id": str(transcript_id),
                "action_id": str(begun.action_id),
                "error": str(exc),
            },
        )
        raise

    log.info(
        "S1-30 knowledge written",
        extra={
            "client_id": str(client_id),
            "transcript_id": str(transcript_id),
            "action_id": str(begun.action_id),
            "rows": len(fields),
        },
    )
    return StoreKnowledgeResult(stored=True, skipped=False, rows_written=len(fields))


@inngest_client.create_function(
    fn_id="store-sales-knowledge",
    trigger=inngest.TriggerEvent(event=SALES_SUMMARY_READY_EVENT),
    concurrency=[
        inngest.Concurrency(limit=5, scope="fn"),
        inngest.Concurrency(key="event.data.transcript_id", limit=1, scope="fn"),
    ],
)
async def store_sales_knowledge(ctx: inngest.Context, step: inngest.Step) -> dict:
    """Inngest wrapper: build production deps, run the core, classify failures.

    Raises:
        inngest.NonRetriableError: malformed event, schema-invalid summary, empty
            key, or a structural OpenAI 4xx - none self-heal.
        Other exceptions (openai 429 / 5xx / transport, back-off): propagate so
            Inngest retries.
    """
    try:
        client_id = uuid.UUID(ctx.event.data["client_id"])
        transcript_id = uuid.UUID(ctx.event.data["transcript_id"])
        document_id = ctx.event.data.get("document_id")
        summary = ctx.event.data["summary"]
    except (KeyError, ValueError, TypeError) as exc:
        # A malformed sales_summary.ready payload will never self-heal; dead-letter
        # loudly rather than retrying forever with zero visibility.
        log.error(
            "S1-30 received a malformed sales_summary.ready event",
            extra={"run_id": ctx.run_id, "error": str(exc)},
        )
        raise inngest.NonRetriableError(f"malformed sales_summary.ready event: {exc}") from exc

    embedder = get_embedding_client()

    async with AsyncSessionLocal() as session:
        try:
            result = await store_sales_knowledge_core(
                session,
                embedder,
                client_id=client_id,
                transcript_id=transcript_id,
                document_id=document_id,
                summary=summary,
                inngest_run_id=ctx.run_id,
            )
        except (ValidationError, EmbeddingConfigError) as exc:
            # Schema-invalid summary or an empty API key: deterministic, won't
            # self-heal -> dead-letter.
            raise inngest.NonRetriableError(str(exc)) from exc
        except openai.APIStatusError as exc:
            # Full 4xx taxonomy (bad/expired key 401, disabled 403, unknown model
            # 404, oversized 413) is structural -> dead-letter. 429 + 5xx are
            # transient -> propagate so Inngest retries.
            if 400 <= exc.status_code < 500 and exc.status_code != 429:
                raise inngest.NonRetriableError(str(exc)) from exc
            raise
        # ConcurrentKnowledgeInProgress (back-off), openai 429 / 5xx,
        # openai.APIConnectionError / APITimeoutError (transport), and any
        # unexpected error propagate -> Inngest retries.

    return {
        "client_id": str(client_id),
        "transcript_id": str(transcript_id),
        "stored": result.stored,
        "skipped": result.skipped,
        "rows_written": result.rows_written,
    }


__all__ = [
    "PLATFORM",
    "STALE_IN_PROGRESS",
    "STORE_ACTION",
    "ConcurrentKnowledgeInProgress",
    "StoreKnowledgeResult",
    "render_knowledge_fields",
    "store_sales_knowledge",
    "store_sales_knowledge_core",
]
