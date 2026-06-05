"""Integration tests for the S1-25a `create_client_record` orchestrator.

These hit Postgres (the `clients` upsert + `onboarding_events` backfill)
via the transactional `async_session` fixture, so every test is marked
`@pytest.mark.db` and skips when no DATABASE_URL is reachable. The
Inngest event emitter is replaced with `FakeEventEmitter` so emitted
events are captured rather than sent.

The S1-25a card spec mandates three behaviours:

- (a) A `pandadoc.signed` event for a new document creates exactly one
  clients row with mapped fields and `current_step = 'signed'`;
  `onboarding_events.client_id` is backfilled and `processed_at` set;
  exactly one `client.created` event is emitted (AFTER commit).
- (b) Replay of the same document_id creates no second clients row
  (idempotent on the UNIQUE INDEX from migration 0007) and is safe to
  re-emit. `processed_at` is preserved (COALESCE - set-once semantics).
- (c) A document body missing the `Client.Email` token raises a clear
  error and writes no partial clients row.

Plus the failure-mode matrix from the remediation plan: PandaDoc 404
propagation as a NonRetriable cause, orphan onboarding_events.id as
`OnboardingEventNotFoundError`, emit-failure-after-commit retry safety,
and a regression test pinning that `onboarding_events.payload` JSONB is
NOT used for field extraction (the architecture pivot).
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.pandadoc.client import FakePandaDocClient, PandaDocNotFound
from bullet_api.worker import CLIENT_CREATED_EVENT, PANDADOC_SIGNED_EVENT, FakeEventEmitter
from bullet_api.worker.client_record import (
    LEGAL_ENTITY_PLACEHOLDER,
    OnboardingEventNotFoundError,
    create_client_record_core,
    fetch_document_for_orchestrator,
)
from bullet_api.worker.clients_payload import PandaDocPayloadError


def _detail_body(
    document_id: str,
    *,
    client_email: str = "signer@example.com",
    client_first_name: str = "Sample",
    client_last_name: str = "Signer",
    business_name: str = "Sample Gym Ltd",
    legal_entity: str = "Sample Gym Ltd",
    hubspot_contact_id: str = "hs-contact-1234",
) -> dict:
    """Build a PandaDoc document detail body matching Bullet's UK template shape.

    Field names pinned to the inspection capture of 04/06/2026 (see
    `apps/api/scripts/inspect_pandadoc_document.py`).
    """
    return {
        "id": document_id,
        "name": "Bullet Digital Media - Digital Marketing Partnership Agreement",
        "status": "document.completed",
        "tokens": [
            {"name": "Client.Email", "value": client_email},
            {"name": "Client.FirstName", "value": client_first_name},
            {"name": "Client.LastName", "value": client_last_name},
            {"name": "Client.Phone", "value": "+447000000000"},
            {"name": "Company.Name", "value": business_name},
            {"name": "Company.Address", "value": "123 High Street"},
            {"name": "Company.State", "value": "Greater Manchester"},
            {"name": "Company.Zip", "value": "M1 1AA"},
            {"name": "Company.Country", "value": "United Kingdom"},
            {"name": "Deal.Currency", "value": "GBP"},
            {"name": "Deal.MonthlyServiceFee", "value": "2500"},
        ],
        "fields": [
            {"name": "Enter Your Company's Legal Trading Name", "value": legal_entity},
        ],
        "metadata": {
            "hubspot.contact_id": hubspot_contact_id,
            "hubspot.deal_id": "hs-deal-5678",
            "hubspot.company_id": "hs-company-9012",
        },
        "template": {"id": "tpl-uk-v3"},
    }


async def _seed_onboarding_event(
    session: AsyncSession,
    document_id: str,
    *,
    payload: dict | None = None,
) -> uuid.UUID:
    """Seed a minimal onboarding_events row matching the S1-22 webhook insert.

    The orchestrator does NOT read this row's payload for field
    extraction (it fetches the document body via the PandaDoc API
    instead), so the payload defaults to a stub `{}`. Tests that want
    to assert the JSONB is ignored pass an explicit `payload` to
    confirm.
    """
    result = await session.execute(
        text(
            "INSERT INTO onboarding_events "
            "  (event_type, external_id, payload, verified_at) "
            "VALUES (:et, :eid, cast(:p AS jsonb), now()) "
            "RETURNING id"
        ),
        {
            "et": PANDADOC_SIGNED_EVENT,
            "eid": document_id,
            "p": json.dumps(payload or {}),
        },
    )
    return result.scalar_one()


# --------------------------------------------------------------------------- #
# (a) Happy path
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_new_signing_creates_client_and_emits(async_session: AsyncSession) -> None:
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    event_id = await _seed_onboarding_event(async_session, document_id)
    document = _detail_body(document_id)
    emitter = FakeEventEmitter()

    result = await create_client_record_core(
        async_session,
        onboarding_event_id=event_id,
        document_id=document_id,
        document=document,
        emitter=emitter,
    )

    assert result.created is True
    assert result.client_created_emitted is True

    # Exactly one clients row, with the mapped fields and current_step=signed.
    client_count = await async_session.execute(
        text("SELECT count(*) FROM clients WHERE pandadoc_document_id = :doc"),
        {"doc": document_id},
    )
    assert client_count.scalar_one() == 1

    client_row = await async_session.execute(
        text(
            "SELECT id, email, business_name, legal_entity, contact_first_name, "
            "  contact_last_name, phone, hubspot_contact_id, current_step::text AS step "
            "FROM clients WHERE pandadoc_document_id = :doc"
        ),
        {"doc": document_id},
    )
    row = client_row.one()
    assert row.id == result.client_id
    assert row.email == "signer@example.com"
    assert row.business_name == "Sample Gym Ltd"
    assert row.legal_entity == "Sample Gym Ltd"
    assert row.contact_first_name == "Sample"
    assert row.contact_last_name == "Signer"
    assert row.phone == "+447000000000"
    assert row.hubspot_contact_id == "hs-contact-1234"
    assert row.step == "signed"

    # The onboarding_events row is backfilled with client_id + processed_at.
    event_row = await async_session.execute(
        text("SELECT client_id, processed_at FROM onboarding_events WHERE id = :id"),
        {"id": event_id},
    )
    event = event_row.one()
    assert event.client_id == result.client_id
    assert event.processed_at is not None

    # Exactly one client.created emission, carrying the right ids + email.
    assert len(emitter.sent) == 1
    name, data = emitter.sent[0]
    assert name == CLIENT_CREATED_EVENT
    assert data["client_id"] == str(result.client_id)
    assert data["document_id"] == document_id
    assert data["onboarding_event_id"] == str(event_id)
    assert data["email"] == "signer@example.com"


@pytest.mark.db
async def test_missing_legal_entity_falls_back_to_business_name(
    async_session: AsyncSession,
) -> None:
    """`clients.legal_entity` is NOT NULL; when the form field is empty,
    the orchestrator falls back to `business_name` so the row stays
    insertable. `business_name` itself stays as the trading name."""
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    event_id = await _seed_onboarding_event(async_session, document_id)
    document = _detail_body(document_id)
    document["fields"] = []  # strip the legal-entity form field

    await create_client_record_core(
        async_session,
        onboarding_event_id=event_id,
        document_id=document_id,
        document=document,
        emitter=FakeEventEmitter(),
    )

    row = await async_session.execute(
        text("SELECT business_name, legal_entity FROM clients WHERE pandadoc_document_id = :doc"),
        {"doc": document_id},
    )
    client = row.one()
    assert client.business_name == "Sample Gym Ltd"
    assert client.legal_entity == "Sample Gym Ltd"


@pytest.mark.db
async def test_missing_both_legal_entity_and_business_name_uses_placeholder_only_for_legal_entity(
    async_session: AsyncSession,
) -> None:
    """The placeholder must NEVER land in `business_name` - that column
    is NULL-able in the schema, so missing == NULL is the correct
    representation. Only `legal_entity` (NOT NULL) gets the placeholder
    so the dashboard "needs review" alert isn't duplicated across both
    columns."""
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    event_id = await _seed_onboarding_event(async_session, document_id)
    document = _detail_body(document_id)
    document["fields"] = []
    document["tokens"] = [{"name": "Client.Email", "value": "signer@example.com"}]

    await create_client_record_core(
        async_session,
        onboarding_event_id=event_id,
        document_id=document_id,
        document=document,
        emitter=FakeEventEmitter(),
    )

    row = await async_session.execute(
        text("SELECT business_name, legal_entity FROM clients WHERE pandadoc_document_id = :doc"),
        {"doc": document_id},
    )
    client = row.one()
    assert client.legal_entity == LEGAL_ENTITY_PLACEHOLDER
    assert client.business_name is None


# --------------------------------------------------------------------------- #
# (b) Replay idempotency
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_replay_of_same_document_id_is_idempotent(
    async_session: AsyncSession,
) -> None:
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    event_id = await _seed_onboarding_event(async_session, document_id)
    document = _detail_body(document_id)

    first_emitter = FakeEventEmitter()
    first = await create_client_record_core(
        async_session,
        onboarding_event_id=event_id,
        document_id=document_id,
        document=document,
        emitter=first_emitter,
    )
    assert first.created is True
    assert len(first_emitter.sent) == 1

    # Capture the first processed_at so we can confirm the COALESCE preserves it.
    first_processed_at = (
        await async_session.execute(
            text("SELECT processed_at FROM onboarding_events WHERE id = :id"),
            {"id": event_id},
        )
    ).scalar_one()

    # Re-run against the SAME document_id. Card spec: replay creates no
    # second client row but is "safe to re-emit" - downstream consumers
    # own their own idempotency.
    second_emitter = FakeEventEmitter()
    second = await create_client_record_core(
        async_session,
        onboarding_event_id=event_id,
        document_id=document_id,
        document=document,
        emitter=second_emitter,
    )
    assert second.created is False
    assert second.client_id == first.client_id
    assert len(second_emitter.sent) == 1

    client_count = await async_session.execute(
        text("SELECT count(*) FROM clients WHERE pandadoc_document_id = :doc"),
        {"doc": document_id},
    )
    assert client_count.scalar_one() == 1

    # COALESCE must preserve the first-success processed_at on retry.
    second_processed_at = (
        await async_session.execute(
            text("SELECT processed_at FROM onboarding_events WHERE id = :id"),
            {"id": event_id},
        )
    ).scalar_one()
    assert second_processed_at == first_processed_at


# --------------------------------------------------------------------------- #
# (c) Missing required field
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_missing_client_email_token_raises_and_writes_no_client(
    async_session: AsyncSession,
) -> None:
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    event_id = await _seed_onboarding_event(async_session, document_id)
    document = _detail_body(document_id)
    # Strip the Client.Email token so extract_client_fields raises.
    document["tokens"] = [t for t in document["tokens"] if t["name"] != "Client.Email"]
    emitter = FakeEventEmitter()

    with pytest.raises(PandaDocPayloadError) as exc:
        await create_client_record_core(
            async_session,
            onboarding_event_id=event_id,
            document_id=document_id,
            document=document,
            emitter=emitter,
        )
    assert exc.value.field == "tokens[Client.Email]"

    # No clients row was written.
    client_count = await async_session.execute(
        text("SELECT count(*) FROM clients WHERE pandadoc_document_id = :doc"),
        {"doc": document_id},
    )
    assert client_count.scalar_one() == 0

    # No event emitted.
    assert emitter.sent == []


# --------------------------------------------------------------------------- #
# Failure-mode matrix tests (per remediation plan)
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_unknown_onboarding_event_id_raises_OnboardingEventNotFoundError(
    async_session: AsyncSession,
) -> None:
    """An orphan event_id is a data-consistency bug, not a PandaDoc
    payload error. Distinct exception type so log filters / alert
    routing can tell them apart."""
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    missing_event_id = uuid.uuid4()
    document = _detail_body(document_id)
    emitter = FakeEventEmitter()

    with pytest.raises(OnboardingEventNotFoundError) as exc:
        await create_client_record_core(
            async_session,
            onboarding_event_id=missing_event_id,
            document_id=document_id,
            document=document,
            emitter=emitter,
        )
    assert exc.value.event_id == missing_event_id
    assert emitter.sent == []


@pytest.mark.db
async def test_payload_jsonb_is_NOT_read_for_field_extraction(
    async_session: AsyncSession,
) -> None:
    """Regression test pinning the architecture decision: the
    orchestrator MUST fetch the document body via the PandaDoc API, NOT
    read `onboarding_events.payload`. If a future change re-introduces a
    JSONB read, the email from the JSONB would conflict with the email
    in the passed-in document, and this test would catch it."""
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    # Seed the audit row with a JSONB payload that has a DIFFERENT
    # Client.Email value than the document passed to the core. If the
    # orchestrator ever read this payload, it would extract this email
    # instead of the document's, and the assertion below would fail.
    fake_jsonb_payload = {
        "data": {"tokens": [{"name": "Client.Email", "value": "should-never-be-read@example.com"}]}
    }
    event_id = await _seed_onboarding_event(async_session, document_id, payload=fake_jsonb_payload)
    document = _detail_body(document_id, client_email="from-api@example.com")
    emitter = FakeEventEmitter()

    result = await create_client_record_core(
        async_session,
        onboarding_event_id=event_id,
        document_id=document_id,
        document=document,
        emitter=emitter,
    )

    # The clients row's email comes from the document body (API), NOT
    # the JSONB payload.
    row = await async_session.execute(
        text("SELECT email FROM clients WHERE id = :id"),
        {"id": result.client_id},
    )
    assert row.scalar_one() == "from-api@example.com"

    # And the emitted event email matches.
    assert emitter.sent[0][1]["email"] == "from-api@example.com"


class _RaisingEmitter:
    """FakeEventEmitter that raises on send. Models F11 in the failure matrix."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.send_attempts = 0

    async def send(self, name: str, data: dict) -> None:
        self.send_attempts += 1
        raise self._exc


