"""Integration tests for the S1-30 `store_sales_knowledge` worker.

Hit Postgres (the `platform_actions` guard + the `client_knowledge` batch write)
via the transactional `async_session` fixture, so most are `@pytest.mark.db`.
The OpenAI embeddings API is the `FakeEmbeddingClient` so no network call is
made.

Matrix: happy (7 rows / shared captured_at / embeddings on non-empty fields /
NULL on empty), replay dedupe, corrected re-link -> fresh batch, schema-invalid
summary + embedding-config + transport failures, concurrency back-off, plus the
Inngest wrapper's error classification and config.
"""

from __future__ import annotations

import types
import uuid

import inngest
import openai
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.summary.models import BudgetRange, NotableQuote, SalesCallSummary
from bullet_api.worker import sales_knowledge as sales_knowledge_module
from bullet_api.worker.embedding_client import EmbeddingConfigError, FakeEmbeddingClient
from bullet_api.worker.platform_actions import build_idempotency_key
from bullet_api.worker.sales_knowledge import (
    PLATFORM,
    STORE_ACTION,
    ConcurrentKnowledgeInProgress,
    store_sales_knowledge,
    store_sales_knowledge_core,
)


def _summary(**overrides) -> SalesCallSummary:
    base = dict(
        business_type="boutique spin studio",
        business_goals=["Fill off-peak slots"],
        budget_range_usd=BudgetRange(min=1500, max=2000, currency="GBP"),
        pain_points=["Wasted prior ad spend"],
        red_flags=["Tight timeline"],
        next_steps=["Send proposal"],
        notable_quotes=[
            NotableQuote(speaker="Owner", quote="Show me the leads.", timestamp_seconds=12)
        ],
    )
    base.update(overrides)
    return SalesCallSummary(**base)


def _summary_dict(**overrides) -> dict:
    return _summary(**overrides).model_dump(mode="json")


async def _seed_client(session: AsyncSession) -> uuid.UUID:
    return (
        await session.execute(
            text(
                "INSERT INTO clients (email, legal_entity, current_step, step_entered_at) "
                "VALUES (:email, 'Sample Gym Ltd', 'signed', now()) RETURNING id"
            ),
            {"email": f"signer+{uuid.uuid4().hex[:6]}@gym.com"},
        )
    ).scalar_one()


async def _rows(session: AsyncSession, client_id: uuid.UUID):
    return (
        (
            await session.execute(
                text(
                    "SELECT key, value, value_text, (embedding IS NOT NULL) AS has_embedding, "
                    "captured_at FROM client_knowledge "
                    "WHERE client_id = :cid AND source = 'sales_call' ORDER BY key"
                ),
                {"cid": client_id},
            )
        )
        .mappings()
        .all()
    )


async def _action(session: AsyncSession, client_id: uuid.UUID):
    return (
        (
            await session.execute(
                text(
                    "SELECT status, last_error, external_id FROM platform_actions "
                    "WHERE client_id = :cid AND platform = 'openai'"
                ),
                {"cid": client_id},
            )
        )
        .mappings()
        .all()
    )


