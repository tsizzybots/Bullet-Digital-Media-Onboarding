"""Inngest client and function registry for bullet-api.

All background functions are registered here and served via
the FastAPI integration in bullet_api.main. Inngest Cloud calls
the serve endpoint; for local dev the Inngest dev server handles routing.
"""

from __future__ import annotations

import inngest

from bullet_api.config import get_settings


def _make_client() -> inngest.Inngest:
    s = get_settings()
    kwargs: dict[str, object] = {"app_id": "bullet-api"}
    if s.inngest_signing_key:
        kwargs["signing_key"] = s.inngest_signing_key
    if s.inngest_event_key:
        kwargs["event_key"] = s.inngest_event_key
    return inngest.Inngest(**kwargs)


inngest_client = _make_client()


@inngest_client.create_function(
    fn_id="noop-test",
    trigger=inngest.TriggerEvent(event="bullet/noop"),
)
async def noop_function(ctx: inngest.Context, step: inngest.Step) -> str:
    """No-op function registered to verify Inngest connectivity."""
    return "noop"


FUNCTIONS = [noop_function]
