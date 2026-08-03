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
    _SIBLING_CANDIDATE_LIMIT,
    ClientNotFoundError,
    create_ghl_subaccount,
    create_ghl_subaccount_core,
)
from bullet_api.worker.identity_key import compute_identity_key, identity_name

COMPANY_ID = "comp_agency_1"


async def _seed_client(
    session: AsyncSession,
    *,
    business_name: str | None = "Sample Gym Ltd",
    legal_entity: str = "Sample Gym Ltd",
    email: str = "signer@example.com",
    phone: str | None = "+44 7700 900123",
    ghl_subaccount_id: str | None = None,
    postal_code: str | None = "E8 1AA",
    parent_client_id: uuid.UUID | None = None,
    created_at_offset_seconds: int = 0,
    address: str | None = None,
) -> uuid.UUID:
    """Insert a minimal `clients` row (as S1-25a would have created).

    `identity_key` is computed from `identity_name` + `postal_code` exactly as
    the orchestrator does (S1-26c), so seeded rows match the returning-client
    check. Two seeds with the same name+postcode share an identity_key (a
    returning client); different name OR postcode -> different key. Passing
    `postal_code=None` yields a NULL identity_key (unidentifiable client).

    `created_at_offset_seconds` sets an EXPLICIT creation order. Rows seeded in
    one transaction all take the same `now()`, so tests that assert "the
    earliest candidate wins" would otherwise be resolved by the `id` tiebreak
    (a random uuid) and could pass for the wrong reason.
    """
    identity_key = compute_identity_key(identity_name(business_name, legal_entity), postal_code)
    result = await session.execute(
        text(
            "INSERT INTO clients ("
            "  email, business_name, legal_entity, contact_first_name, "
            "  contact_last_name, phone, ghl_subaccount_id, postal_code, "
            "  identity_key, address, parent_client_id, current_step, step_entered_at, "
            "  created_at"
            ") VALUES ("
            "  :email, :business_name, :legal_entity, 'Sample', 'Signer', "
            "  :phone, :ghl_subaccount_id, :postal_code, :identity_key, :address, "
            "  :parent_client_id, 'signed', now(), "
            "  now() + make_interval(secs => :created_at_offset)"
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
            "parent_client_id": parent_client_id,
            "created_at_offset": created_at_offset_seconds,
            "address": address,
        },
    )
    return result.scalar_one()


