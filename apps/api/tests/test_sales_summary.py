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

import types
import uuid

import anthropic
import httpx
import inngest
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.storage.client import FakeStorageClient, ObjectNotFound
from bullet_api.summary.models import BudgetRange, NotableQuote, SalesCallSummary
from bullet_api.worker import SALES_SUMMARY_READY_EVENT, FakeEventEmitter
from bullet_api.worker import sales_summary as sales_summary_module
from bullet_api.worker.platform_actions import build_idempotency_key
from bullet_api.worker.sales_summary import (
    PLATFORM,
    SUMMARISE_ACTION,
    ConcurrentSummaryInProgress,
    EmptyTranscriptForSummaryError,
    summarise_sales_call,
    summarise_sales_call_core,
)
from bullet_api.worker.summary_client import (
    FakeSummaryClient,
    HttpAnthropicClient,
    SummaryConfigError,
    SummaryRefusedError,
    SummaryTruncatedError,
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


# --------------------------------------------------------------------------- #
# Pre-PR review hardening (S1-29): wrapper input validation + error taxonomy
# --------------------------------------------------------------------------- #


def _api_status_error(status: int) -> anthropic.APIStatusError:
    """A bare APIStatusError carrying `status_code` - the wrapper branches on
    the code, not the subclass, so this stands in for 401/403/404/429/5xx."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIStatusError(
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
    """Drive the Inngest wrapper (`_handler`) in isolation: the core is stubbed
    to raise `core_exc` (or is never reached, for malformed-input tests), and
    the production dep factories are neutralised so no real R2 / Anthropic / DB
    is touched. Returns/raises exactly what the wrapper does, so we can assert
    its retriable-vs-NonRetriable classification directly."""
    if data is None:
        data = {
            "client_id": str(uuid.uuid4()),
            "transcript_id": str(uuid.uuid4()),
            "r2_key": R2_KEY,
            "document_id": "doc-1",
        }

    async def _core(*a, **k):
        if core_exc is not None:
            raise core_exc
        raise AssertionError("core should not complete in these tests")

    monkeypatch.setattr(sales_summary_module, "summarise_sales_call_core", _core)
    monkeypatch.setattr(sales_summary_module, "get_storage_client", lambda: object())
    monkeypatch.setattr(sales_summary_module, "get_summary_client", lambda: object())
    monkeypatch.setattr(sales_summary_module, "AsyncSessionLocal", lambda: _FakeSession())
    return await summarise_sales_call._handler(_ctx(data), None)


async def test_malformed_event_missing_field_dead_letters(monkeypatch) -> None:
    """A transcript.linked payload missing client_id must NOT retry forever with
    no visibility - it dead-letters (NonRetriable) before any work."""
    with pytest.raises(inngest.NonRetriableError):
        await _invoke_wrapper(
            monkeypatch, data={"transcript_id": str(uuid.uuid4()), "r2_key": R2_KEY}
        )


async def test_malformed_event_bad_uuid_dead_letters(monkeypatch) -> None:
    with pytest.raises(inngest.NonRetriableError):
        await _invoke_wrapper(
            monkeypatch,
            data={"client_id": "not-a-uuid", "transcript_id": str(uuid.uuid4()), "r2_key": R2_KEY},
        )


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
async def test_structural_4xx_dead_letters(monkeypatch, status: int) -> None:
    """The full 4xx taxonomy (bad key 401, disabled 403, unknown model 404,
    oversized 413, ...) is non-self-healing -> NonRetriable, not just 400."""
    with pytest.raises(inngest.NonRetriableError):
        await _invoke_wrapper(monkeypatch, core_exc=_api_status_error(status))


@pytest.mark.parametrize("status", [429, 500, 503, 529])
async def test_transient_status_propagates(monkeypatch, status: int) -> None:
    """429 + 5xx / overloaded are transient -> propagate (Inngest retries),
    NOT dead-lettered."""
    with pytest.raises(anthropic.APIStatusError):
        await _invoke_wrapper(monkeypatch, core_exc=_api_status_error(status))


async def test_unicode_decode_error_dead_letters(monkeypatch) -> None:
    """A non-UTF-8 transcript object is deterministic -> NonRetriable, not a
    forever-retried transient."""
    exc = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    with pytest.raises(inngest.NonRetriableError):
        await _invoke_wrapper(monkeypatch, core_exc=exc)


async def test_concurrent_backoff_propagates_retriable(monkeypatch) -> None:
    """The back-off signal is retriable (so the retry finds the winner's
    success), never dead-lettered."""
    with pytest.raises(ConcurrentSummaryInProgress):
        await _invoke_wrapper(monkeypatch, core_exc=ConcurrentSummaryInProgress(uuid.uuid4()))


# --------------------------------------------------------------------------- #
# Pre-PR review hardening (S1-29): r2_key idempotency + concurrency guard (DB)
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_relink_to_corrected_transcript_resummarises(async_session: AsyncSession) -> None:
    """A re-link to a corrected transcript (same transcript_id, NEW r2_key) must
    produce a fresh summary + a distinct action row, not replay-short-circuit on
    the stale original. Proves r2_key is part of the idempotency key."""
    client_id = await _seed_client(async_session)
    transcript_id = uuid.uuid4()
    key_a = "sales-call-transcripts/original.txt"
    key_b = "sales-call-transcripts/corrected.txt"
    storage = FakeStorageClient(gets={key_a: b"first pass", key_b: b"corrected pass"})

    await summarise_sales_call_core(
        async_session,
        storage,
        FakeSummaryClient(summary=_summary()),
        FakeEventEmitter(),
        client_id=client_id,
        transcript_id=transcript_id,
        document_id="doc-1",
        r2_key=key_a,
    )

    second_client = FakeSummaryClient(summary=_summary())
    second_emitter = FakeEventEmitter()
    result = await summarise_sales_call_core(
        async_session,
        storage,
        second_client,
        second_emitter,
        client_id=client_id,
        transcript_id=transcript_id,
        document_id="doc-1",
        r2_key=key_b,
    )

    assert result.summarised is True  # NOT a replay short-circuit
    assert second_client.calls == 1  # a fresh summary was produced
    assert len(await _actions(async_session, client_id)) == 2  # one row per r2_key
    assert len(second_emitter.sent) == 1


async def _seed_in_progress(
    session: AsyncSession, *, client_id: uuid.UUID, transcript_id: uuid.UUID, age_sql: str
) -> None:
    """Insert an in_progress row for the S1-29 idempotency key, started `age_sql`
    ago (e.g. `now()` or `now() - interval '20 minutes'`) - simulating another
    run holding the transcript."""
    key = build_idempotency_key(client_id, PLATFORM, SUMMARISE_ACTION, transcript_id, R2_KEY)
    await session.execute(
        text(
            "INSERT INTO platform_actions "
            "(client_id, platform, action, idempotency_key, status, started_at) "
            f"VALUES (:cid, :p, :a, :k, 'in_progress', {age_sql})"
        ),
        {"cid": client_id, "p": PLATFORM, "a": SUMMARISE_ACTION, "k": key},
    )
    await session.commit()


@pytest.mark.db
async def test_concurrent_fresh_in_progress_backs_off(async_session: AsyncSession) -> None:
    """A second run that finds a FRESH in_progress row backs off (retriable)
    without spending a second LLM call - the double-spend the reviewer flagged."""
    client_id = await _seed_client(async_session)
    transcript_id = uuid.uuid4()
    await _seed_in_progress(
        async_session, client_id=client_id, transcript_id=transcript_id, age_sql="now()"
    )

    summary_client = FakeSummaryClient(summary=_summary())
    emitter = FakeEventEmitter()
    with pytest.raises(ConcurrentSummaryInProgress):
        await _run(
            async_session,
            client_id=client_id,
            transcript_id=transcript_id,
            storage=FakeStorageClient(gets={R2_KEY: TRANSCRIPT_BYTES}),
            summary_client=summary_client,
            emitter=emitter,
        )
    assert summary_client.calls == 0  # did NOT double-spend the LLM
    assert emitter.sent == []
    assert len(await _actions(async_session, client_id)) == 1  # no duplicate row


@pytest.mark.db
async def test_stale_in_progress_is_reclaimed(async_session: AsyncSession) -> None:
    """A STALE in_progress row (older than STALE_IN_PROGRESS -> the prior run
    crashed) is reclaimed and re-run, not stuck backing off forever."""
    client_id = await _seed_client(async_session)
    transcript_id = uuid.uuid4()
    await _seed_in_progress(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        age_sql="now() - interval '20 minutes'",
    )

    summary_client = FakeSummaryClient(summary=_summary())
    emitter = FakeEventEmitter()
    result = await _run(
        async_session,
        client_id=client_id,
        transcript_id=transcript_id,
        storage=FakeStorageClient(gets={R2_KEY: TRANSCRIPT_BYTES}),
        summary_client=summary_client,
        emitter=emitter,
    )
    assert result.summarised is True  # reclaimed + ran
    assert summary_client.calls == 1
    rows = await _actions(async_session, client_id)
    assert len(rows) == 1 and rows[0].status == "success"
    assert len(emitter.sent) == 1


# --------------------------------------------------------------------------- #
# Pre-PR review hardening (S1-29): HttpAnthropicClient stop_reason branches
# --------------------------------------------------------------------------- #


def _fake_anthropic(resp):
    class _Messages:
        async def parse(self, **kwargs):
            return resp

    return types.SimpleNamespace(messages=_Messages())


async def test_http_client_refusal_raises() -> None:
    resp = types.SimpleNamespace(
        stop_reason="refusal",
        stop_details=types.SimpleNamespace(category="cyber"),
        parsed_output=None,
    )
    client = HttpAnthropicClient(api_key="sk-x", model="m", client=_fake_anthropic(resp))
    with pytest.raises(SummaryRefusedError):
        await client.summarise("Rep: hi")


async def test_http_client_truncation_raises() -> None:
    resp = types.SimpleNamespace(stop_reason="max_tokens", parsed_output=None)
    client = HttpAnthropicClient(api_key="sk-x", model="m", client=_fake_anthropic(resp))
    with pytest.raises(SummaryTruncatedError):
        await client.summarise("Rep: hi")


async def test_http_client_missing_parsed_output_raises() -> None:
    """end_turn but no parsed output is unexpected -> hard failure, not a silent
    None return."""
    resp = types.SimpleNamespace(stop_reason="end_turn", parsed_output=None)
    client = HttpAnthropicClient(api_key="sk-x", model="m", client=_fake_anthropic(resp))
    with pytest.raises(SummaryRefusedError):
        await client.summarise("Rep: hi")
