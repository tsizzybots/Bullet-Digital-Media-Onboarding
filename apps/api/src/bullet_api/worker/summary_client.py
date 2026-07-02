"""Anthropic summariser seam (S1-29).

`SummaryClient` is a small Protocol so the worker depends on the interface, not
on the Anthropic SDK. `HttpAnthropicClient` is the production wiring (async SDK +
`messages.parse` structured output + prompt-cached system/few-shot prefix);
`FakeSummaryClient` returns a canned `SalesCallSummary` (or raises a seeded
error) for tests. Mirrors the PandaDoc / GHL / Google client seams.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol

from anthropic import AsyncAnthropic

from bullet_api.config import get_settings
from bullet_api.summary.models import SalesCallSummary
from bullet_api.summary.prompts import FEW_SHOT, SYSTEM_PROMPT

# A 7-field summary is small; 8k leaves ample headroom so the JSON never
# truncates mid-object (which would surface as stop_reason == "max_tokens").
SUMMARY_MAX_TOKENS = 8000


class SummaryRefusedError(Exception):
    """Claude declined to summarise (stop_reason == "refusal").

    A content/policy outcome that will not self-heal on retry; the worker
    records it failed and dead-letters. `category` carries the refusal class
    from `stop_details` for operator triage (may be None)."""

    def __init__(self, category: str | None) -> None:
        self.category = category
        super().__init__(f"Anthropic refused to summarise the transcript (category={category}).")


class SummaryTruncatedError(Exception):
    """The summary JSON was cut off (stop_reason == "max_tokens").

    Distinct from a refusal or a schema error: a budget problem. Retriable -
    a fresh attempt (or a larger max_tokens) can complete it. Should be rare
    given SUMMARY_MAX_TOKENS."""


class SummaryConfigError(RuntimeError):
    """A deployment-config failure (e.g. an empty ANTHROPIC_API_KEY) that will
    not self-heal on retry. A SUBCLASS of RuntimeError so existing fail-loud
    callers/tests still match, but a DISTINCT type the worker can catch
    precisely - so an unrelated, genuinely transient RuntimeError (a closed
    event loop, a mid-flight connection drop surfaced as RuntimeError) is NOT
    mis-classified as non-retriable and silently dead-lettered."""


class SummaryClient(Protocol):
    async def summarise(self, transcript_text: str) -> SalesCallSummary:
        """Summarise a sales-call transcript into the §7.1 `SalesCallSummary`.

        Raises:
            SummaryRefusedError: Claude refused (non-retriable).
            SummaryTruncatedError: output truncated (retriable).
            pydantic.ValidationError: model output failed schema validation
                (non-retriable - deterministic mismatch).
            anthropic.APIError / httpx transport errors: propagate for the
                wrapper to classify retriable-vs-not.
        """
        ...


class HttpAnthropicClient:
    """Production summariser - Anthropic async SDK, structured output, cached prefix.

    Accepts an optional pre-built `client` so tests can inject a fake
    AsyncAnthropic (the request-shape hook); production passes None and the
    client is built from the api key. Fails loudly (RuntimeError) on an empty
    api key rather than silently producing nothing.
    """

    def __init__(self, api_key: str, model: str, client: Any | None = None) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    def _build_system(self) -> list[dict]:
        # Two cached blocks (stable prefix): instructions + few-shot. The
        # transcript stays out of `system` so the prefix is byte-identical
        # across calls and the cache reads on the second call within the TTL.
        return [
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": FEW_SHOT, "cache_control": {"type": "ephemeral"}},
        ]

    async def summarise(self, transcript_text: str) -> SalesCallSummary:
        client = self._client
        if client is None:
            if not self._api_key:
                raise SummaryConfigError(
                    "ANTHROPIC_API_KEY is empty; cannot summarise. Set it on the Render env group."
                )
            # Build once and cache on the instance. A per-call AsyncAnthropic
            # each opens (and never closes) an httpx connection pool; reusing one
            # client keeps connections pooled across summaries. Paired with the
            # cached get_summary_client() factory, that is one client per process.
            client = AsyncAnthropic(api_key=self._api_key)
            self._client = client

        response = await client.messages.parse(
            model=self._model,
            max_tokens=SUMMARY_MAX_TOKENS,
            system=self._build_system(),
            messages=[{"role": "user", "content": transcript_text}],
            output_format=SalesCallSummary,
        )

        # Check stop_reason BEFORE reading parsed_output: a refusal/truncation
        # returns 200 with empty or partial content and parsed_output is None.
        if response.stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            raise SummaryRefusedError(category)
        if response.stop_reason == "max_tokens":
            raise SummaryTruncatedError()

        summary = response.parsed_output
        if summary is None:
            # end_turn but no parsed output is unexpected; treat as a hard failure.
            raise SummaryRefusedError(None)
        return summary


class FakeSummaryClient:
    """Test double. Returns a canned summary or raises a seeded error; counts
    calls so tests can assert the LLM was (not) invoked."""

    def __init__(
        self,
        summary: SalesCallSummary | None = None,
        error: Exception | None = None,
    ) -> None:
        self.summary = summary if summary is not None else SalesCallSummary()
        self.error = error
        self.calls = 0

    async def summarise(self, transcript_text: str) -> SalesCallSummary:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.summary


@lru_cache(maxsize=1)
def get_summary_client() -> SummaryClient:
    """Worker factory, process-cached so the HttpAnthropicClient (and its pooled
    AsyncAnthropic) is reused across runs rather than rebuilt per invocation.
    Tests inject a FakeSummaryClient directly into the core, so this cache is
    only exercised on the production path."""
    settings = get_settings()
    return HttpAnthropicClient(api_key=settings.anthropic_api_key, model=settings.anthropic_model)


__all__ = [
    "FakeSummaryClient",
    "HttpAnthropicClient",
    "SUMMARY_MAX_TOKENS",
    "SummaryClient",
    "SummaryConfigError",
    "SummaryRefusedError",
    "SummaryTruncatedError",
    "get_summary_client",
]
