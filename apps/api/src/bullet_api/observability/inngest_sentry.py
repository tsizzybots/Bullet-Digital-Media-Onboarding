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
This is adapted from `inngest.experimental.sentry_middleware`, whose own
docstring says: "NOT STABLE! This is an experimental feature and may change
in the future. If you'd like to use it, we recommend copying this file into
your source code." We follow that instruction rather than importing the
experimental path, so an SDK patch release cannot silently change or remove
our error reporting.

`MiddlewareSync` (not the async `Middleware`) is correct even though every
function here is async: the middleware manager holds
`list[Middleware | MiddlewareSync]` and awaits/calls each hook appropriately,
and none of these hooks do I/O worth making async.
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
    """

    def __init__(self, client: inngest.Inngest, raw_request: object) -> None:
        super().__init__(client, raw_request)

        if sentry_sdk.is_initialized() is False:
            # Not fatal: capture_exception on an uninitialised SDK is a no-op.
            # Logged so a misconfigured deploy is visible rather than silently
            # dropping worker errors (the exact failure mode S1-34b hit).
            client.logger.warning("Sentry SDK is not initialized")

        sentry_sdk.set_tag("inngest.app.id", client.app_id)

    def before_response(self) -> None:
        # Inngest invocations are short-lived HTTP requests; flush before the
        # response so an event is not lost when the worker goes idle.
        sentry_sdk.flush()

    def transform_input(
        self,
        ctx: inngest.Context | inngest.ContextSync,
        function: inngest.Function[typing.Any],
        steps: inngest.StepMemos,
    ) -> None:
        # These tags are what make a Sentry issue actionable: they point
        # straight at the Inngest run so the failure can be replayed.
        sentry_sdk.set_tag("inngest.event.count", len(ctx.events))
        sentry_sdk.set_tag("inngest.event.id", ctx.event.id)
        sentry_sdk.set_tag("inngest.event.name", ctx.event.name)
        sentry_sdk.set_tag("inngest.function.id", function.local_id)
        sentry_sdk.set_tag("inngest.function.name", function.name)
        sentry_sdk.set_tag("inngest.run.id", ctx.run_id)

    def transform_output(self, output: inngest.TransformOutputResult) -> None:
        # THE point of this middleware: the SDK has already swallowed the
        # exception into `output.error`, so this is the only place left to
        # report it.
        if output.error:
            sentry_sdk.capture_exception(output.error)


__all__ = ["SentryMiddleware"]