def _ghl_location(
    location_id: str,
    *,
    name: str = "Sample Gym Ltd",
    postal_code: str | None = "E8 1AA",
) -> GhlLocation:
    """A GHL location as `find_location_by_email` projects one.

    `postal_code=None` models a LEGACY sub-account (created before we started
    sending `postalCode`), which is exactly the case the corroboration guard
    must refuse to reuse.
    """
    raw: dict = {"id": location_id, "name": name, "companyId": COMPANY_ID}
    if postal_code is not None:
        raw["postalCode"] = postal_code
    return GhlLocation(id=location_id, name=name, company_id=COMPANY_ID, raw=raw)


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
        ),
        lookup_result=None,
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
            "phone": "+44 7700 900123",
            # S1-26c: sent so the location is self-identifying for a later
            # returning-client lookup (see the corroboration tests below).
            "postalCode": "E8 1AA",
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
        location=GhlLocation(id="loc_1", name="n", company_id=COMPANY_ID, raw={"id": "loc_1"}),
        lookup_result=None,
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
        location=GhlLocation(id="loc_1", name="n", company_id=COMPANY_ID, raw={"id": "loc_1"}),
        lookup_result=None,
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
    ghl = FakeGhlClient(error=GhlClientError(422, "validation failed"), lookup_result=None)

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
    ghl = FakeGhlClient(error=GhlServerError(503, "service unavailable"), lookup_result=None)

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
    ghl = FakeGhlClient(error=httpx.ReadTimeout("response lost"), lookup_result=None)

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
    ghl = FakeGhlClient(
        location=GhlLocation(id="loc_1", name="n", company_id=COMPANY_ID, raw={}),
        lookup_result=None,
    )
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
        location=GhlLocation(id="loc_1", name="n", company_id=COMPANY_ID, raw={"id": "loc_1"}),
        lookup_result=None,
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
    ghl = FakeGhlClient(
        location=GhlLocation(id="loc_new", name="n", company_id=COMPANY_ID, raw={}),
        lookup_result=None,
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
        location=GhlLocation(id="loc_unused", name="n", company_id=COMPANY_ID, raw={}),
        lookup_result=None,
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
        location=GhlLocation(id="loc_unused", name="n", company_id=COMPANY_ID, raw={}),
        lookup_result=None,
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
    keeping a flat two-level tree (COALESCE(parent_client_id, id)).

    The intermediate deliberately holds a DIFFERENT sub-account id and a LATER
    created_at than the root. Both matter: with the same id, `ghl_subaccount_id`
    would assert true whichever row won, and with tied `created_at` the winner
    would be decided by the random-uuid tiebreak - so the original version of
    this test could not fail.
    """
    root_id = await _seed_client(
        async_session,
        email="root@example.com",
        ghl_subaccount_id="loc_root",
        created_at_offset_seconds=-120,
    )
    intermediate_id = await _seed_client(
        async_session,
        email="root@example.com",
        ghl_subaccount_id="loc_intermediate",
        parent_client_id=root_id,
        created_at_offset_seconds=-60,
    )
    third_id = await _seed_client(async_session, email="root@example.com")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_unused"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=third_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.parent_client_id == root_id
    assert result.parent_client_id != intermediate_id
    assert result.ghl_subaccount_id == "loc_root"
    assert result.ghl_subaccount_id != "loc_intermediate"


@pytest.mark.db
async def test_ghl_lookup_reuses_existing_when_no_db_sibling(
    async_session: AsyncSession,
) -> None:
    """No DB sibling, but GHL already has a CORROBORATED location for the email
    (a client that exists in GHL but not our DB, or our own orphan from a
    create whose response was lost): reuse it, parent_client_id stays NULL, no
    create POST.

    The location must agree on BOTH name and postcode - which is exactly what a
    location we created ourselves carries, since `_build_location_payload` now
    sends the postcode. This is the at-least-once backstop still working.
    """
    client_id = await _seed_client(async_session, email="inghl@example.com")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_should_not_create"),
        lookup_result=_ghl_location(
            "loc_existing_ghl", name="Sample Gym Ltd", postal_code="E8 1AA"
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
    its OWN sub-account, parent_client_id stays NULL.

    The GHL lookup IS seeded with the Hackney location, because both franchises
    share `ops@brandgyms.com` and the email search really does return it. That
    is the leg the first implementation left un-re-keyed: the DB check
    separated the franchises and the email lookup immediately merged them back.
    Names match here ("Brand Gym" both), so ONLY the postcode corroboration
    keeps them apart.
    """
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
        location=_ghl_location("loc_croydon", name="Brand Gym", postal_code="CR0 1AA"),
        lookup_result=_ghl_location("loc_hackney", name="Brand Gym", postal_code="E8 1AA"),
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
    # A real create POST happened - neither a DB-sibling reuse nor a reuse of
    # the Hackney location the email lookup returned.
    assert len(ghl.calls) == 1
    assert ghl.lookup_calls == [("ops@brandgyms.com", COMPANY_ID)]

    # A postcode MISMATCH is a confident "different site", not an ambiguous
    # one, so it is not flagged for human review.
    flagged = await async_session.execute(
        text("SELECT possible_duplicate FROM clients WHERE id = :id"), {"id": croydon_id}
    )
    assert flagged.scalar_one() is False


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
        ),
        lookup_result=None,
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
        ),
        lookup_result=None,
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
    ghl1 = FakeGhlClient(error=httpx.ReadTimeout("response lost"), lookup_result=None)
    with pytest.raises(httpx.ReadTimeout):
        await create_ghl_subaccount_core(
            async_session,
            ghl1,
            client_id=client_id,
            onboarding_event_id=event_id,
            company_id=COMPANY_ID,
        )
    assert ghl1.calls != []  # the create POST was attempted

    # Attempt 2 (Inngest retry): GHL now reports the orphaned location. It
    # carries the name AND postcode attempt 1 sent, so it corroborates and the
    # backstop still recognises our own orphan.
    ghl2 = FakeGhlClient(
        lookup_result=_ghl_location("loc_orphan", name="Sample Gym Ltd", postal_code="E8 1AA")
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
        ),
        lookup_result=None,
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

    # Per-identity cap. The key is a CEL ternary rather than a bare field read
    # so `client.created` events already QUEUED when this deploys - which
    # predate `dedup_key` - fall back to their client_id instead of all
    # colliding in one null bucket. Pinned exactly: CEL has no `??`, and an
    # expression Inngest rejects fails the ALL-OR-NOTHING registration for every
    # function (the S1-26a outage), so this string is not a free-form edit.
    per_identity = next(
        c
        for c in fn_config.concurrency
        if c.key == 'has(event.data.dedup_key) ? event.data.dedup_key : "email:" + event.data.email'
    )
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


