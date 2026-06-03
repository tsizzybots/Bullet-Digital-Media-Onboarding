"""Event-emitter abstraction over the Inngest client.

`EventEmitter` is a small Protocol so handlers depend on the interface
rather than on Inngest specifically. `InngestEventEmitter` is the
production wiring around the shared `inngest_client`; `FakeEventEmitter`
is used by tests to capture emitted events and assert on them without a
network call. This mirrors the email-client seam in `bullet_api.email`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import inngest

from bullet_api.worker.client import inngest_client

# Used as BOTH the onboarding_events.event_type and the Inngest event name
# so the persisted audit record and the fan-out trigger stay in lockstep.
PANDADOC_SIGNED_EVENT = "pandadoc.signed"


class EventEmitter(Protocol):
    async def send(self, name: str, data: dict) -> None:
        """Emit a single event by name with a JSON-serialisable payload."""
        ...


class InngestEventEmitter:
    """Production emitter - sends one event via the shared Inngest client."""

    def __init__(self, client: inngest.Inngest) -> None:
        self._client = client

    async def send(self, name: str, data: dict) -> None:
        await self._client.send(inngest.Event(name=name, data=data))


@dataclass
class FakeEventEmitter:
    """Test double. Records emitted events on `sent` for assertions."""

    sent: list[tuple[str, dict]] = field(default_factory=list)

    async def send(self, name: str, data: dict) -> None:
        self.sent.append((name, data))


def get_event_emitter() -> EventEmitter:
    """FastAPI dependency. Tests override this with a `FakeEventEmitter`
    instance so they can read `emitter.sent` and assert against it."""
    return InngestEventEmitter(inngest_client)