@pytest.mark.db
async def test_emit_failure_after_commit_propagates_and_row_is_durable(
    async_session: AsyncSession,
) -> None:
    """F11: if the emit fails after the commit, the exception
    propagates (Inngest retries the function), but the DB state from
    the commit IS durable. On retry the UPSERT is a no-op and the emit
    gets another shot."""
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    event_id = await _seed_onboarding_event(async_session, document_id)
    document = _detail_body(document_id)
    emitter = _RaisingEmitter(RuntimeError("simulated Inngest network blip"))

    with pytest.raises(RuntimeError, match="simulated Inngest network blip"):
        await create_client_record_core(
            async_session,
            onboarding_event_id=event_id,
            document_id=document_id,
            document=document,
            emitter=emitter,
        )

    # CRITICAL: the client row is durable despite the emit failure.
    # This is the whole point of commit-then-emit ordering.
    client_count = await async_session.execute(
        text("SELECT count(*) FROM clients WHERE pandadoc_document_id = :doc"),
        {"doc": document_id},
    )
    assert client_count.scalar_one() == 1

    # The audit row backfill is also durable.
    event_row = await async_session.execute(
        text("SELECT client_id, processed_at FROM onboarding_events WHERE id = :id"),
        {"id": event_id},
    )
    backfilled = event_row.one()
    assert backfilled.client_id is not None
    assert backfilled.processed_at is not None

    # The emit was attempted exactly once before raising.
    assert emitter.send_attempts == 1


