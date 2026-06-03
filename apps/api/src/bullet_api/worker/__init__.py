"""Background-worker primitives.

The Inngest client and function registry live in `client`. The
`events` module adds a thin `EventEmitter` protocol so route handlers
can emit events through an interface and tests can substitute a
`FakeEventEmitter` that captures them, mirroring the email-client seam.
"""

from __future__ import annotations

from bullet_api.worker.events import (
    PANDADOC_SIGNED_EVENT,
    EventEmitter,
    FakeEventEmitter,
    InngestEventEmitter,
    get_event_emitter,
)

__all__ = [
    "PANDADOC_SIGNED_EVENT",
    "EventEmitter",
    "FakeEventEmitter",
    "InngestEventEmitter",
    "get_event_emitter",
]
