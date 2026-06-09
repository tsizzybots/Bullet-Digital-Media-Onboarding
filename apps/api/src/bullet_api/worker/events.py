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

from bullet_api.worker._inngest import inngest_client

# Used as BOTH the onboarding_events.event_type and the Inngest event name
# so the persisted audit record and the fan-out trigger stay in lockstep.
# Data payload: {document_id, onboarding_event_id, account}. `account` (S1-25c)
# is the PandaDoc account the signing came from ("uk" | "int"); the
# orchestrator uses it to fetch the document with the matching API key. Events
# emitted before S1-25c (or by a producer that omits it) default to "uk" at the
# consumer.
PANDADOC_SIGNED_EVENT = "pandadoc.signed"

# Emitted by the S1-25a `create_client_record` orchestrator once a clients
# row exists for a freshly-signed PandaDoc. Every downstream fan-out (GHL
# sub-account creation, Asana project, Stripe subscription, Xero contact,
# signed-PDF storage in R2, ...) keys off this event rather than
# `pandadoc.signed` directly, so `client_id` is guaranteed to exist in the
# payload they receive. Data payload: {client_id, onboarding_event_id,
# document_id, email, account}. `account` (S1-25c) is propagated from
# `pandadoc.signed` so the signed-PDF worker downloads with the matching
# PandaDoc API key.
CLIENT_CREATED_EVENT = "client.created"


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
