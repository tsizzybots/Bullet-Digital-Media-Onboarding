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
    vendored: 29/07/2026, hardened 06/08/2026
The 06/08 hardening deliberately DIVERGES from upstream (see below); upstream
is no longer a byte-for-byte reference, so diff behaviour, not text.

`MiddlewareSync` (not the async `Middleware`) is correct even though every
function here is async: the middleware manager holds
`list[Middleware | MiddlewareSync]` and awaits/calls each hook appropriately,
and none of these hooks do I/O worth making async.

THE HARDENING, AND WHY IT IS NOT OPTIONAL
-----------------------------------------
Upstream lets a hook raise. That is not merely "lost telemetry" - it CHANGES
THE OUTCOME OF THE JOB. Verified in the installed SDK:

    inngest/_internal/function.py:181-186
        err = await middleware.transform_output(call_res)
        if isinstance(err, Exception):
            return execution_lib.CallResult(err)   # <- REPLACES the success

    inngest/_internal/errors.py:266-269
        def is_retriable(err): return True for any non-Inngest exception

So a Sentry blip during `transform_output` converts an ALREADY-SUCCESSFUL run
into a retriable failure. None of our handlers use `ctx.step.run`, so nothing
is memoised and the retry re-executes every side effect: the GHL location
POST, the R2 upload, the `client.created` emit. Combined with the GHL search
indexing lag (S1-26d), that can mint a DUPLICATE SUB-ACCOUNT IN BULLET'S LIVE
AGENCY.

Hence the rule enforced below: **no hook may ever raise.** Telemetry is not
allowed to corrupt the thing it observes. Every hook body is wrapped, and a
failure inside one is logged and swallowed.
"""

from __future__ import annotations

import typing

import inngest
import sentry_sdk

# Bound the flush so it cannot stall the event loop. `sentry_sdk.flush()` with
# no argument falls back to `shutdown_timeout` (2.0s by default) and
# BLOCKING-joins the transport - on the same loop that serves the PandaDoc
# webhook and the dashboard API. Telemetry does not deserve two seconds of the
# request loop, so we cap it hard and accept dropping an event under a slow
# Sentry rather than degrading the app.
_FLUSH_TIMEOUT_SECONDS = 0.5


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
        # Set by `transform_output`; read by `before_response` so a flush only
        # happens when there is actually something to send.
        self._captured = False

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

    def before_response(self) -> None:
        try:
            # Inngest invocations are short-lived HTTP requests, so an event
            # buffered at return time can be lost when the worker goes idle.
            # Only flush when something was captured - on the overwhelmingly
            # common success path this hook then costs nothing.
            if self._captured:
                sentry_sdk.flush(timeout=_FLUSH_TIMEOUT_SECONDS)
        except Exception:
            self._logger.exception("Sentry middleware before_response failed; run unaffected")

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
                self._captured = True
        except Exception:
            self._logger.exception("Sentry middleware transform_output failed; run unaffected")


__all__ = ["SentryMiddleware"]
