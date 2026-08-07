"""Shared Inngest client - dep-free so it can be imported from anywhere.

Inngest functions registered across the codebase decorate themselves against
``inngest_client`` (e.g. ``@inngest_client.create_function(...)``). Keeping
the client in its own module - with no imports from elsewhere under
``bullet_api.worker`` - means every function file can ``from
bullet_api.worker._inngest import inngest_client`` without closing an import
cycle. ``bullet_api.worker.__init__`` re-exports the symbol so the
canonical import path for everyone outside the worker package is
``from bullet_api.worker import inngest_client``.
"""

from __future__ import annotations

import inngest

from bullet_api.config import get_settings
from bullet_api.observability.inngest_sentry import SentryMiddleware


def _make_client() -> inngest.Inngest:
    settings = get_settings()
    kwargs: dict[str, object] = {"app_id": "bullet-api"}
    if settings.inngest_signing_key:
        kwargs["signing_key"] = settings.inngest_signing_key
    if settings.inngest_event_key:
        kwargs["event_key"] = settings.inngest_event_key
    # S1-34b: report handler failures to Sentry. The Inngest SDK catches
    # exceptions inside its executor and returns them to Inngest Cloud as a
    # response body, so they never reach the ASGI stack where Sentry's FastAPI
    # integration hooks - without this, worker failures are invisible (the
    # exact gap that hid S1-34b's TypeError for a week).
    #
    # Gated on the DSN so local dev / CI / tests do not register a middleware
    # that would only log "Sentry SDK is not initialized" on every run; this
    # mirrors `init_sentry()`, which is a no-op without a DSN.
    if settings.sentry_dsn:
        kwargs["middleware"] = [SentryMiddleware]
    return inngest.Inngest(**kwargs)


inngest_client = _make_client()
