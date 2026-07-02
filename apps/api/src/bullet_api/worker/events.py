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

# Emitted by the S1-27 Google Meet webhook (`/webhooks/google-meet`) once a
# transcript-ready Pub/Sub push is verified + persisted as an onboarding_events
# row. The `capture_meet_transcript` worker keys off it to fetch + store the
# transcript. Data payload: {onboarding_event_id, transcript_name,
# conference_record_name}. The call precedes the client record, so there is no
# client_id here - linking happens later (immediately if a client already
# matches, else at signing time, else manually).
MEET_TRANSCRIPT_READY_EVENT = "google_meet.transcript_ready"

# Emitted whenever a parked transcript becomes attached to a client (immediate
# email match at capture, email match at signing time, or a manual assign).
# S1-29 (AI sales-call summary) keys off this. Data payload: {client_id,
# transcript_id, r2_key, source, document_id}. At-least-once: the producers emit
# after commit, so a consumer MUST be idempotent per `transcript_id`.
TRANSCRIPT_LINKED_EVENT = "transcript.linked"

# Emitted by the S1-29 `summarise_sales_call` worker once it has produced + validated
# the PRD §7.1 sales-call summary for a linked transcript. S1-30 keys off this to
# write the summary into `client_knowledge` (+ embeddings). Data payload:
# {client_id, transcript_id, document_id, summary} where `summary` is the validated
# §7.1 JSON (model_dump of SalesCallSummary). At-least-once: emitted after commit
# and re-emitted on retry, so the S1-30 consumer MUST be idempotent per transcript.
SALES_SUMMARY_READY_EVENT = "sales_summary.ready"


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