async def _run(session, *, client_id, transcript_id, summary, embedder):
    return await store_sales_knowledge_core(
        session,
        embedder,
        client_id=client_id,
        transcript_id=transcript_id,
        document_id="doc-1",
        summary=summary,
    )


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_happy_path_writes_seven_rows(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    embedder = FakeEmbeddingClient()
    result = await _run(
        async_session,
        client_id=client_id,
        transcript_id=uuid.uuid4(),
        summary=_summary_dict(),
        embedder=embedder,
    )

    assert result.stored is True
    assert result.rows_written == 7

    rows = await _rows(async_session, client_id)
    assert len(rows) == 7
    keys = {r["key"] for r in rows}
    assert keys == {
        "business_type",
        "business_goals",
        "budget_range_usd",
        "pain_points",
        "red_flags",
        "next_steps",
        "notable_quotes",
    }
    # One shared captured_at across the whole batch (the S1-32 read contract).
    assert len({r["captured_at"] for r in rows}) == 1
    # Every field here is populated -> every row has an embedding.
    assert all(r["has_embedding"] for r in rows)
    # Embedded once, for all seven non-empty value_texts.
    assert embedder.calls == 1
    assert len(embedder.embedded) == 7
    # Value shapes match the §7.1 JSONB contract the dashboard renders.
    by_key = {r["key"]: r["value"] for r in rows}
    assert by_key["business_type"] == "boutique spin studio"
    assert by_key["business_goals"] == ["Fill off-peak slots"]
    assert by_key["budget_range_usd"] == {"min": 1500, "max": 2000, "currency": "GBP"}
    assert by_key["notable_quotes"][0]["speaker"] == "Owner"

    action = await _action(async_session, client_id)
    assert len(action) == 1 and action[0]["status"] == "success"


@pytest.mark.db
async def test_empty_field_gets_null_embedding(async_session: AsyncSession) -> None:
    """An empty field (e.g. no red flags) still writes a row, but with a NULL
    embedding + NULL value_text (PRD: embedding populated only when value_text is
    non-empty)."""
    client_id = await _seed_client(async_session)
    embedder = FakeEmbeddingClient()
    await _run(
        async_session,
        client_id=client_id,
        transcript_id=uuid.uuid4(),
        summary=_summary_dict(red_flags=[], budget_range_usd=None),
        embedder=embedder,
    )
    rows = {r["key"]: r for r in await _rows(async_session, client_id)}
    assert rows["red_flags"]["has_embedding"] is False
    assert rows["red_flags"]["value_text"] is None
    assert rows["red_flags"]["value"] == []
    assert rows["budget_range_usd"]["has_embedding"] is False
    assert rows["budget_range_usd"]["value"] is None  # JSON null, not a missing row
    # Populated fields still embedded; only the 5 non-empty were sent.
    assert rows["business_type"]["has_embedding"] is True
    assert len(embedder.embedded) == 5


# --------------------------------------------------------------------------- #
# Idempotency / replay
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_replay_same_summary_does_not_duplicate(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    transcript_id = uuid.uuid4()
    summary = _summary_dict()

    first = FakeEmbeddingClient()
    await _run(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        summary=summary,
        embedder=first,
    )
    second = FakeEmbeddingClient()
    result = await _run(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        summary=summary,
        embedder=second,
    )

    assert result.skipped is True
    assert second.calls == 0  # no second embedding spend
    assert len(await _rows(async_session, client_id)) == 7  # still one batch
    assert len(await _action(async_session, client_id)) == 1


@pytest.mark.db
async def test_corrected_relink_writes_fresh_batch(async_session: AsyncSession) -> None:
    """Same transcript_id but a DIFFERENT summary (a corrected re-link) hashes
    differently -> a new action + a fresh batch, not a stale short-circuit."""
    client_id = await _seed_client(async_session)
    transcript_id = uuid.uuid4()

    await _run(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        summary=_summary_dict(),
        embedder=FakeEmbeddingClient(),
    )
    second = FakeEmbeddingClient()
    result = await _run(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        summary=_summary_dict(business_type="corrected type"),
        embedder=second,
    )

    assert result.stored is True
    assert second.calls == 1
    assert len(await _rows(async_session, client_id)) == 14  # two batches
    assert len(await _action(async_session, client_id)) == 2


# --------------------------------------------------------------------------- #
# Failure modes (core)
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_schema_invalid_summary_dead_letters_before_any_row(
    async_session: AsyncSession,
) -> None:
    """A schema-invalid summary is validated BEFORE the idempotency key / any DB
    write (so the dedupe hash is over the canonical form), so it raises with NO
    platform_actions row - a contract violation the wrapper dead-letters, like a
    malformed event."""
    client_id = await _seed_client(async_session)
    embedder = FakeEmbeddingClient()
    # notable_quotes must be objects, not bare strings -> ValidationError.
    bad = _summary_dict()
    bad["notable_quotes"] = ["not-an-object"]

    with pytest.raises(ValidationError):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=uuid.uuid4(),
            summary=bad,
            embedder=embedder,
        )
    assert embedder.calls == 0  # never embedded a bad summary
    assert await _rows(async_session, client_id) == []
    assert await _action(async_session, client_id) == []  # no row written pre-validation


@pytest.mark.db
async def test_wrong_dimension_embedding_dead_letters(async_session: AsyncSession) -> None:
    """A misconfigured model returning non-1536-dim vectors is caught as a config
    error (dead-letter) rather than failing opaquely at the vector(1536) cast and
    retrying forever."""
    client_id = await _seed_client(async_session)
    embedder = FakeEmbeddingClient(vector=[0.1] * 10)  # wrong dimension
    with pytest.raises(EmbeddingConfigError, match="non-1536-dimension"):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=uuid.uuid4(),
            summary=_summary_dict(),
            embedder=embedder,
        )
    assert await _rows(async_session, client_id) == []
    assert (await _action(async_session, client_id))[0]["status"] == "failed"


