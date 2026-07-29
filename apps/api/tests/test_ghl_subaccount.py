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
- the two dedup concurrency guards (per-client + per-identity; the former
  global cap of 3 was dropped in S1-26a - Inngest's max is 2);
- idempotency: a replay must not create a second sub-account or a second
  action row.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
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
from bullet_api.worker.identity_key import compute_identity_key

COMPANY_ID = "comp_agency_1"


async def _seed_client(
    session: AsyncSession,
    *,
    business_name: str | None = "Sample Gym Ltd",
    legal_entity: str = "Sample Gym Ltd",
    email: str = "signer@example.com",
    phone: str | None = "+447000000000",
    ghl_subaccount_id: str | None = None,
    postal_code: str | None = "E8 1AA",
) -> uuid.UUID:
    """Insert a minimal `clients` row (as S1-25a would have created).

    `identity_key` is computed from `business_name` + `postal_code` exactly as
    the orchestrator does (S1-26c), so seeded rows match the returning-client
    check. Two seeds with the same name+postcode share an identity_key (a
    returning client); different name OR postcode -> different key. Passing
    `postal_code=None` yields a NULL identity_key (unidentifiable client).
    """
    identity_key = compute_identity_key(business_name, postal_code)
    result = await session.execute(
        text(
            "INSERT INTO clients ("
            "  email, business_name, legal_entity, contact_first_name, "
            "  contact_last_name, phone, ghl_subaccount_id, postal_code, "
            "  identity_key, current_step, step_entered_at"
            ") VALUES ("
            "  :email, :business_name, :legal_entity, 'Sample', 'Signer', "
            "  :phone, :ghl_subaccount_id, :postal_code, :identity_key, "
            "  'signed', now()"
            ") RETURNING id"
        ),
        {
            "email": email,
            "business_name": business_name,
            "legal_entity": legal_entity,
            "phone": phone,
            "ghl_subaccount_id": ghl_subaccount_id,
            "postal_code": postal_code,
            "identity_key": identity_key,
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
async def test_transient_error_records_failed_and_propagates(async_session: AsyncSession) -> None:
    """A transport-level error (e.g. an httpx read timeout - no HTTP status,
    so neither `GhlClientError` nor `GhlServerError`) must still be recorded
    as a `failed` action, never left stuck `in_progress`. A response-lost
    read timeout is also the worst case of the at-least-once create window
    deferred to S1-26, so it has to be visible in the audit trail."""
    client_id = await _seed_client(async_session)
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(error=httpx.ReadTimeout("response lost"))

    with pytest.raises(httpx.ReadTimeout):
        await create_ghl_subaccount_core(
            async_session,
            ghl,
            client_id=client_id,
            onboarding_event_id=event_id,
            company_id=COMPANY_ID,
        )

    actions = await _platform_action(async_session, client_id)
    assert len(actions) == 1
    assert actions[0].status == "failed"
    assert "response lost" in actions[0].last_error
    assert actions[0].retry_count == 1

    # No sub-account id written back on a failed create.
    written = await async_session.execute(
        text("SELECT ghl_subaccount_id FROM clients WHERE id = :id"), {"id": client_id}
    )
    assert written.scalar_one() is None


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
# S1-26 returning-client check + parent_client_id linking
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_returning_client_links_parent_and_reuses_db_sibling(
    async_session: AsyncSession,
) -> None:
    """Second signing for the same BUSINESS (identity key = name + postcode)
    whose prior client row is already provisioned: link the new row via
    parent_client_id and reuse the existing sub-account id without any GHL
    call. The two signings use DIFFERENT emails - identity-key matching still
    recognises the returning client, which the old email match could not."""
    parent_id = await _seed_client(
        async_session,
        email="parent@example.com",
        business_name="Returning Gym Ltd",
        legal_entity="Returning Gym Ltd",
        ghl_subaccount_id="loc_parent",
    )
    child_id = await _seed_client(
        async_session,
        email="different-email@example.com",
        business_name="Returning Gym Ltd",
        legal_entity="Returning Gym Ltd",
    )
    event_id = await _seed_onboarding_event(async_session)
    # A create location is configured but must NEVER be used on this path.
    ghl = FakeGhlClient(
        location=GhlLocation(id="loc_unused", name="n", company_id=COMPANY_ID, raw={})
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=child_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.skipped is True
    assert result.created is False
    assert result.ghl_subaccount_id == "loc_parent"
    assert result.parent_client_id == parent_id

    # New row linked to the root parent and reuses its sub-account id.
    row = await async_session.execute(
        text("SELECT parent_client_id, ghl_subaccount_id FROM clients WHERE id = :id"),
        {"id": child_id},
    )
    parent_client_id, ghl_id = row.one()
    assert parent_client_id == parent_id
    assert ghl_id == "loc_parent"

    # DB sibling short-circuits BEFORE any GHL call (neither lookup nor create).
    assert ghl.calls == []
    assert ghl.lookup_calls == []

    # Audited as success + skipped_existing marker (reason db_sibling).
    actions = await _platform_action(async_session, child_id)
    assert len(actions) == 1
    assert actions[0].status == "success"
    assert actions[0].external_id == "loc_parent"
    assert actions[0].response["skipped_existing"] is True
    assert actions[0].response["reason"] == "db_sibling"
    assert actions[0].response["parent_client_id"] == str(parent_id)


@pytest.mark.db
async def test_returning_client_db_sibling_reuse_is_idempotent_on_replay(
    async_session: AsyncSession,
) -> None:
    """Replaying the same event for a returning client does not create a
    second action row, re-call GHL, or re-link the parent. The second run
    short-circuits on the already-provisioned check (the first reuse wrote
    ghl_subaccount_id back onto the row)."""
    parent_id = await _seed_client(
        async_session, email="replay@example.com", ghl_subaccount_id="loc_parent"
    )
    child_id = await _seed_client(
        async_session, email="replay@example.com", legal_entity="Replay Site Ltd"
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=GhlLocation(id="loc_unused", name="n", company_id=COMPANY_ID, raw={})
    )

    first = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=child_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )
    assert first.parent_client_id == parent_id
    assert first.ghl_subaccount_id == "loc_parent"

    second = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=child_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )
    assert second.skipped is True
    assert second.ghl_subaccount_id == "loc_parent"

    # Still exactly one action row (idempotency-key dedupe), still linked.
    assert ghl.calls == []
    assert ghl.lookup_calls == []
    actions = await _platform_action(async_session, child_id)
    assert len(actions) == 1
    assert actions[0].status == "success"
    row = await async_session.execute(
        text("SELECT parent_client_id FROM clients WHERE id = :id"), {"id": child_id}
    )
    assert row.scalar_one() == parent_id


@pytest.mark.db
async def test_returning_client_links_to_root_not_intermediate(
    async_session: AsyncSession,
) -> None:
    """A third signing links to the ORIGINAL root, not an intermediate child,
    keeping a flat two-level tree (COALESCE(parent_client_id, id))."""
    root_id = await _seed_client(
        async_session, email="root@example.com", ghl_subaccount_id="loc_root"
    )
    # An already-linked child (parent_client_id = root, same sub-account) that
    # ALSO shares root's identity key, so it is a candidate sibling too - the
    # test proves the third signing links to the ROOT (earliest), not this
    # intermediate.
    await async_session.execute(
        text(
            "INSERT INTO clients (email, business_name, legal_entity, ghl_subaccount_id, "
            "  parent_client_id, postal_code, identity_key, current_step, step_entered_at) "
            "VALUES ('root@example.com', 'Sample Gym Ltd', 'Child One', 'loc_root', :root, "
            "  'E8 1AA', :identity_key, 'signed', now())"
        ),
        {"root": root_id, "identity_key": compute_identity_key("Sample Gym Ltd", "E8 1AA")},
    )
    third_id = await _seed_client(async_session, email="root@example.com")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=GhlLocation(id="loc_unused", name="n", company_id=COMPANY_ID, raw={})
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=third_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.parent_client_id == root_id
    assert result.ghl_subaccount_id == "loc_root"


@pytest.mark.db
async def test_ghl_lookup_reuses_existing_when_no_db_sibling(
    async_session: AsyncSession,
) -> None:
    """No DB sibling, but GHL already has a location for the email (a client
    that exists in GHL but not our DB): reuse it, parent_client_id stays NULL,
    no create POST."""
    client_id = await _seed_client(async_session, email="inghl@example.com")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=GhlLocation(id="loc_should_not_create", name="n", company_id=COMPANY_ID, raw={}),
        lookup_result=GhlLocation(
            id="loc_existing_ghl",
            name="Existing",
            company_id=COMPANY_ID,
            raw={"id": "loc_existing_ghl"},
        ),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.skipped is True
    assert result.created is False
    assert result.ghl_subaccount_id == "loc_existing_ghl"
    assert result.parent_client_id is None  # no DB sibling to link

    # Lookup ran, create did NOT.
    assert ghl.lookup_calls == [("inghl@example.com", COMPANY_ID)]
    assert ghl.calls == []

    written = await async_session.execute(
        text("SELECT ghl_subaccount_id, parent_client_id FROM clients WHERE id = :id"),
        {"id": client_id},
    )
    ghl_id, parent = written.one()
    assert ghl_id == "loc_existing_ghl"
    assert parent is None

    actions = await _platform_action(async_session, client_id)
    assert actions[0].status == "success"
    assert actions[0].external_id == "loc_existing_ghl"
    assert actions[0].response["reason"] == "ghl_lookup"


@pytest.mark.db
async def test_franchise_separation_different_postcode_not_linked(
    async_session: AsyncSession,
) -> None:
    """Same brand at a DIFFERENT postcode is a different client (a franchise):
    different identity key -> no DB sibling -> the second signing provisions
    its OWN sub-account, parent_client_id stays NULL. Email matching (both
    share `ops@brandgyms.com`) would have WRONGLY linked these."""
    await _seed_client(
        async_session,
        email="ops@brandgyms.com",
        business_name="Brand Gym",
        legal_entity="Brand Gym",
        postal_code="E8 1AA",
        ghl_subaccount_id="loc_hackney",
    )
    croydon_id = await _seed_client(
        async_session,
        email="ops@brandgyms.com",  # same email, different location
        business_name="Brand Gym",
        legal_entity="Brand Gym",
        postal_code="CR0 1AA",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=GhlLocation(
            id="loc_croydon", name="Brand Gym", company_id=COMPANY_ID, raw={"id": "loc_croydon"}
        )
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=croydon_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.parent_client_id is None
    assert result.ghl_subaccount_id == "loc_croydon"
    # A real create POST happened (not a DB-sibling reuse).
    assert len(ghl.calls) == 1


@pytest.mark.db
async def test_prefix_collision_divergent_names_flags_possible_duplicate(
    async_session: AsyncSession,
) -> None:
    """Two DIFFERENT businesses share a 6-char name prefix AND a postcode, so
    they collide on the identity key - but the full names diverge. The second
    signing is NOT merged: it is flagged possible_duplicate (pointing at the
    collided sibling) and provisions its OWN sub-account."""
    # Sanity: these DO collide on the identity key.
    assert compute_identity_key("Fitness First", "E8 1AA") == compute_identity_key(
        "Fitness Studio", "E8 1AA"
    )
    first_id = await _seed_client(
        async_session,
        email="a@fitnessfirst.com",
        business_name="Fitness First",
        legal_entity="Fitness First",
        postal_code="E8 1AA",
        ghl_subaccount_id="loc_first",
    )
    second_id = await _seed_client(
        async_session,
        email="b@fitnessstudio.com",
        business_name="Fitness Studio",
        legal_entity="Fitness Studio",
        postal_code="E8 1AA",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=GhlLocation(
            id="loc_studio", name="Fitness Studio", company_id=COMPANY_ID, raw={"id": "loc_studio"}
        )
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=second_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    # NOT merged: own sub-account created, no parent link.
    assert result.created is True
    assert result.parent_client_id is None
    assert result.ghl_subaccount_id == "loc_studio"

    # Flagged for a human, pointing at the collided sibling.
    flag = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_of FROM clients WHERE id = :id"),
        {"id": second_id},
    )
    possible_duplicate, possible_duplicate_of = flag.one()
    assert possible_duplicate is True
    assert possible_duplicate_of == first_id


@pytest.mark.db
async def test_missing_postcode_null_identity_key_creates_fresh(
    async_session: AsyncSession,
) -> None:
    """A client with no postcode has a NULL identity key, so the sibling check
    self-skips (fail-safe to CREATE) even when another client shares the name -
    and it is NOT flagged as a possible duplicate."""
    await _seed_client(
        async_session,
        email="a@nopostcode.com",
        business_name="No Postcode Gym",
        legal_entity="No Postcode Gym",
        postal_code="E8 1AA",
        ghl_subaccount_id="loc_existing",
    )
    new_id = await _seed_client(
        async_session,
        email="b@nopostcode.com",
        business_name="No Postcode Gym",
        legal_entity="No Postcode Gym",
        postal_code=None,  # -> identity_key NULL -> no match
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=GhlLocation(
            id="loc_new", name="No Postcode Gym", company_id=COMPANY_ID, raw={"id": "loc_new"}
        )
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=new_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.parent_client_id is None
    flag = await async_session.execute(
        text("SELECT possible_duplicate FROM clients WHERE id = :id"),
        {"id": new_id},
    )
    assert flag.scalar_one() is False


@pytest.mark.db
async def test_at_least_once_window_closed_by_lookup_on_retry(
    async_session: AsyncSession,
) -> None:
    """Attempt 1 creates the location in GHL but the response is lost (read
    timeout) -> action failed, no id written back. Attempt 2's lookup finds
    the orphaned location and reuses it instead of POSTing a duplicate. This
    is the at-least-once duplicate-create window S1-25 deferred to S1-26."""
    client_id = await _seed_client(async_session, email="lost@example.com")
    event_id = await _seed_onboarding_event(async_session)

    # Attempt 1: lookup finds nothing, create raises a transport-level error
    # (response lost - the location may or may not have been created in GHL).
    ghl1 = FakeGhlClient(error=httpx.ReadTimeout("response lost"))
    with pytest.raises(httpx.ReadTimeout):
        await create_ghl_subaccount_core(
            async_session,
            ghl1,
            client_id=client_id,
            onboarding_event_id=event_id,
            company_id=COMPANY_ID,
        )
    assert ghl1.calls != []  # the create POST was attempted

    # Attempt 2 (Inngest retry): GHL now reports the orphaned location.
    ghl2 = FakeGhlClient(
        lookup_result=GhlLocation(
            id="loc_orphan", name="n", company_id=COMPANY_ID, raw={"id": "loc_orphan"}
        )
    )
    result = await create_ghl_subaccount_core(
        async_session,
        ghl2,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.skipped is True
    assert result.ghl_subaccount_id == "loc_orphan"
    assert ghl2.calls == []  # NO second create POST - duplicate avoided

    # Single action row (idempotency key dedupe), transitioned failed -> success.
    actions = await _platform_action(async_session, client_id)
    assert len(actions) == 1
    assert actions[0].status == "success"
    assert actions[0].external_id == "loc_orphan"
    assert actions[0].response["reason"] == "ghl_lookup"
    assert actions[0].retry_count == 1  # the failed attempt 1 bumped it once
    # The stale error from attempt 1 is cleared when the row flips to success,
    # so the dashboard never shows a green action carrying an error string.
    assert actions[0].last_error is None


@pytest.mark.db
async def test_fresh_create_runs_lookup_first(async_session: AsyncSession) -> None:
    """No DB sibling and no existing GHL location: the lookup runs, returns
    None, then a fresh location is created (the common first-signing path)."""
    client_id = await _seed_client(async_session, email="fresh@example.com")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=GhlLocation(
            id="loc_fresh", name="n", company_id=COMPANY_ID, raw={"id": "loc_fresh"}
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
    assert result.ghl_subaccount_id == "loc_fresh"
    assert ghl.lookup_calls == [("fresh@example.com", COMPANY_ID)]
    assert len(ghl.calls) == 1


@pytest.mark.db
async def test_lookup_transport_error_records_failed_and_propagates(
    async_session: AsyncSession,
) -> None:
    """A transport-level error from the lookup (no HTTP status) must be
    recorded as a `failed` action and re-raised, never left stuck
    `in_progress`; the create POST is not attempted."""
    client_id = await _seed_client(async_session, email="lookupfail@example.com")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(lookup_error=httpx.ReadTimeout("lookup lost"))

    with pytest.raises(httpx.ReadTimeout):
        await create_ghl_subaccount_core(
            async_session,
            ghl,
            client_id=client_id,
            onboarding_event_id=event_id,
            company_id=COMPANY_ID,
        )

    assert ghl.calls == []  # never reached the create POST
    actions = await _platform_action(async_session, client_id)
    assert len(actions) == 1
    assert actions[0].status == "failed"
    assert "lookup lost" in actions[0].last_error
    assert actions[0].retry_count == 1

    written = await async_session.execute(
        text("SELECT ghl_subaccount_id FROM clients WHERE id = :id"), {"id": client_id}
    )
    assert written.scalar_one() is None


@pytest.mark.db
async def test_lookup_server_error_records_failed_and_propagates(
    async_session: AsyncSession,
) -> None:
    """A typed retriable error from the lookup (GhlServerError 5xx/429) is
    recorded as `failed` and re-raised, same as the create path - so both
    external calls have matching typed-error AND transport-error coverage and
    the wrapper can classify it for retry."""
    client_id = await _seed_client(async_session, email="lookup5xx@example.com")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(lookup_error=GhlServerError(503, "service unavailable"))

    with pytest.raises(GhlServerError):
        await create_ghl_subaccount_core(
            async_session,
            ghl,
            client_id=client_id,
            onboarding_event_id=event_id,
            company_id=COMPANY_ID,
        )

    assert ghl.calls == []  # never reached the create POST
    actions = await _platform_action(async_session, client_id)
    assert len(actions) == 1
    assert actions[0].status == "failed"
    assert "service unavailable" in actions[0].last_error
    assert actions[0].retry_count == 1


# --------------------------------------------------------------------------- #
# Flow-control declaration assertions (concurrency caps + throttle)
# --------------------------------------------------------------------------- #


def test_create_ghl_subaccount_declares_concurrency_caps() -> None:
    """The function declares EXACTLY the two dedup guards: a per-client cap of 1
    (no concurrent double-create) and a per-identity cap of 1 (S1-26c: no
    duplicate create across two rows for the same business, keyed on
    `event.data.dedup_key` = identity_key or the unique client_id). The former
    global cap of 3 was dropped (S1-26a) because Inngest allows a max of 2
    concurrency constraints - exceeding it fails ALL function registration.
    Enforcement is server-side in Inngest; this only asserts the declaration."""
    fn_config = create_ghl_subaccount.get_config("").main
    assert fn_config.concurrency is not None
    assert len(fn_config.concurrency) == 2  # <= Inngest's max of 2

    # No un-keyed global cap remains.
    assert all(c.key is not None for c in fn_config.concurrency)

    per_client = next(c for c in fn_config.concurrency if c.key == "event.data.client_id")
    assert per_client.limit == 1
    assert per_client.scope == "fn"

    per_identity = next(c for c in fn_config.concurrency if c.key == "event.data.dedup_key")
    assert per_identity.limit == 1
    assert per_identity.scope == "fn"


def test_create_ghl_subaccount_declares_global_throttle() -> None:
    """A keyless `throttle=` bounds the aggregate GHL start-rate across all
    clients (S1-26a follow-up): the two per-key concurrency caps cannot bound a
    `reconcile_pandadoc` multi-signing heal, which fans out to N distinct
    `client.created` events. `throttle=` is a SEPARATE Inngest param (not a
    concurrency constraint), so it does not re-trip the 2-constraint limit. It
    bounds run STARTS only (GCRA: at most `limit + burst` starts per period
    window, so 5 + the default burst of 1 = up to 6 starts in a single 10s
    window; sustained 5/10s), NOT in-flight calls. Enforcement is server-side;
    this only asserts the declaration."""
    fn_config = create_ghl_subaccount.get_config("").main
    assert fn_config.throttle is not None
    # Keyless: caps the whole function, not per-client/per-identity.
    assert fn_config.throttle.key is None
    assert fn_config.throttle.limit == 5
    assert fn_config.throttle.period == timedelta(seconds=10)
    # Lock the SDK-default burst so the real per-window ceiling (limit + burst
    # = 6) is a documented, deliberate choice - a silent default bump would
    # change the enforced rate without failing any test.
    assert fn_config.throttle.burst == 1
    # Throttle (queue-and-drain) was chosen over rate_limit (drop-excess):
    # dropping a backlog-heal's `client.created` would strand real signings.
    assert fn_config.rate_limit is None


def test_create_ghl_subaccount_triggers_on_client_created() -> None:
    fn_config = create_ghl_subaccount.get_config("").main
    assert fn_config.triggers[0].event == CLIENT_CREATED_EVENT