# --------------------------------------------------------------------------- #
# fetch_document_for_orchestrator (NonRetriable translation layer)
# --------------------------------------------------------------------------- #


async def test_fetch_document_for_orchestrator_returns_body_on_success() -> None:
    document = {"id": "doc-1", "tokens": []}
    pandadoc_client = FakePandaDocClient(details={"doc-1": document})
    out = await fetch_document_for_orchestrator(pandadoc_client, "doc-1")
    assert out == document


async def test_fetch_document_for_orchestrator_translates_404_to_non_retriable() -> None:
    import inngest

    pandadoc_client = FakePandaDocClient(details={})  # 404 for any id
    with pytest.raises(inngest.NonRetriableError) as exc:
        await fetch_document_for_orchestrator(pandadoc_client, "doc-missing")
    assert "doc-missing" in str(exc.value)
    # The PandaDocNotFound is preserved as the cause for debugging.
    assert isinstance(exc.value.__cause__, PandaDocNotFound)


async def test_fetch_document_for_orchestrator_translates_runtime_error_to_non_retriable() -> None:
    """`HttpPandaDocClient` raises RuntimeError when PANDADOC_API_KEY is
    empty (mis-configured deploy). Must NonRetriable - the deploy
    can't self-heal by retrying."""
    import inngest

    class _EmptyKeyClient:
        async def fetch_document(self, document_id: str) -> dict:  # noqa: ARG002
            raise NotImplementedError

        async def fetch_document_details(self, document_id: str) -> dict:  # noqa: ARG002
            raise RuntimeError("PANDADOC_API_KEY is empty; cannot fetch document.")

    with pytest.raises(inngest.NonRetriableError) as exc:
        await fetch_document_for_orchestrator(_EmptyKeyClient(), "any-doc")
    assert "PANDADOC_API_KEY" in str(exc.value)
    assert isinstance(exc.value.__cause__, RuntimeError)


# --------------------------------------------------------------------------- #
# Concurrency declaration assertion (F12, can't be enforced without inngest dev)
# --------------------------------------------------------------------------- #


def test_create_client_record_declares_per_document_concurrency_cap() -> None:
    """F12: the function is decorated with a per-document concurrency
    cap so duplicate `pandadoc.signed` events for the same document
    don't fan out duplicate PandaDoc fetches + duplicate `client.created`
    emits. Cap enforcement is server-side in Inngest; this test only
    asserts the declaration is correct."""
    from bullet_api.worker.client_record import create_client_record

    fn_config = create_client_record.get_config("").main
    assert fn_config.concurrency is not None
    assert len(fn_config.concurrency) == 1
    cap = fn_config.concurrency[0]
    assert cap.limit == 1
    assert cap.key == "event.data.document_id"
    assert cap.scope == "fn"