# ---------------------------------------------------------------------------
# S1-26c review fixes: corroboration on BOTH returning-client checks.
#
# The first implementation re-keyed only the DB sibling lookup. These cover the
# cases the reviewer found: a name match alone is not enough on either leg, the
# earliest candidate at a key is not necessarily the right one, and a NULL
# identity key must still have SOME dedup path.
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_sibling_match_wins_over_earlier_divergent_candidate(
    async_session: AsyncSession,
) -> None:
    """With several rows on one identity key, the NAME-MATCHING one is chosen -
    even when an unrelated business got to the key first.

    "Fitness Studio" and "Fitness First" both key `fitnes|E81AA`. Judging only
    the earliest candidate (the original `LIMIT 1`) meant a second "Fitness
    First" signing saw "Fitness Studio", declared divergence, and provisioned a
    duplicate - forever, on every future signing for that business.
    """
    await _seed_client(
        async_session,
        email="a@example.com",
        business_name="Fitness Studio",
        legal_entity="Fitness Studio",
        postal_code="E8 1AA",
        ghl_subaccount_id="loc_studio",
        created_at_offset_seconds=-120,
    )
    first_id = await _seed_client(
        async_session,
        email="b@example.com",
        business_name="Fitness First",
        legal_entity="Fitness First",
        postal_code="E8 1AA",
        ghl_subaccount_id="loc_first",
        created_at_offset_seconds=-60,
    )
    returning_id = await _seed_client(
        async_session,
        email="c@example.com",  # a third email for the same business
        business_name="Fitness First",
        legal_entity="Fitness First",
        postal_code="E8 1AA",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_should_not_create"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=returning_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.skipped is True
    assert result.ghl_subaccount_id == "loc_first"
    assert result.parent_client_id == first_id
    assert ghl.calls == []
    # A real match was found, so nothing is flagged and GHL is never consulted.
    assert ghl.lookup_calls == []
    flagged = await async_session.execute(
        text("SELECT possible_duplicate FROM clients WHERE id = :id"), {"id": returning_id}
    )
    assert flagged.scalar_one() is False


@pytest.mark.db
async def test_no_matching_sibling_flags_against_earliest_candidate(
    async_session: AsyncSession,
) -> None:
    """When NO candidate at the key matches on name, the row is flagged against
    the earliest candidate and provisioned its own sub-account (never merged)."""
    earliest_id = await _seed_client(
        async_session,
        email="a@example.com",
        business_name="Fitness Studio",
        legal_entity="Fitness Studio",
        postal_code="E8 1AA",
        ghl_subaccount_id="loc_studio",
        created_at_offset_seconds=-120,
    )
    await _seed_client(
        async_session,
        email="b@example.com",
        business_name="Fitness Center",
        legal_entity="Fitness Center",
        postal_code="E8 1AA",
        ghl_subaccount_id="loc_center",
        created_at_offset_seconds=-60,
    )
    new_id = await _seed_client(
        async_session,
        email="c@example.com",
        business_name="Fitness First",
        legal_entity="Fitness First",
        postal_code="E8 1AA",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_own", name="Fitness First"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=new_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.parent_client_id is None
    assert result.ghl_subaccount_id == "loc_own"

    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_of FROM clients WHERE id = :id"),
        {"id": new_id},
    )
    is_dup, dup_of = flagged.one()
    assert is_dup is True
    assert dup_of == earliest_id


@pytest.mark.db
async def test_null_identity_key_email_fallback_flags_but_never_links(
    async_session: AsyncSession,
) -> None:
    """A NULL identity_key detects a candidate by email but must NOT merge.

    Round-1 finding 5 restored an email sibling query so these documents kept a
    DB-side dedup signal. Round-2 finding 5 showed that LINKING on it
    reintroduces franchise conflation, because the guard leans on the postcode
    to separate franchises and the postcode is exactly what is missing here.
    So the path now flags a candidate for review and provisions its own
    sub-account: detection without merging.
    """
    parent_id = await _seed_client(
        async_session,
        email="nopostcode@example.com",
        postal_code=None,
        ghl_subaccount_id="loc_parent",
        created_at_offset_seconds=-60,
    )
    child_id = await _seed_client(async_session, email="nopostcode@example.com", postal_code=None)
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_own"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=child_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    # Its OWN sub-account - NOT the sibling's.
    assert result.created is True
    assert result.ghl_subaccount_id == "loc_own"
    assert result.parent_client_id is None
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_of FROM clients WHERE id = :id"),
        {"id": child_id},
    )
    is_dup, dup_of = flagged.one()
    assert is_dup is True
    assert dup_of == parent_id


