"""Integration tests for the S1-25 `create_ghl_subaccount` fan-out.

These hit Postgres (`clients` read + write-back, `platform_actions`
begin/complete/fail) via the transactional `async_session` fixture, so
every DB test is marked `@pytest.mark.db` and skips when no DATABASE_URL
is reachable. The GHL HTTP client is replaced with `FakeGhlClient` so no
API call is made and the request payload can be asserted.

Card spec mandates:

- success: a `platform_actions` row with `status='success'`, `external_id`
  set, and `clients.ghl_subaccount_id` populated;
- API failure: `status='failed'` with `last_error` and `retry_count`
  incremented;
- a concurrency cap of 3 (declared on the function);
- idempotency: a replay must not create a second sub-account or a second
  action row.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.ghl.client import FakeGhlClient, GhlClientError, GhlLocation, GhlServerError
from bullet_api.worker import CLIENT_CREATED_EVENT, PANDADOC_SIGNED_EVENT
from bullet_api.worker.ghl_subaccount import (
    ClientNotFoundError,
    create_ghl_subaccount,
    create_ghl_subaccount_core,
)

COMPANY_ID = "comp_agency_1"


async def _seed_client(
    session: AsyncSession,
    *,
    business_name: str | None = "Sample Gym Ltd",
    legal_entity: str = "Sample Gym Ltd",
    email: str = "signer@example.com",
    phone: str | None = "+447000000000",
    ghl_subaccount_id: str | None = None,
) -> uuid.UUID:
    """Insert a minimal `clients` row (as S1-25a would have created)."""
    result = await session.execute(
        text(
            "INSERT INTO clients ("
            "  email, business_name, legal_entity, contact_first_name, "
            "  contact_last_name, phone, ghl_subaccount_id, current_step, step_entered_at"
            ") VALUES ("
            "  :email, :business_name, :legal_entity, 'Sample', 'Signer', "
            "  :phone, :ghl_subaccount_id, 'signed', now()"
            ") RETURNING id"
        ),
        {
            "email": email,
            "business_name": business_name,
            "legal_entity": legal_entity,
            "phone": phone,
            "ghl_subaccount_id": ghl_subaccount_id,
        },
    )
    return result.scalar_one()


async def _seed_onboarding_event(session: AsyncSession) -> uuid.UUID:
    """Seed a `pandadoc.signed` onboarding_events row to satisfy the
    `platform_actions.event_id` FK."""
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    result = await session.execute(
        text(
            "INSERT INTO onboarding_events (event_type, external_id, payload, verified_at) "
            "VALUES (:et, :eid, cast('{}' AS jsonb), now()) RETURNING id"
        ),
        {"et": PANDADOC_SIGNED_EVENT, "eid": document_id},
    )
    return result.scalar_one()


async def _platform_action(session: AsyncSession, client_id: uuid.UUID) -> object:
    rows = await session.execute(
        text(
            "SELECT status, external_id, last_error, retry_count, payload, response "
            "FROM platform_actions WHERE client_id = :cid AND platform = 'ghl'"
        ),
        {"cid": client_id},
    )
    return rows.all()


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_success_records_action_and_writes_subaccount_id(
    async_session: AsyncSession,
) -> None:
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=GhlLocation(
            id="loc_new_1", name="Sample Gym Ltd", company_id=COMPANY_ID, raw={"id": "loc_new_1"}
        )
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.skipped is False
    assert result.ghl_subaccount_id == "loc_new_1"

    # clients.ghl_subaccount_id written back
    written = await async_session.execute(
        text("SELECT ghl_subaccount_id FROM clients WHERE id = :id"), {"id": client_id}
    )
    assert written.scalar_one() == "loc_new_1"

    # platform_actions row recorded success with external_id
    actions = await _platform_action(async_session, client_id)
    assert len(actions) == 1
    action = actions[0]
    assert action.status == "success"
    assert action.external_id == "loc_new_1"
    assert action.retry_count == 0

    # payload was persisted; the request carried the agency companyId + name
    assert ghl.calls == [
        {
            "name": "Sample Gym Ltd",
            "companyId": COMPANY_ID,
            "phone": "+447000000000",
            "prospectInfo": {
                "firstName": "Sample",
                "lastName": "Signer",
                "email": "signer@example.com",
            },
        }
    ]


@pytest.mark.db
async def test_snapshot_id_included_only_when_configured(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=GhlLocation(id="loc_1", name="n", company_id=COMPANY_ID, raw={"id": "loc_1"})
    )

    await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
        snapshot_id="snap_xyz",
    )

    assert ghl.calls[0]["snapshotId"] == "snap_xyz"


@pytest.mark.db
async def test_name_falls_back_to_legal_entity_when_no_business_name(
    async_session: AsyncSession,
) -> None:
    client_id = await _seed_client(
        async_session, business_name=None, legal_entity="Legal Entity LLC"
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=GhlLocation(id="loc_1", name="n", company_id=COMPANY_ID, raw={"id": "loc_1"})
    )

    await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert ghl.calls[0]["name"] == "Legal Entity LLC"


# --------------------------------------------------------------------------- #
# Failure modes
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_client_error_records_failed_and_raises(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(error=GhlClientError(422, "validation failed"))

    with pytest.raises(GhlClientError):
        await create_ghl_subaccount_core(
            async_session,
            ghl,
            client_id=client_id,
            onboarding_event_id=event_id,
            company_id=COMPANY_ID,
        )

    actions = await _platform_action(async_session, client_id)
    assert len(actions) == 1
    action = actions[0]
    assert action.status == "failed"
    assert "validation failed" in action.last_error
    assert action.retry_count == 1

    # No sub-account id written back on failure.
    written = await async_session.execute(
        text("SELECT ghl_subaccount_id FROM clients WHERE id = :id"), {"id": client_id}
    )
    assert written.scalar_one() is None


@pytest.mark.db
async def test_server_error_records_failed_and_propagates(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(error=GhlServerError(503, "service unavailable"))

    with pytest.raises(GhlServerError):
        await create_ghl_subaccount_core(
            async_session,
            ghl,
            client_id=client_id,
            onboarding_event_id=event_id,
            company_id=COMPANY_ID,
        )

    actions = await _platform_action(async_session, client_id)
    assert actions[0].status == "failed"
    assert actions[0].retry_count == 1


@pytest.mark.db
async def test_missing_client_raises_client_not_found(async_session: AsyncSession) -> None:
    ghl = FakeGhlClient(location=GhlLocation(id="loc_1", name="n", company_id=COMPANY_ID, raw={}))
    with pytest.raises(ClientNotFoundError):
        await create_ghl_subaccount_core(
            async_session,
            ghl,
            client_id=uuid.uuid4(),
            onboarding_event_id=None,
            company_id=COMPANY_ID,
        )
    assert ghl.calls == []  # never called GHL


# --------------------------------------------------------------------------- #
# Idempotency / short-circuit
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_replay_same_event_does_not_create_second_subaccount(
    async_session: AsyncSession,
) -> None:
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=GhlLocation(id="loc_1", name="n", company_id=COMPANY_ID, raw={"id": "loc_1"})
    )

    first = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )
    assert first.created is True

    # Replay: same client + same event id. The client now has a
    # ghl_subaccount_id, so this short-circuits without a second POST.
    second = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )
    assert second.created is False
    assert second.skipped is True
    assert second.ghl_subaccount_id == "loc_1"

    # GHL called exactly once across both runs.
    assert len(ghl.calls) == 1
    # Exactly one platform_actions row (idempotency key dedupe).
    actions = await _platform_action(async_session, client_id)
    assert len(actions) == 1
    assert actions[0].status == "success"


@pytest.mark.db
async def test_already_provisioned_client_skips_ghl_call(async_session: AsyncSession) -> None:
    client_id = await _seed_client(async_session, ghl_subaccount_id="loc_existing")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=GhlLocation(id="loc_new", name="n", company_id=COMPANY_ID, raw={}))

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.skipped is True
    assert result.created is False
    assert result.ghl_subaccount_id == "loc_existing"
    assert ghl.calls == []  # never called GHL

    # The skip is still audited as a success against the existing id.
    actions = await _platform_action(async_session, client_id)
    assert len(actions) == 1
    assert actions[0].status == "success"
    assert actions[0].external_id == "loc_existing"


# --------------------------------------------------------------------------- #
# Concurrency declaration assertion
# --------------------------------------------------------------------------- #


def test_create_ghl_subaccount_declares_concurrency_caps() -> None:
    """The function declares a global cap of 3 (rate limit) plus a
    per-client cap of 1 (no concurrent double-create). Enforcement is
    server-side in Inngest; this only asserts the declaration."""
    fn_config = create_ghl_subaccount.get_config("").main
    assert fn_config.concurrency is not None
    assert len(fn_config.concurrency) == 2

    global_cap = next(c for c in fn_config.concurrency if c.key is None)
    assert global_cap.limit == 3
    assert global_cap.scope == "fn"

    per_client = next(c for c in fn_config.concurrency if c.key == "event.data.client_id")
    assert per_client.limit == 1
    assert per_client.scope == "fn"


def test_create_ghl_subaccount_triggers_on_client_created() -> None:
    fn_config = create_ghl_subaccount.get_config("").main
    assert fn_config.triggers[0].event == CLIENT_CREATED_EVENT
