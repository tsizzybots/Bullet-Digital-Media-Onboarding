"""Sentry capture for Inngest function runs (S1-34b).

WHY THIS EXISTS
---------------
`init_sentry()` wires Sentry's FastAPI integration, which auto-captures
exceptions that bubble up the ASGI stack. Inngest function failures never
do: the Inngest SDK catches the handler exception INSIDE its own executor
and returns it to Inngest Cloud as a JSON response body. The request itself
completes (with a 500 status), so nothing propagates to Starlette and Sentry
never sees it.

The practical cost of that gap was S1-34b itself: every Inngest function had
been failing with a `TypeError` since the 0.5 upgrade and NOTHING surfaced it
for a week - the failure had to be dug out of the Inngest REST API by hand.
This middleware closes the gap so a worker failure raises a Sentry issue like
any other error.

WHY VENDORED RATHER THAN IMPORTED
---------------------------------
Adapted from `inngest.experimental.sentry_middleware`, whose own docstring
says: "NOT STABLE! This is an experimental feature and may change in the
future. If you'd like to use it, we recommend copying this file into your
source code." We follow that instruction rather than importing the
experimental path, so an SDK patch release cannot silently change or remove
our error reporting.

PROVENANCE (keep this current - it is what makes an SDK bump diffable)
    upstream: inngest/experimental/sentry_middleware.py
    version:  inngest 0.5.18
    vendored: 29/07/2026, hardened 06/08/2026 and 07/08/2026
This file deliberately DIVERGES from upstream - see the DIVERGENCES list
below. Upstream is no longer a byte-for-byte reference, so diff BEHAVIOUR
against that list, not text.

`MiddlewareSync` (not the async `Middleware`) is correct even though every
function here is async: the middleware manager holds
`list[Middleware | MiddlewareSync]` and awaits/calls each hook appropriately,
and none of these hooks do I/O worth making async.

THE TWO RULES, AND WHY THEY ARE NOT OPTIONAL
--------------------------------------------
**No hook may RAISE.** The SDK returns `CallResult(err)` OVER an
already-successful result when a hook raises (`function.py:181-186`), and any
non-Inngest exception is retriable (`errors.py:266-269`). Nothing here uses
`ctx.step.run`, so the retry re-executes every side effect - the GHL location
POST, the R2 upload, the emit - and with the S1-26d indexing lag that can mint
a duplicate sub-account in the live agency. Hence every hook body is wrapped.

**No hook may BLOCK.** Hooks are called inline on the event loop, so a
`sentry_sdk.flush()` here waited on the process-wide transport queue and
reached the same duplicate-sub-account outcome via httpx timeouts. The flush
and the whole `before_response` override were therefore DELETED, not tuned: on
a long-lived Render web service the BackgroundWorker drains continuously, so it
bought nothing. `flush_async` exists if that ever changes.

Full reasoning: CHANGELOG entries for 06/08 and 07/08.

DIVERGENCES FROM UPSTREAM (keep this list current - it is what a bump diffs against)
    1. every hook body wrapped in try/except
    2. `__init__` wrapped
    3. `before_response` override removed entirely
    4. `_captured` flag removed (it only gated the deleted flush)
    5. `ctx.attempt` tagged
"""

from __future__ import annotations

import typing

import inngest
import sentry_sdk


class SentryMiddleware(inngest.MiddlewareSync):
    """Tag Inngest runs and report handler failures to Sentry.

    Concurrency: the `set_tag` calls below write to the CURRENT Sentry scope,
    and several Inngest functions can be in flight in this process at once
    (each invocation is a separate HTTP request to the shared serve endpoint).
    Tags do not leak between them because Sentry's ASGI integration wraps every
    request in its own `isolation_scope()` (sentry_sdk 2.x,
    `integrations/asgi.py`), so each invocation tags an isolated scope. This
    holds only while the functions are served over HTTP by the FastAPI app -
    if they are ever driven outside a request (a CLI backfill, an in-process
    runner), the tags would share whatever scope the caller is on.

    Failure policy: NO hook raises. See the module docstring - a raising hook
    replaces a successful `CallResult` and the replacement is retriable, so it
    would re-run every un-memoised side effect the handler already performed.
    """

    def __init__(self, client: inngest.Inngest, raw_request: object) -> None:
        super().__init__(client, raw_request)
        # Kept so the hooks can log without reaching for a module-level logger,
        # and so a hook failure is attributable to the app it came from.
        self._logger = client.logger

        try:
            if sentry_sdk.is_initialized() is False:
                # Not fatal: capture_exception on an uninitialised SDK is a
                # no-op. Logged so a misconfigured deploy is visible rather
                # than silently dropping worker errors - the exact failure mode
                # S1-34b hit.
                client.logger.warning("Sentry SDK is not initialized")
            sentry_sdk.set_tag("inngest.app.id", client.app_id)
        except Exception:
            # A constructor raise would fail the run before the handler is even
            # called, so this is guarded like every other hook.
            client.logger.exception("Sentry middleware __init__ failed; continuing without it")

    def transform_input(
        self,
        ctx: inngest.Context | inngest.ContextSync,
        function: inngest.Function[typing.Any],
        steps: inngest.StepMemos,
    ) -> None:
        try:
            # These tags are what make a Sentry issue actionable: they point
            # straight at the Inngest run so the failure can be replayed.
            sentry_sdk.set_tag("inngest.event.count", len(ctx.events))
            sentry_sdk.set_tag("inngest.event.id", ctx.event.id)
            sentry_sdk.set_tag("inngest.event.name", ctx.event.name)
            sentry_sdk.set_tag("inngest.function.id", function.local_id)
            sentry_sdk.set_tag("inngest.function.name", function.name)
            sentry_sdk.set_tag("inngest.run.id", ctx.run_id)
            # Inngest retries a failing job several times, and each attempt
            # captures again - so one broken job raises ~5 events and a
            # self-healing blip is indistinguishable from a terminal failure.
            # Tagging the attempt makes "attempt 0 only" (transient, recovered)
            # separable from "reached the final attempt" (actually dead).
            sentry_sdk.set_tag("inngest.attempt", ctx.attempt)
        except Exception:
            self._logger.exception("Sentry middleware transform_input failed; run unaffected")

    def transform_output(self, output: inngest.TransformOutputResult) -> None:
        try:
            # THE point of this middleware: the SDK has already swallowed the
            # exception into `output.error`, so this is the only place left to
            # report it.
            if output.error:
                sentry_sdk.capture_exception(output.error)
        except Exception:
            self._logger.exception("Sentry middleware transform_output failed; run unaffected")


__all__ = ["SentryMiddleware"]