@pytest.mark.db
async def test_persist_failure_records_failed_no_zombie(
    async_session: AsyncSession, monkeypatch
) -> None:
    """A DB error AFTER the embedding (here: complete_action raises) must record
    the action `failed` and roll back the INSERT - never leave a zombie
    in_progress or a partial batch."""
    client_id = await _seed_client(async_session)

    async def _boom(*a, **k):
        raise RuntimeError("db exploded on complete")

    monkeypatch.setattr(sales_knowledge_module, "complete_action", _boom)

    with pytest.raises(RuntimeError, match="db exploded"):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=uuid.uuid4(),
            summary=_summary_dict(),
            embedder=FakeEmbeddingClient(),
        )
    assert await _rows(async_session, client_id) == []  # INSERT rolled back
    action = await _action(async_session, client_id)
    assert len(action) == 1 and action[0]["status"] == "failed"  # not a zombie in_progress


@pytest.mark.db
async def test_embedding_config_error_fails_no_rows(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    embedder = FakeEmbeddingClient(error=EmbeddingConfigError("empty key"))
    with pytest.raises(EmbeddingConfigError):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=uuid.uuid4(),
            summary=_summary_dict(),
            embedder=embedder,
        )
    assert await _rows(async_session, client_id) == []
    assert (await _action(async_session, client_id))[0]["status"] == "failed"


@pytest.mark.db
async def test_transport_error_fails_and_propagates(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    embedder = FakeEmbeddingClient(error=openai.APITimeoutError(request=None))
    with pytest.raises(openai.APITimeoutError):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=uuid.uuid4(),
            summary=_summary_dict(),
            embedder=embedder,
        )
    assert await _rows(async_session, client_id) == []
    assert (await _action(async_session, client_id))[0]["status"] == "failed"


@pytest.mark.db
async def test_concurrent_fresh_in_progress_backs_off(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    transcript_id = uuid.uuid4()
    summary = _summary_dict()
    # Seed a FRESH in_progress row for this run's exact idempotency key.
    key = build_idempotency_key(
        client_id,
        PLATFORM,
        STORE_ACTION,
        transcript_id,
        sales_knowledge_module._summary_hash(summary),
    )
    await async_session.execute(
        text(
            "INSERT INTO platform_actions "
            "(client_id, platform, action, idempotency_key, status, started_at) "
            "VALUES (:cid, :p, :a, :k, 'in_progress', now())"
        ),
        {"cid": client_id, "p": PLATFORM, "a": STORE_ACTION, "k": key},
    )
    await async_session.commit()

    embedder = FakeEmbeddingClient()
    with pytest.raises(ConcurrentKnowledgeInProgress):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=transcript_id,
            summary=summary,
            embedder=embedder,
        )
    assert embedder.calls == 0  # did not double-spend embeddings
    assert await _rows(async_session, client_id) == []


async def _seed_in_progress(async_session, client_id, transcript_id, summary, *, age_sql):
    key = build_idempotency_key(
        client_id,
        PLATFORM,
        STORE_ACTION,
        transcript_id,
        sales_knowledge_module._summary_hash(summary),
    )
    await async_session.execute(
        text(
            "INSERT INTO platform_actions "
            "(client_id, platform, action, idempotency_key, status, started_at) "
            f"VALUES (:cid, :p, :a, :k, 'in_progress', {age_sql})"
        ),
        {"cid": client_id, "p": PLATFORM, "a": STORE_ACTION, "k": key},
    )
    await async_session.commit()


@pytest.mark.db
async def test_stale_in_progress_is_reclaimed_and_writes(async_session: AsyncSession) -> None:
    """A STALE in_progress row (prior run crashed) is atomically reclaimed and the
    batch is written to success."""
    client_id = await _seed_client(async_session)
    transcript_id = uuid.uuid4()
    summary = _summary_dict()
    await _seed_in_progress(
        async_session, client_id, transcript_id, summary, age_sql="now() - interval '20 minutes'"
    )

    embedder = FakeEmbeddingClient()
    result = await _run(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        summary=summary,
        embedder=embedder,
    )
    assert result.stored is True
    assert embedder.calls == 1
    assert len(await _rows(async_session, client_id)) == 7
    action = await _action(async_session, client_id)
    assert len(action) == 1 and action[0]["status"] == "success"


