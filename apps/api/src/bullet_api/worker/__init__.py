"""Background-worker primitives.

The shared Inngest client lives in `_inngest`; the function registry is
aggregated in `client`. The `events` module defines the event-emitter
seam so route handlers depend on a Protocol and tests can substitute a
`FakeEventEmitter` that captures emissions, mirroring the email-client
seam in `bullet_api.email`.

Public surface (importable as `from bullet_api.worker import ...`):

- Event constants: `PANDADOC_SIGNED_EVENT`, `CLIENT_CREATED_EVENT`,
  `MEET_TRANSCRIPT_READY_EVENT`, `TRANSCRIPT_LINKED_EVENT`
- Emitter seam: `EventEmitter`, `InngestEventEmitter`, `FakeEventEmitter`,
  `get_event_emitter`
- Inngest client: `inngest_client` (the shared singleton)
"""

from __future__ import annotations

from bullet_api.worker._inngest import inngest_client
from bullet_api.worker.events import (
    CLIENT_CREATED_EVENT,
    MEET_TRANSCRIPT_READY_EVENT,
    PANDADOC_SIGNED_EVENT,
    SALES_SUMMARY_READY_EVENT,
    TRANSCRIPT_LINKED_EVENT,
    EventEmitter,
    FakeEventEmitter,
    InngestEventEmitter,
    get_event_emitter,
)

__all__ = [
    "CLIENT_CREATED_EVENT",
    "EventEmitter",
    "FakeEventEmitter",
    "InngestEventEmitter",
    "MEET_TRANSCRIPT_READY_EVENT",
    "PANDADOC_SIGNED_EVENT",
    "SALES_SUMMARY_READY_EVENT",
    "TRANSCRIPT_LINKED_EVENT",
    "get_event_emitter",
    "inngest_client",
]
