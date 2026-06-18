"""Integration tests for the S1-29 `summarise_sales_call` worker.

Hit Postgres (the `platform_actions` begin/complete/fail) via the transactional
`async_session` fixture, so all are `@pytest.mark.db`. The Anthropic API and R2
are the Fake* doubles so no network call is made.

Matrix (the failure/transport/replay paths, not just happy):
- happy: valid summary -> platform_actions success + sales_summary.ready emitted;
- schema-invalid (ValidationError) and refusal -> failed + raised (NonRetriable);
- transport error -> failed + raised (retriable);
- replay short-circuits AND re-emits from the stored summary (no second LLM call);
- empty transcript / missing R2 object -> failed, LLM never called;
- the HttpAnthropicClient builds a cache_control'd request (no real network).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.storage.client import FakeStorageClient, ObjectNotFound
from bullet_api.summary.models import BudgetRange, NotableQuote, SalesCallSummary
from bullet_api.worker import SALES_SUMMARY_READY_EVENT, FakeEventEmitter
from bullet_api.worker.sales_summary import (
    EmptyTranscriptForSummaryError,
    summarise_sales_call,
    summarise_sales_call_core,
)
from bullet_api.worker.summary_client import (
    FakeSummaryClient,
    HttpAnthropicClient,
    SummaryConfigError,
    SummaryRefusedError,
)

R2_KEY = "sales-call-transcripts/conferenceRecords/c/transcripts/t.txt"
TRANSCRIPT_BYTES = b"Rep: Tell me about the studio.\nOwner: Boutique spin, Leeds.\n"


def _summary() -> SalesCallSummary:
    return SalesCallSummary(
        business_type="boutique spin studio",
        business_goals=["Fill off-peak slots"],
        budget_range_usd=BudgetRange(min=1500, max=2000, currency="GBP"),
        pain_points=["Wasted prior ad spend"],
        red_flags=[],
        next_steps=["Send proposal"],
        notable_quotes=[
            NotableQuote(speaker="Owner", quote="Show me the leads.", timestamp_seconds=0)
        ],
    )


def _validation_error() -> ValidationError:
    try:
        BudgetRange(min="not-a-number", max=1, currency="USD")  # type: ignore[arg-type]
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


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


async def _actions(session: AsyncSession, client_id: uuid.UUID):
    return (
        await session.execute(
            text(
                "SELECT status, last_error, external_id, response "
                "FROM platform_actions WHERE client_id = :cid AND platform = 'anthropic'"
            ),
            {"cid": client_id},
        )
    ).all()


async def _run(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    transcript_id: uuid.UUID,
    storage: FakeStorageClient,
    summary_client: FakeSummaryClient,
    emitter: FakeEventEmitter,
):
    return await summarise_sales_call_core(
        session,
        storage,
        summary_client,
        emitter,
        client_id=client_id,
        transcript_id=transcript_id,
        document_id="doc-1",
        r2_key=R2_KEY,
    )


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_happy_path_summarises_and_emits(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    transcript_id = uuid.uuid4()
    storage = FakeStorageClient(gets={R2_KEY: TRANSCRIPT_BYTES})
    summary_client = FakeSummaryClient(summary=_summary())
    emitter = FakeEventEmitter()

    result = await _run(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        storage=storage,
        summary_client=summary_client,
        emitter=emitter,
    )

    assert result.summarised is True
    assert summary_client.calls == 1

    actions = await _actions(async_session, client_id)
    assert len(actions) == 1
    assert actions[0].status == "success"
    assert actions[0].external_id == str(transcript_id)
    assert actions[0].response["summary"]["business_type"] == "boutique spin studio"

    assert len(emitter.sent) == 1
    name, data = emitter.sent[0]
    assert name == SALES_SUMMARY_READY_EVENT
    assert data["client_id"] == str(client_id)
    assert data["transcript_id"] == str(transcript_id)
    assert data["document_id"] == "doc-1"
    assert data["summary"]["budget_range_usd"]["currency"] == "GBP"


# --------------------------------------------------------------------------- #
# Failure modes
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_schema_invalid_fails_no_emit(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    storage = FakeStorageClient(gets={R2_KEY: TRANSCRIPT_BYTES})
    summary_client = FakeSummaryClient(error=_validation_error())
    emitter = FakeEventEmitter()

    with pytest.raises(ValidationError):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=uuid.uuid4(),
            storage=storage,
            summary_client=summary_client,
            emitter=emitter,
        )

    actions = await _actions(async_session, client_id)
    assert actions[0].status == "failed"
    assert actions[0].last_error
    assert emitter.sent == []


@pytest.mark.db
async def test_refusal_fails_no_emit(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    storage = FakeStorageClient(gets={R2_KEY: TRANSCRIPT_BYTES})
    summary_client = FakeSummaryClient(error=SummaryRefusedError("cyber"))
    emitter = FakeEventEmitter()

    with pytest.raises(SummaryRefusedError):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=uuid.uuid4(),
            storage=storage,
            summary_client=summary_client,
            emitter=emitter,
        )
    actions = await _actions(async_session, client_id)
    assert actions[0].status == "failed"
    assert emitter.sent == []


@pytest.mark.db
async def test_transport_error_fails_and_propagates(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    storage = FakeStorageClient(gets={R2_KEY: TRANSCRIPT_BYTES})
    summary_client = FakeSummaryClient(error=httpx.ReadTimeout("slow"))
    emitter = FakeEventEmitter()

    with pytest.raises(httpx.ReadTimeout):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=uuid.uuid4(),
            storage=storage,
            summary_client=summary_client,
            emitter=emitter,
        )
    actions = await _actions(async_session, client_id)
    assert actions[0].status == "failed"
    assert emitter.sent == []


@pytest.mark.db
async def test_empty_transcript_short_circuits(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    storage = FakeStorageClient(gets={R2_KEY: b"   \n  "})
    summary_client = FakeSummaryClient(summary=_summary())
    emitter = FakeEventEmitter()

    with pytest.raises(EmptyTranscriptForSummaryError):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=uuid.uuid4(),
            storage=storage,
            summary_client=summary_client,
            emitter=emitter,
        )
    assert summary_client.calls == 0  # never spent a model call
    actions = await _actions(async_session, client_id)
    assert actions[0].status == "failed"
    assert emitter.sent == []


@pytest.mark.db
async def test_missing_transcript_object_fails(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    storage = FakeStorageClient(gets={})  # key absent -> ObjectNotFound
    summary_client = FakeSummaryClient(summary=_summary())
    emitter = FakeEventEmitter()

    with pytest.raises(ObjectNotFound):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=uuid.uuid4(),
            storage=storage,
            summary_client=summary_client,
            emitter=emitter,
        )
    assert summary_client.calls == 0
    actions = await _actions(async_session, client_id)
    assert actions[0].status == "failed"
    assert emitter.sent == []


# --------------------------------------------------------------------------- #
# Replay: idempotent + re-emits from stored summary (the critical property)
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_replay_re_emits_without_second_llm_call(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    transcript_id = uuid.uuid4()
    storage = FakeStorageClient(gets={R2_KEY: TRANSCRIPT_BYTES})

    first_client = FakeSummaryClient(summary=_summary())
    first_emitter = FakeEventEmitter()
    await _run(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        storage=storage,
        summary_client=first_client,
        emitter=first_emitter,
    )

    # Replay (simulates an Inngest retry after a post-commit crash).
    second_client = FakeSummaryClient(summary=_summary())
    second_emitter = FakeEventEmitter()
    result = await _run(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        storage=storage,
        summary_client=second_client,
        emitter=second_emitter,
    )

    assert result.skipped is True
    assert second_client.calls == 0  # no second model call
    # exactly one action row across both runs
    assert len(await _actions(async_session, client_id)) == 1
    # but the event IS re-emitted (re-derived from platform_actions.response)
    assert len(second_emitter.sent) == 1
    name, data = second_emitter.sent[0]
    assert name == SALES_SUMMARY_READY_EVENT
    assert data["summary"]["business_type"] == "boutique spin studio"


@pytest.mark.db
async def test_replay_with_missing_stored_summary_does_not_emit(
    async_session: AsyncSession,
) -> None:
    """Invariant-violation guard: a `success` row whose `response` has no
    `summary` (a shape that complete_action never writes, but could arise from
    a manual fix-up) must NOT emit a malformed event - it short-circuits, logs,
    and emits nothing rather than driving S1-30 with junk."""
    client_id = await _seed_client(async_session)
    transcript_id = uuid.uuid4()
    storage = FakeStorageClient(gets={R2_KEY: TRANSCRIPT_BYTES})

    # First run reaches success normally.
    await _run(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        storage=storage,
        summary_client=FakeSummaryClient(summary=_summary()),
        emitter=FakeEventEmitter(),
    )
    # Corrupt the stored response so the replay can't re-derive a summary.
    await async_session.execute(
        text(
            "UPDATE platform_actions SET response = '{}'::jsonb "
            "WHERE client_id = :cid AND platform = 'anthropic'"
        ),
        {"cid": client_id},
    )
    await async_session.commit()

    replay_client = FakeSummaryClient(summary=_summary())
    replay_emitter = FakeEventEmitter()
    result = await _run(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        storage=storage,
        summary_client=replay_client,
        emitter=replay_emitter,
    )

    assert result.skipped is True
    assert replay_client.calls == 0  # still no second model call
    assert replay_emitter.sent == []  # no malformed event emitted


# --------------------------------------------------------------------------- #
# HttpAnthropicClient request shape (no real network) + Inngest config
# --------------------------------------------------------------------------- #


async def test_http_client_builds_cached_request() -> None:
    """The system + few-shot blocks carry cache_control; the transcript is in
    messages with no breakpoint; output_format is the §7.1 model."""
    captured: dict = {}

    class _Resp:
        stop_reason = "end_turn"

        @property
        def parsed_output(self):
            return _summary()

    class _Messages:
        async def parse(self, **kwargs):
            captured.update(kwargs)
            return _Resp()

    class _FakeAnthropic:
        messages = _Messages()

    client = HttpAnthropicClient(api_key="sk-x", model="claude-opus-4-7", client=_FakeAnthropic())
    out = await client.summarise("Rep: hi\nOwner: hello")

    assert out.business_type == "boutique spin studio"
    assert captured["model"] == "claude-opus-4-7"
    assert captured["output_format"] is SalesCallSummary
    system = captured["system"]
    assert len(system) == 2
    assert all(block["cache_control"] == {"type": "ephemeral"} for block in system)
    # transcript is the user message, uncached
    assert captured["messages"][0]["role"] == "user"
    assert "cache_control" not in captured["messages"][0]


async def test_http_client_empty_key_raises() -> None:
    """Empty key fails loud as the typed SummaryConfigError (a RuntimeError
    subclass) so the worker can dead-letter it without swallowing unrelated,
    genuinely transient RuntimeErrors."""
    client = HttpAnthropicClient(api_key="", model="claude-opus-4-7")
    with pytest.raises(SummaryConfigError, match="ANTHROPIC_API_KEY is empty"):
        await client.summarise("Rep: hi")


def test_worker_inngest_config() -> None:
    cfg = summarise_sales_call.get_config("http://localhost:8000/api/inngest").main
    assert [t.event for t in cfg.triggers] == ["transcript.linked"]
    caps = {(c.key, c.limit) for c in cfg.concurrency}
    assert (None, 5) in caps
    assert ("event.data.transcript_id", 1) in caps