@pytest.mark.db
async def test_null_identity_key_email_sibling_still_name_guarded(
    async_session: AsyncSession,
) -> None:
    """The email fallback is NOT a licence to merge on email alone: a shared
    mailbox with a different business name is flagged, not linked."""
    other_id = await _seed_client(
        async_session,
        email="ops@brandgyms.com",
        business_name="Hackney Gym",
        legal_entity="Hackney Gym",
        postal_code=None,
        ghl_subaccount_id="loc_hackney",
        created_at_offset_seconds=-60,
    )
    croydon_id = await _seed_client(
        async_session,
        email="ops@brandgyms.com",
        business_name="Croydon Gym",
        legal_entity="Croydon Gym",
        postal_code=None,
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_croydon", name="Croydon Gym"), lookup_result=None
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=croydon_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.ghl_subaccount_id == "loc_croydon"
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_of FROM clients WHERE id = :id"),
        {"id": croydon_id},
    )
    is_dup, dup_of = flagged.one()
    assert is_dup is True
    assert dup_of == other_id


@pytest.mark.db
async def test_ghl_hit_without_postcode_is_not_reused(async_session: AsyncSession) -> None:
    """A LEGACY GHL location (no postcode on file) cannot be corroborated, so
    it is NOT reused: own sub-account + a flag naming the suspected location.

    This is the deliberate trade-off. Reusing on the name alone is how two
    franchises on one mailbox get merged, and a wrong merge puts one client's
    assets inside another client's account. A spare sub-account is visible here
    and deletable; a wrong merge is neither.
    """
    client_id = await _seed_client(async_session, email="legacy@example.com")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_new"),
        lookup_result=_ghl_location("loc_legacy", name="Sample Gym Ltd", postal_code=None),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.ghl_subaccount_id == "loc_new"
    assert len(ghl.calls) == 1

    flagged = await async_session.execute(
        text(
            "SELECT possible_duplicate, possible_duplicate_ghl_id, possible_duplicate_of "
            "FROM clients WHERE id = :id"
        ),
        {"id": client_id},
    )
    is_dup, ghl_candidate, client_candidate = flagged.one()
    assert is_dup is True
    assert ghl_candidate == "loc_legacy"
    # The collision is with a LOCATION, not a clients row.
    assert client_candidate is None


@pytest.mark.db
async def test_ghl_hit_with_divergent_name_is_not_reused(async_session: AsyncSession) -> None:
    """A hit on a shared mailbox whose location name is a different business is
    not reused even when it carries a postcode."""
    client_id = await _seed_client(async_session, email="shared@example.com")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_new"),
        lookup_result=_ghl_location("loc_other", name="Totally Other Gym", postal_code="E8 1AA"),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.ghl_subaccount_id == "loc_new"
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_ghl_id FROM clients WHERE id = :id"),
        {"id": client_id},
    )
    is_dup, ghl_candidate = flagged.one()
    assert is_dup is True
    assert ghl_candidate == "loc_other"


