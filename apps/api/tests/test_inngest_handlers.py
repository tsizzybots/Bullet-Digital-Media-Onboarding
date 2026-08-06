"""Guard the Inngest INVOCATION contract (S1-34b).

WHY THIS FILE EXISTS
--------------------
S1-34b: every Inngest function was declared `(ctx, step)` (the 0.3/0.4 API)
while the installed SDK invokes handlers with a SINGLE `ctx` (0.5.x). Every
invocation therefore raised

    TypeError: create_client_record() missing 1 required positional argument: 'step'

so NO function ever executed - in any environment - for weeks.

The suite had 279 passing tests and caught none of it, because every existing
test either calls the pure `*_core` helper directly or asserts declarations
via `fn.get_config("")`. Nothing exercised the path Inngest actually uses.
These tests close that hole: they assert the registered handlers are callable
the way the SDK calls them, so an arity/signature regression fails CI instead
of silently killing the whole fan-out in production.

No DB, no network - these are pure signature/invocation checks.
"""

from __future__ import annotations

import inspect
from unittest.mock import Mock

import pytest

from bullet_api.observability.inngest_sentry import SentryMiddleware
from bullet_api.worker import _inngest as _inngest_mod
from bullet_api.worker.client import FUNCTIONS, noop_function

# The SDK's contract in 0.5.x is
# `FunctionHandlerAsync = Callable[[Context], Awaitable[T]]` - exactly ONE
# positional parameter. Step tooling moved onto the context (`ctx.step`), so a
# handler must never take a second `step` argument again.
_EXPECTED_HANDLER_ARITY = 1


def _positional_params(handler: object) -> list[inspect.Parameter]:
    return [
        p
        for p in inspect.signature(handler).parameters.values()  # type: ignore[arg-type]
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]


def test_functions_registry_is_not_empty() -> None:
    """A guard on the guard: if FUNCTIONS were ever emptied, the arity test
    below would vacuously pass and this file would protect nothing."""
    assert len(FUNCTIONS) > 0


@pytest.mark.parametrize("fn", FUNCTIONS, ids=lambda f: f.local_id)
def test_handler_takes_exactly_one_positional_arg(fn: object) -> None:
    """Every registered handler must accept exactly `(ctx)`.

    This is the direct regression test for S1-34b: a handler reintroducing a
    second positional parameter (e.g. `step`) is uncallable by the SDK and
    kills the ENTIRE fan-out, not just its own function.
    """
    handler = fn._handler  # type: ignore[attr-defined]
    params = _positional_params(handler)
    assert len(params) == _EXPECTED_HANDLER_ARITY, (
        f"{fn.local_id} handler takes {len(params)} positional args "  # type: ignore[attr-defined]
        f"({[p.name for p in params]}); the Inngest 0.5.x contract is exactly "
        f"{_EXPECTED_HANDLER_ARITY} (ctx). Step tooling is `ctx.step`."
    )


@pytest.mark.parametrize("fn", FUNCTIONS, ids=lambda f: f.local_id)
def test_handler_binds_to_the_sdk_call_shape(fn: object) -> None:
    """The handler must BIND to `handler(ctx)` - the exact call the SDK makes.

    Strictly stronger than counting positional parameters above, and it closes
    two holes that count leaves open:

    1. A REQUIRED KEYWORD-ONLY parameter (`async def h(ctx, *, step)`) has one
       positional param, so the count assertion passes - but the SDK's
       `handler(ctx)` call still raises TypeError and the whole fan-out dies
       exactly as it did in S1-34b.
    2. Only 4 of the 7 handlers are ever invoked via `_handler` anywhere in the
       suite (noop here; signed_pdf / sales_knowledge / sales_summary in their
       own files). `create_client_record`, `create_ghl_subaccount` and
       `capture_meet_transcript` had NO call-shape coverage at all.

    `Signature.bind` answers "is this callable this way?" without executing the
    body, so this stays a pure offline check - no DB, no network, no creds.
    """
    handler = fn._handler  # type: ignore[attr-defined]
    try:
        inspect.signature(handler).bind(object())
    except TypeError as exc:  # pragma: no cover - only on a regression
        pytest.fail(
            f"{fn.local_id} handler cannot be called as handler(ctx): {exc}. "  # type: ignore[attr-defined]
            "The Inngest 0.5.x contract is a single positional `ctx`; step "
            "tooling is `ctx.step`. A handler that fails to bind kills the "
            "ENTIRE fan-out, not just its own function."
        )


@pytest.mark.parametrize("fn", FUNCTIONS, ids=lambda f: f.local_id)
def test_handler_is_async(fn: object) -> None:
    """All our handlers are async; the SDK awaits them (`is_handler_async`).
    A sync handler would be invoked down a different SDK path than the one
    these functions' `await`-heavy bodies assume."""
    assert inspect.iscoroutinefunction(fn._handler)  # type: ignore[attr-defined]


class _StubSettings:
    """Minimal stand-in for Settings: only the fields `_make_client` reads."""

    def __init__(self, sentry_dsn: str = "") -> None:
        self.sentry_dsn = sentry_dsn
        self.inngest_signing_key = ""
        self.inngest_event_key = ""


def test_sentry_middleware_registered_when_dsn_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a DSN, worker failures must be reported - the S1-34b gap.

    Without this middleware the Inngest SDK swallows handler exceptions into
    its response body and Sentry never sees them.
    """
    monkeypatch.setattr(_inngest_mod, "get_settings", lambda: _StubSettings("https://x@y/1"))
    client = _inngest_mod._make_client()
    assert SentryMiddleware in client.middleware


def test_sentry_middleware_absent_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    """No DSN (local dev / CI / tests) registers nothing, mirroring
    `init_sentry()`'s no-op posture, so runs do not log
    "Sentry SDK is not initialized" on every invocation."""
    monkeypatch.setattr(_inngest_mod, "get_settings", lambda: _StubSettings(""))
    client = _inngest_mod._make_client()
    assert SentryMiddleware not in client.middleware


async def test_noop_handler_is_actually_invocable() -> None:
    """Invoke a real registered handler the way the SDK does - one argument.

    `noop_function` is the only function with no I/O, so it can be driven end
    to end here. It ignores its context, so a Mock stands in for the real
    `inngest.Context` (constructing one needs the SDK's internal Group/Step
    machinery, which would test the SDK rather than our handler). The point is
    the CALL: under the S1-34b bug this raised TypeError before the body ran.
    """
    assert await noop_function._handler(Mock()) == "noop"