@pytest.mark.db
async def test_lost_stale_reclaim_backs_off(async_session: AsyncSession, monkeypatch) -> None:
    """If the atomic stale-reclaim loses the CAS (another run claimed it first),
    the worker backs off (retriable) and does NOT embed or write - closing the
    double-reclaim / double-spend window the reviewer flagged."""
    client_id = await _seed_client(async_session)
    transcript_id = uuid.uuid4()
    summary = _summary_dict()
    await _seed_in_progress(
        async_session, client_id, transcript_id, summary, age_sql="now() - interval '20 minutes'"
    )

    async def _lost(*a, **k):
        return False

    monkeypatch.setattr(sales_knowledge_module, "reclaim_stale_action", _lost)

    embedder = FakeEmbeddingClient()
    with pytest.raises(ConcurrentKnowledgeInProgress):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=transcript_id,
            summary=summary,
            embedder=embedder,
        )
    assert embedder.calls == 0  # lost the race -> no second embedding spend
    assert await _rows(async_session, client_id) == []


# --------------------------------------------------------------------------- #
# Inngest wrapper: input validation + error taxonomy + config
# --------------------------------------------------------------------------- #


def _api_status_error(status: int) -> openai.APIStatusError:
    import httpx

    request = httpx.Request("POST", "https://api.openai.com/v1/embeddings")
    return openai.APIStatusError(
        "boom", response=httpx.Response(status, request=request), body=None
    )


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _ctx(data: dict):
    return types.SimpleNamespace(event=types.SimpleNamespace(data=data), run_id="run-x")


async def _invoke_wrapper(monkeypatch, *, data=None, core_exc=None):
    if data is None:
        data = {
            "client_id": str(uuid.uuid4()),
            "transcript_id": str(uuid.uuid4()),
            "document_id": "doc-1",
            "summary": _summary_dict(),
        }

    async def _core(*a, **k):
        if core_exc is not None:
            raise core_exc
        raise AssertionError("core should not complete in these tests")

    monkeypatch.setattr(sales_knowledge_module, "store_sales_knowledge_core", _core)
    monkeypatch.setattr(sales_knowledge_module, "get_embedding_client", lambda: object())
    monkeypatch.setattr(sales_knowledge_module, "AsyncSessionLocal", lambda: _FakeSession())
    return await store_sales_knowledge._handler(_ctx(data))


async def test_malformed_event_missing_field_dead_letters(monkeypatch) -> None:
    with pytest.raises(inngest.NonRetriableError):
        await _invoke_wrapper(
            monkeypatch, data={"transcript_id": str(uuid.uuid4()), "summary": _summary_dict()}
        )


async def test_malformed_event_bad_uuid_dead_letters(monkeypatch) -> None:
    with pytest.raises(inngest.NonRetriableError):
        await _invoke_wrapper(
            monkeypatch,
            data={
                "client_id": "not-a-uuid",
                "transcript_id": str(uuid.uuid4()),
                "summary": _summary_dict(),
            },
        )


async def test_schema_invalid_summary_dead_letters(monkeypatch) -> None:
    with pytest.raises(inngest.NonRetriableError):
        await _invoke_wrapper(monkeypatch, core_exc=ValidationError.from_exception_data("x", []))


async def test_embedding_config_error_dead_letters(monkeypatch) -> None:
    with pytest.raises(inngest.NonRetriableError):
        await _invoke_wrapper(monkeypatch, core_exc=EmbeddingConfigError("empty key"))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
async def test_structural_4xx_dead_letters(monkeypatch, status: int) -> None:
    with pytest.raises(inngest.NonRetriableError):
        await _invoke_wrapper(monkeypatch, core_exc=_api_status_error(status))


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_status_propagates(monkeypatch, status: int) -> None:
    with pytest.raises(openai.APIStatusError):
        await _invoke_wrapper(monkeypatch, core_exc=_api_status_error(status))


async def test_backoff_propagates_retriable(monkeypatch) -> None:
    with pytest.raises(ConcurrentKnowledgeInProgress):
        await _invoke_wrapper(monkeypatch, core_exc=ConcurrentKnowledgeInProgress(uuid.uuid4()))


def test_worker_inngest_config() -> None:
    cfg = store_sales_knowledge.get_config("http://localhost:8000/api/inngest").main
    assert [t.event for t in cfg.triggers] == ["sales_summary.ready"]
    caps = {(c.key, c.limit) for c in cfg.concurrency}
    assert (None, 5) in caps
    assert ("event.data.transcript_id", 1) in caps