@pytest.mark.db
async def test_ghl_hit_reads_postcode_nested_under_business(
    async_session: AsyncSession,
) -> None:
    """GHL returns address fields nested under `business` on some payload
    shapes; a location is not declared uncorroboratable before both are read."""
    client_id = await _seed_client(async_session, email="nested@example.com")
    event_id = await _seed_onboarding_event(async_session)
    nested = GhlLocation(
        id="loc_nested",
        name="Sample Gym Ltd",
        company_id=COMPANY_ID,
        raw={"id": "loc_nested", "business": {"postalCode": "E8 1AA"}},
    )
    ghl = FakeGhlClient(location=_ghl_location("loc_new"), lookup_result=nested)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.skipped is True
    assert result.ghl_subaccount_id == "loc_nested"
    assert ghl.calls == []


@pytest.mark.db
async def test_flagged_sibling_collision_is_not_rescued_by_ghl_lookup(
    async_session: AsyncSession,
) -> None:
    """A row flagged by the DB dedup guard must NOT be quietly merged ~130 lines
    later by the email lookup returning that same sibling's location.

    "Fitness First" and "Fitness Studio" share a key AND a mailbox. The guard
    flags the collision; the email lookup then finds "Fitness Studio"'s
    location, whose name diverges, so it is refused too.
    """
    await _seed_client(
        async_session,
        email="ops@shared.example.com",
        business_name="Fitness Studio",
        legal_entity="Fitness Studio",
        postal_code="E8 1AA",
        ghl_subaccount_id="loc_studio",
        created_at_offset_seconds=-60,
    )
    new_id = await _seed_client(
        async_session,
        email="ops@shared.example.com",
        business_name="Fitness First",
        legal_entity="Fitness First",
        postal_code="E8 1AA",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_own", name="Fitness First"),
        lookup_result=_ghl_location("loc_studio", name="Fitness Studio", postal_code="E8 1AA"),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=new_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.ghl_subaccount_id == "loc_own"
    assert result.ghl_subaccount_id != "loc_studio"


@pytest.mark.db
async def test_create_payload_carries_postcode(async_session: AsyncSession) -> None:
    """`postalCode` is sent on create so the location is SELF-IDENTIFYING: it is
    the second signal a later returning-client lookup corroborates against."""
    client_id = await _seed_client(async_session, email="payload@example.com")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_new"), lookup_result=None)

    await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert ghl.calls[0]["postalCode"] == "E8 1AA"


@pytest.mark.db
async def test_create_payload_omits_postcode_when_absent(async_session: AsyncSession) -> None:
    """No postcode on the client means the key is omitted entirely rather than
    sent as an empty string GHL might reject."""
    client_id = await _seed_client(async_session, email="nopostcode2@example.com", postal_code=None)
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_new"), lookup_result=None)

    await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert "postalCode" not in ghl.calls[0]


@pytest.mark.db
async def test_ghl_hit_with_divergent_name_and_no_postcode_is_not_flagged(
    async_session: AsyncSession,
) -> None:
    """Name says "different business" and there is no postcode to argue back
    with: a confident negative, not an ambiguity.

    This is the shared-ops-mailbox shape, which is NORMAL at Bullet. Flagging
    it would put a review badge on routine signings, and a flag that fires on
    the happy path is one the team stops reading.
    """
    client_id = await _seed_client(async_session, email="ops@shared2.example.com")
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_new"),
        lookup_result=_ghl_location("loc_other", name="Totally Other Gym", postal_code=None),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.ghl_subaccount_id == "loc_new"
    flagged = await async_session.execute(
        text("SELECT possible_duplicate FROM clients WHERE id = :id"), {"id": client_id}
    )
    assert flagged.scalar_one() is False


@pytest.mark.db
async def test_ghl_hit_undecidable_when_client_has_no_postcode(
    async_session: AsyncSession,
) -> None:
    """ "Unknown" cuts both ways: the CLIENT can be the side missing a postcode.

    Name agrees and the location carries a postcode, but with nothing to
    compare it against we cannot conclude "same site", so this is flagged for a
    human rather than merged on the name alone.
    """
    client_id = await _seed_client(
        async_session, email="clientnopostcode@example.com", postal_code=None
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_new", postal_code=None),
        lookup_result=_ghl_location("loc_maybe", name="Sample Gym Ltd", postal_code="E8 1AA"),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_ghl_id FROM clients WHERE id = :id"),
        {"id": client_id},
    )
    is_dup, ghl_candidate = flagged.one()
    assert is_dup is True
    assert ghl_candidate == "loc_maybe"


@pytest.mark.db
async def test_sibling_candidate_cap_is_logged_not_silent(
    async_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """The candidate fetch is bounded, and hitting the bound is LOGGED.

    Pins the known limitation honestly: with more divergent-name rows on one
    key than the cap, a matching row beyond the cap is missed and the signing
    provisions a duplicate. That is acceptable (a key realistically holds a
    handful of rows) but it must never look like "no returning client found" -
    a silent truncation would read as a clean negative.
    """
    for index in range(_SIBLING_CANDIDATE_LIMIT):
        await _seed_client(
            async_session,
            email=f"cap{index}@example.com",
            business_name=f"Capgym Variant {index}",
            legal_entity=f"Capgym Variant {index}",
            postal_code="E8 1AA",
            ghl_subaccount_id=f"loc_cap_{index}",
            created_at_offset_seconds=-1000 + index,
        )
    # The TRUE match, created last so it sorts past the cap.
    await _seed_client(
        async_session,
        email="realmatch@example.com",
        business_name="Capgym",
        legal_entity="Capgym",
        postal_code="E8 1AA",
        ghl_subaccount_id="loc_real_match",
        created_at_offset_seconds=-1,
    )
    returning_id = await _seed_client(
        async_session,
        email="returning@example.com",
        business_name="Capgym",
        legal_entity="Capgym",
        postal_code="E8 1AA",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_own", name="Capgym"), lookup_result=None)

    with caplog.at_level("WARNING"):
        result = await create_ghl_subaccount_core(
            async_session,
            ghl,
            client_id=returning_id,
            onboarding_event_id=event_id,
            company_id=COMPANY_ID,
        )

    # The match beyond the cap was missed - the documented limitation...
    assert result.created is True
    assert result.ghl_subaccount_id == "loc_own"
    # ...but it was NOT silent.
    assert any("candidate cap" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Reviewer finding 4: name + postcode alone is NOT enough to auto-link.
#
# `Company.Zip` is the COMPANY postcode, not the studio's, so a franchisee with
# two studios who enters only the brand plus a head-office postcode produces an
# identical identity key AND identical normalized names. A second signal (phone
# or address line) must also agree, and absence is not agreement.
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_franchisee_two_studios_one_head_office_is_not_auto_linked(
    async_session: AsyncSession,
) -> None:
    """THE scenario from the review: same brand, same head-office postcode, two
    different studios. Identity key matches, names match - and before this fix
    studio 2 was silently linked into studio 1's sub-account."""
    studio_one = await _seed_client(
        async_session,
        email="owner@f45franchise.com",
        business_name="F45 Training",
        legal_entity="F45 Training",
        postal_code="E8 1AA",  # head office, not the studio
        phone="+44 7700 900111",
        address="Studio One, 1 Mare Street",
        ghl_subaccount_id="loc_studio_one",
        created_at_offset_seconds=-60,
    )
    studio_two = await _seed_client(
        async_session,
        email="owner@f45franchise.com",
        business_name="F45 Training",
        legal_entity="F45 Training",
        postal_code="E8 1AA",
        phone="+44 7700 900222",  # different studio, different line
        address="Studio Two, 99 Kingsland Road",
    )
    event_id = await _seed_onboarding_event(async_session)
    # BOTH legs armed. The shipped version of this test omitted lookup_result,
    # so the email leg - which is what actually merged the two studios in
    # production - never fired and the test passed while the bug was live.
    # Both studios really do share the ops@ mailbox, so the lookup returns
    # studio one's location.
    ghl = FakeGhlClient(
        location=_ghl_location("loc_studio_two", name="F45 Training", postal_code="E8 1AA"),
        lookup_result=_ghl_location("loc_studio_one", name="F45 Training", postal_code="E8 1AA"),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=studio_two,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    # Its OWN sub-account, NOT studio one's.
    assert result.created is True
    assert result.ghl_subaccount_id == "loc_studio_two"
    assert result.parent_client_id is None

    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_of FROM clients WHERE id = :id"),
        {"id": studio_two},
    )
    is_dup, dup_of = flagged.one()
    assert is_dup is True
    assert dup_of == studio_one


@pytest.mark.db
async def test_name_and_postcode_alone_with_no_second_signal_is_flagged(
    async_session: AsyncSession,
) -> None:
    """Absence is not agreement: with no phone and no address on either side,
    only name + postcode agree, so this flags rather than merging."""
    existing = await _seed_client(
        async_session,
        email="a@example.com",
        phone=None,
        address=None,
        ghl_subaccount_id="loc_existing",
        created_at_offset_seconds=-60,
    )
    new_id = await _seed_client(async_session, email="b@example.com", phone=None, address=None)
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_own"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=new_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.ghl_subaccount_id == "loc_own"
    assert result.parent_client_id is None
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_of FROM clients WHERE id = :id"),
        {"id": new_id},
    )
    is_dup, dup_of = flagged.one()
    assert is_dup is True
    assert dup_of == existing


@pytest.mark.db
async def test_matching_phone_corroborates_and_links(async_session: AsyncSession) -> None:
    """A genuine returning client under a DIFFERENT email still links, because
    the phone corroborates - including across formatting differences, which
    must not be mistaken for a different business."""
    parent_id = await _seed_client(
        async_session,
        email="old@example.com",
        phone="+44 7700 900123",
        ghl_subaccount_id="loc_parent",
        created_at_offset_seconds=-60,
    )
    returning_id = await _seed_client(
        async_session,
        email="new@example.com",  # different email, same business
        phone="07700 900123",  # same number, typed differently
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_should_not_create"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=returning_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.skipped is True
    assert result.ghl_subaccount_id == "loc_parent"
    assert result.parent_client_id == parent_id
    assert ghl.calls == []
    flagged = await async_session.execute(
        text("SELECT possible_duplicate FROM clients WHERE id = :id"), {"id": returning_id}
    )
    assert flagged.scalar_one() is False


@pytest.mark.db
async def test_changed_phone_no_longer_links_now_that_address_does_not_vote(
    async_session: AsyncSession,
) -> None:
    """Address stopped counting as corroboration (review round 2, finding 2).

    Same premises, changed contact number: this USED to link on the address.
    It now flags, because address and postcode come from the same HubSpot
    company record - the address agrees precisely when the key already does,
    including in the franchisee case the bar exists to block, so it is not
    evidence of anything.
    """
    parent_id = await _seed_client(
        async_session,
        email="old2@example.com",
        phone="+44 7700 900123",
        address="12 Mare Street, London",
        ghl_subaccount_id="loc_parent2",
        created_at_offset_seconds=-60,
    )
    returning_id = await _seed_client(
        async_session,
        email="new2@example.com",
        phone="+44 7700 900999",  # new number
        address="12 Mare Street,  London",  # same premises
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_own"),
        lookup_result=None,
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=returning_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.ghl_subaccount_id == "loc_own"
    assert result.parent_client_id is None
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_of FROM clients WHERE id = :id"),
        {"id": returning_id},
    )
    is_dup, dup_of = flagged.one()
    assert is_dup is True
    assert dup_of == parent_id


@pytest.mark.db
async def test_uncorroborated_candidate_does_not_hide_a_corroborated_one(
    async_session: AsyncSession,
) -> None:
    """Scanning continues past a name-matching-but-uncorroborated row: an
    earlier franchise sibling must not shadow the real returning client."""
    await _seed_client(
        async_session,
        email="franchise@example.com",
        phone="+447700900111",
        address="Other Studio",
        ghl_subaccount_id="loc_other_studio",
        created_at_offset_seconds=-120,
    )
    real_parent = await _seed_client(
        async_session,
        email="real@example.com",
        phone="+447700900123",
        address="Real Premises",
        ghl_subaccount_id="loc_real",
        created_at_offset_seconds=-60,
    )
    returning_id = await _seed_client(
        async_session,
        email="returning@example.com",
        phone="+44 7700 900123",
        address="Real Premises",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_should_not_create"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=returning_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.skipped is True
    assert result.ghl_subaccount_id == "loc_real"
    assert result.parent_client_id == real_parent


# ---------------------------------------------------------------------------
# Review round 2: the legs must not contradict each other.
#
# Round 1 fixed each guard in isolation; nothing asserted the COMPOSED outcome,
# so the GHL leg quietly merged the rows the DB leg refused. These drive the
# core with BOTH legs armed and assert the final triple
# (ghl_subaccount_id, parent_client_id, possible_duplicate).
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_ghl_leg_never_rescues_a_row_the_db_leg_refused(
    async_session: AsyncSession,
) -> None:
    """Finding 1: flagged AND merged was the shipped behaviour.

    The DB leg refuses studio two (name matches, phone does not), then the email
    lookup returns studio one's location. The GHL guard cannot re-judge this
    safely: rows sharing an identity_key share the postcode BY CONSTRUCTION, so
    its name+postcode test collapses to a name check bar 1 already passed. The
    fix makes the GHL leg subordinate - a collision this run skips reuse
    entirely.
    """
    studio_one = await _seed_client(
        async_session,
        email="owner@f45.com",
        business_name="F45 Training",
        legal_entity="F45 Training",
        postal_code="E8 1AA",
        phone="+44 7700 900111",
        ghl_subaccount_id="loc_studio_one",
        created_at_offset_seconds=-60,
    )
    studio_two = await _seed_client(
        async_session,
        email="owner@f45.com",
        business_name="F45 Training",
        legal_entity="F45 Training",
        postal_code="E8 1AA",
        phone="+44 7700 900222",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_studio_two", name="F45 Training", postal_code="E8 1AA"),
        lookup_result=_ghl_location("loc_studio_one", name="F45 Training", postal_code="E8 1AA"),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=studio_two,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    row = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_of FROM clients WHERE id = :id"),
        {"id": studio_two},
    )
    is_dup, dup_of = row.one()
    # The whole composed outcome, not one leg's opinion of it.
    assert result.ghl_subaccount_id == "loc_studio_two"
    assert result.ghl_subaccount_id != "loc_studio_one"
    assert result.created is True
    assert result.parent_client_id is None
    assert is_dup is True
    assert dup_of == studio_one
    # The lookup must not even run once the DB leg has refused a candidate.
    assert ghl.lookup_calls == []


@pytest.mark.db
async def test_flagged_row_dashboard_claim_holds(async_session: AsyncSession) -> None:
    """`client-detail.tsx` tells the operator a flagged client "was given its
    own GHL sub-account rather than being merged". That sentence has to be TRUE
    for every flagged row, so assert it as a contract rather than trusting it.
    """
    await _seed_client(
        async_session,
        email="shared@x.com",
        business_name="Same Name Gym",
        legal_entity="Same Name Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900111",
        ghl_subaccount_id="loc_first",
        created_at_offset_seconds=-60,
    )
    second = await _seed_client(
        async_session,
        email="shared@x.com",
        business_name="Same Name Gym",
        legal_entity="Same Name Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900222",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_second", name="Same Name Gym", postal_code="E8 1AA"),
        lookup_result=_ghl_location("loc_first", name="Same Name Gym", postal_code="E8 1AA"),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=second,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    flagged = await async_session.execute(
        text("SELECT possible_duplicate FROM clients WHERE id = :id"), {"id": second}
    )
    if flagged.scalar_one():
        assert result.created is True, "flagged rows must be provisioned their OWN sub-account"
        assert result.parent_client_id is None, "a flagged row must not be linked to a parent"
