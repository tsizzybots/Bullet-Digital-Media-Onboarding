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
from unittest.mock import Mock, patch

import inngest
import pytest

from bullet_api.observability import inngest_sentry
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


def _callables_under_guard(fn: object) -> list[tuple[str, object]]:
    """Every callable the SDK will invoke with a single `ctx` for this function.

    `on_failure` goes down the SAME one-arg path as the main handler
    (`function.py:153-158` assigns `handler = self._opts.on_failure`). So an
    `on_failure` declared `(ctx, step)` passes every other test here and then
    fails at RUNTIME - on the branch that only ever runs when something is
    already broken, which is the worst possible time to discover it.

    Returns `[]` entries for undefined on_failure so the parametrisation stays
    honest rather than asserting against None.
    """
    out: list[tuple[str, object]] = [("handler", fn._handler)]  # type: ignore[attr-defined]
    on_failure = getattr(fn._opts, "on_failure", None)  # type: ignore[attr-defined]
    if on_failure is not None:
        out.append(("on_failure", on_failure))
    return out


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
    for kind, handler in _callables_under_guard(fn):
        params = _positional_params(handler)
        assert len(params) == _EXPECTED_HANDLER_ARITY, (
            f"{fn.local_id} {kind} takes {len(params)} positional args "  # type: ignore[attr-defined]
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
    for kind, handler in _callables_under_guard(fn):
        try:
            inspect.signature(handler).bind(object())
        except TypeError as exc:  # pragma: no cover - only on a regression
            pytest.fail(
                f"{fn.local_id} {kind} cannot be called as f(ctx): {exc}. "  # type: ignore[attr-defined]
                "The Inngest 0.5.x contract is a single positional `ctx`; step "
                "tooling is `ctx.step`. A handler that fails to bind kills the "
                "ENTIRE fan-out, not just its own function."
            )


@pytest.mark.parametrize("fn", FUNCTIONS, ids=lambda f: f.local_id)
def test_handler_is_async(fn: object) -> None:
    """All our handlers are async; the SDK awaits them (`is_handler_async`).
    A sync handler would be invoked down a different SDK path than the one
    these functions' `await`-heavy bodies assume."""
    for kind, handler in _callables_under_guard(fn):
        assert inspect.iscoroutinefunction(handler), f"{fn.local_id} {kind} is not async"  # type: ignore[attr-defined]


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


# ---------------------------------------------------------------------------
# The Sentry middleware, ACTUALLY EXECUTED.
#
# Review round 3: the middleware is gated on `sentry_dsn`, which is empty in
# CI, in local dev, and in the 30/07 end-to-end run - and the only tests
# asserted class membership in `client.middleware`. So not one line of the
# module had ever run anywhere, and staging would have been its first
# execution. That is the same shape as the bug this ticket exists to fix, so
# these tests drive every hook directly, including its failure path.
# ---------------------------------------------------------------------------


class _FakeLogger:
    """Captures `exception(...)` calls so a swallowed hook failure is provable."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.exceptions: list[str] = []

    def warning(self, msg: str, *a: object, **k: object) -> None:
        self.warnings.append(msg)

    def exception(self, msg: str, *a: object, **k: object) -> None:
        self.exceptions.append(msg)


class _FakeInngestClient:
    def __init__(self) -> None:
        self.app_id = "bullet-api-test"
        self.logger = _FakeLogger()


def _middleware() -> tuple[SentryMiddleware, _FakeInngestClient]:
    client = _FakeInngestClient()
    return SentryMiddleware(client, None), client  # type: ignore[arg-type]


def _ctx() -> Mock:
    ctx = Mock()
    ctx.events = [Mock()]
    ctx.event.id = "evt_1"
    ctx.event.name = "client.created"
    ctx.run_id = "run_1"
    ctx.attempt = 0
    return ctx


def _function() -> Mock:
    fn = Mock()
    fn.local_id = "create-ghl-subaccount"
    fn.name = "create-ghl-subaccount"
    return fn


def test_transform_input_sets_the_tags_that_make_an_issue_actionable() -> None:
    mw, _ = _middleware()
    with patch.object(inngest_sentry.sentry_sdk, "set_tag") as set_tag:
        mw.transform_input(_ctx(), _function(), Mock())
    tagged = {call.args[0]: call.args[1] for call in set_tag.call_args_list}
    assert tagged["inngest.run.id"] == "run_1"
    assert tagged["inngest.function.id"] == "create-ghl-subaccount"
    assert tagged["inngest.event.name"] == "client.created"
    # Inngest retries several times and each attempt captures again, so without
    # this one failing job raises ~5 events and a recovered blip is
    # indistinguishable from a terminal failure.
    assert tagged["inngest.attempt"] == 0


def test_transform_output_captures_only_when_there_is_an_error() -> None:
    mw, _ = _middleware()
    with patch.object(inngest_sentry.sentry_sdk, "capture_exception") as capture:
        mw.transform_output(Mock(error=None))
        assert capture.call_count == 0
        boom = RuntimeError("handler blew up")
        mw.transform_output(Mock(error=boom))
        capture.assert_called_once_with(boom)


def test_flush_is_bounded_and_only_runs_when_something_was_captured() -> None:
    """An unbounded flush blocking-joins the transport for up to 2s ON THE
    EVENT LOOP shared with the PandaDoc webhook and the dashboard API."""
    mw, _ = _middleware()
    with patch.object(inngest_sentry.sentry_sdk, "flush") as flush:
        mw.before_response()
        assert flush.call_count == 0, "nothing captured - must not flush at all"

    with patch.object(inngest_sentry.sentry_sdk, "capture_exception"):
        mw.transform_output(Mock(error=RuntimeError("x")))
    with patch.object(inngest_sentry.sentry_sdk, "flush") as flush:
        mw.before_response()
        flush.assert_called_once()
        assert flush.call_args.kwargs["timeout"] == pytest.approx(0.5)


# --- THE regression guard for P1-1 -----------------------------------------


@pytest.mark.parametrize(
    ("hook", "args"),
    [
        ("before_response", ()),
        ("transform_input", "INPUT"),
        ("transform_output", "OUTPUT"),
    ],
)
def test_no_hook_can_ever_raise(hook: str, args: object) -> None:
    """A raising hook does not just lose telemetry - it CHANGES THE JOB.

    Verified in the SDK: `function.py` returns `CallResult(err)` OVER an
    already-successful result when a hook raises, and `errors.is_retriable`
    returns True for any non-Inngest exception. Since no handler uses
    `ctx.step.run`, nothing is memoised, so the retry re-runs every side
    effect - the GHL location POST, the R2 upload, the `client.created` emit.
    With the S1-26d indexing lag that can mint a duplicate sub-account in
    Bullet's LIVE agency.

    So: Sentry blowing up must be survivable, every time.
    """
    mw, client = _middleware()
    # Make the middleware believe it captured something, so before_response
    # actually reaches the flush it is supposed to survive.
    mw._captured = True

    boom = RuntimeError("sentry is down")
    with (
        patch.object(inngest_sentry.sentry_sdk, "set_tag", side_effect=boom),
        patch.object(inngest_sentry.sentry_sdk, "capture_exception", side_effect=boom),
        patch.object(inngest_sentry.sentry_sdk, "flush", side_effect=boom),
    ):
        if args == "INPUT":
            mw.transform_input(_ctx(), _function(), Mock())
        elif args == "OUTPUT":
            mw.transform_output(Mock(error=RuntimeError("handler failed")))
        else:
            mw.before_response()

    # Swallowed, and loudly enough to diagnose.
    assert client.logger.exceptions, f"{hook} swallowed the failure without logging it"


def test_constructor_survives_sentry_raising() -> None:
    """A constructor raise would fail the run before the handler even runs."""
    client = _FakeInngestClient()
    with patch.object(
        inngest_sentry.sentry_sdk, "set_tag", side_effect=RuntimeError("sentry is down")
    ):
        SentryMiddleware(client, None)  # type: ignore[arg-type]
    assert client.logger.exceptions


def test_vendored_hook_names_still_exist_on_the_sdk_base() -> None:
    """Vendoring turns what would have been a loud ImportError into a SILENT
    no-op: if 0.5.19+ renames a hook, our override becomes a dead method that
    is simply never called, and worker errors quietly stop reaching Sentry.
    The version pin guards the handler contract; this guards the middleware one.
    """
    for hook in ("before_response", "transform_input", "transform_output"):
        assert hasattr(inngest.MiddlewareSync, hook), (
            f"inngest.MiddlewareSync no longer defines {hook!r}, so our vendored "
            "override is now dead code and worker errors are silently unreported."
        )


def test_every_decorated_function_is_actually_in_the_registry() -> None:
    """`FUNCTIONS` is hand-maintained, so a decorated handler that nobody
    appended is SILENTLY never registered: the event fires, nothing runs, and
    no error is raised anywhere.

    That is the same failure shape as S1-34a - work that looks done, produces
    no output, and reports nothing. `serve()` is handed `FUNCTIONS`, so
    membership of that list is the whole difference between a live function and
    dead code.

    Scans the worker package for every `inngest.Function` object and asserts
    set-equality with the registry, so the omission fails here instead of in
    production.
    """
    import importlib
    import pkgutil

    import bullet_api.worker as worker_pkg

    discovered: dict[str, inngest.Function] = {}
    for mod_info in pkgutil.iter_modules(worker_pkg.__path__):
        module = importlib.import_module(f"{worker_pkg.__name__}.{mod_info.name}")
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, inngest.Function):
                discovered[attr.local_id] = attr

    registered = {fn.local_id for fn in FUNCTIONS}
    missing = set(discovered) - registered
    assert not missing, (
        f"decorated but NOT in FUNCTIONS, so never served: {sorted(missing)}. "
        "Add them to bullet_api.worker.client.FUNCTIONS - until then their "
        "trigger events fire and nothing runs, with no error anywhere."
    )


def test_captured_flag_does_not_leak_between_concurrent_runs() -> None:
    """`_captured` is PER-INVOCATION state, and that is load-bearing.

    Several Inngest functions can be in flight in this process at once. If the
    middleware were a shared singleton, one failing run would set the flag and
    every later SUCCESSFUL run would then pay the flush - and the flag would
    never reset, so the cost would be permanent after the first error.

    It is safe because the SDK instantiates the class per invocation:
    `MiddlewareManager.add()` does `middleware(self.client, self._raw_request)`,
    and `from_client` is called per incoming request (`comm_lib/handler.py`).
    This test pins that assumption so it fails loudly if the SDK ever switches
    to sharing instances.
    """
    failing, _ = _middleware()
    clean, _ = _middleware()

    with patch.object(inngest_sentry.sentry_sdk, "capture_exception"):
        failing.transform_output(Mock(error=RuntimeError("boom")))

    assert failing._captured is True
    assert clean._captured is False, "middleware state leaked between instances"

    with patch.object(inngest_sentry.sentry_sdk, "flush") as flush:
        clean.before_response()
        assert flush.call_count == 0, "a clean run must not flush because another run failed"
