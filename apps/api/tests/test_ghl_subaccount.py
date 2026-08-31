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
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.ghl.client import (
    FakeGhlClient,
    GhlClientError,
    GhlLocation,
    GhlNotConfiguredError,
    GhlServerError,
)
from bullet_api.worker import CLIENT_CREATED_EVENT, PANDADOC_SIGNED_EVENT
from bullet_api.worker import ghl_subaccount as ghl_subaccount_module
from bullet_api.worker.ghl_subaccount import (
    _SIBLING_CANDIDATE_LIMIT,
    GHL_CREATE_SUBACCOUNT_ACTION,
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
    phone: str | None = None,
    contact_first_name: str | None = None,
    contact_last_name: str | None = None,
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

    **The CORROBORATING signals - `phone`, `contact_first_name`,
    `contact_last_name` - deliberately have NO shared default.** They were
    previously defaulted to one value on every seed, which meant every seeded
    pair agreed on them for free: bar 2 and bar 3 were satisfied by the fixture
    rather than by the test, so they were pinned open and mutating either to a
    constant `True` passed the whole suite (review round 5). Absence is not
    agreement anywhere in this module, so `None` is the honest default - a row
    with no phone and no signer name genuinely carries no corroboration. A test
    that wants a link must SAY so by seeding the signal, which also puts the
    load-bearing value at the call site where a reader (and a reviewer flipping
    it) can see it.

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
            "  :email, :business_name, :legal_entity, :contact_first_name, "
            "  :contact_last_name, :phone, :ghl_subaccount_id, :postal_code, "
            "  :identity_key, :address, "
            "  :parent_client_id, 'signed', now(), "
            "  now() + make_interval(secs => :created_at_offset)"
            ") RETURNING id"
        ),
        {
            "email": email,
            "business_name": business_name,
            "legal_entity": legal_entity,
            "phone": phone,
            "contact_first_name": contact_first_name,
            "contact_last_name": contact_last_name,
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
    phone: str | None = None,
) -> GhlLocation:
    """A GHL location as `find_location_by_email` projects one.

    `postal_code=None` models a LEGACY sub-account (created before we started
    sending `postalCode`), which is exactly the case the corroboration guard
    must refuse to reuse.

    `phone` defaults to ABSENT rather than to a shared value, for the same
    reason `_seed_client`'s corroborators do (review round 5): a default here
    would satisfy the GHL leg's phone bar on every location for free, so the
    bar would be pinned open and a test could not tell it apart from no bar at
    all. A test that expects REUSE must seed the phone it is corroborating
    against.
    """
    raw: dict = {"id": location_id, "name": name, "companyId": COMPANY_ID}
    if postal_code is not None:
        raw["postalCode"] = postal_code
    if phone is not None:
        raw["phone"] = phone
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
    client_id = await _seed_client(
        async_session,
        # Stated explicitly because this test ASSERTS they reach GHL in
        # the create payload; they are no longer fixture defaults.
        phone="+44 7700 900123",
        contact_first_name="Sample",
        contact_last_name="Signer",
    )
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
        # Bar 2. The link below is only legitimate because this agrees.
        phone="+44 7700 900123",
        ghl_subaccount_id="loc_parent",
    )
    child_id = await _seed_client(
        async_session,
        email="different-email@example.com",
        business_name="Returning Gym Ltd",
        legal_entity="Returning Gym Ltd",
        phone="+44 7700 900123",
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
        async_session,
        email="replay@example.com",
        phone="+44 7700 900123",  # bar 2 - what permits the reuse being replayed
        ghl_subaccount_id="loc_parent",
    )
    child_id = await _seed_client(
        async_session,
        email="replay@example.com",
        legal_entity="Replay Site Ltd",
        phone="+44 7700 900123",
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
        phone="+44 7700 900123",  # bar 2, shared across all three signings
        ghl_subaccount_id="loc_root",
        created_at_offset_seconds=-120,
    )
    intermediate_id = await _seed_client(
        async_session,
        email="root@example.com",
        phone="+44 7700 900123",
        ghl_subaccount_id="loc_intermediate",
        parent_client_id=root_id,
        created_at_offset_seconds=-60,
    )
    third_id = await _seed_client(async_session, email="root@example.com", phone="+44 7700 900123")
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

    The location must agree on name, postcode AND phone - which is exactly what
    a location we created ourselves carries, since `_build_location_payload`
    sends all three. This is the at-least-once backstop still working.

    Phone joined the bar in review round 5 (finding 5): name + postcode alone
    is the bar round 2 already rejected for the DB leg, and it let one
    franchisee's lost-response orphan swallow another's signing.
    """
    client_id = await _seed_client(
        async_session, email="inghl@example.com", phone="+44 7700 900123"
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_should_not_create"),
        lookup_result=_ghl_location(
            "loc_existing_ghl",
            name="Sample Gym Ltd",
            postal_code="E8 1AA",
            phone="+44 7700 900123",
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
    signing is NOT merged and provisions its OWN sub-account.
    CHANGED (review round 6): the row is no longer FLAGGED. Every candidate here
    diverges on name, so this is a PURE prefix collision - the flag used to
    point at `candidates[0]`, a row positively established as a different
    business. That put a possible-duplicate badge on an innocent client naming a
    business it has nothing to do with, and - because the GHL leg is subordinate
    to any collision - suppressed its own GHL lookup, so its real existing
    location was never found. It still provisions its own sub-account, which was
    always the safe outcome; only the misleading flag is gone.
    """
    # Sanity: these DO collide on the identity key.
    assert compute_identity_key("Fitness First", "E8 1AA") == compute_identity_key(
        "Fitness Studio", "E8 1AA"
    )
    await _seed_client(
        async_session,
        email="a@fitnessfirst.com",
        business_name="Fitness First",
        legal_entity="Fitness First",
        postal_code="E8 1AA",
        # Bar 2 must CLEAR (same phone) or it refuses first and the name
        # check below never decides anything - which is what disarmed this test
        # when the shared fixture default was removed (review round 5).
        phone="+44 7700 900123",
        ghl_subaccount_id="loc_first",
    )
    second_id = await _seed_client(
        async_session,
        email="b@fitnessstudio.com",
        business_name="Fitness Studio",
        legal_entity="Fitness Studio",
        postal_code="E8 1AA",
        phone="+44 7700 900123",
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
    assert possible_duplicate is False, "a pure prefix collision must not flag an innocent client"
    assert possible_duplicate_of is None
    # ...and the GHL leg was NOT suppressed, so this client can still find its
    # own existing location.
    assert ghl.lookup_calls != []


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
async def test_lost_response_orphan_is_reused_when_the_lookup_returns_it(
    async_session: AsyncSession,
) -> None:
    """Attempt 1 creates the location in GHL but the response is lost (read
    timeout) -> action failed, no id written back. Attempt 2's lookup finds the
    orphaned location and reuses it instead of POSTing a duplicate.

    RENAMED (review round 5). This was
    `test_at_least_once_window_closed_by_lookup_on_retry`, asserting a
    guarantee `_classify_ghl_hit`'s own docstring withdraws: the GHL search is
    eventually consistent and the Inngest retry follows within seconds, so it
    usually will NOT return our own orphan (S1-26d). The window is NOT closed -
    this only proves the backstop works WHEN the search does return it, which
    is the honest claim and the one that must keep holding as the corroboration
    bars tighten.
    """
    client_id = await _seed_client(async_session, email="lost@example.com", phone="+44 7700 900123")
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
    # carries the name, postcode AND phone attempt 1 sent, so it corroborates
    # on all three bars and the backstop still recognises our own orphan.
    ghl2 = FakeGhlClient(
        lookup_result=_ghl_location(
            "loc_orphan",
            name="Sample Gym Ltd",
            postal_code="E8 1AA",
            phone="+44 7700 900123",
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
    `event.data.dedup_key` = identity_key, else `email:<lowercased>`, else the
    client_id - corrected round 6; `:994` was fixed in round 5 and this one was
    missed). The former
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
    # predate `dedup_key` - fall back to `"email:" + email` instead of all
    # colliding in one null bucket. (Corrected, review round 5: this comment
    # said `client_id`, which stopped being true when the email tier was added
    # in round 4 - a per-row id is a private bucket that serialises nothing.)
    # Pinned exactly: CEL has no `??`, and an
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
        phone="+44 7700 900999",  # a DIFFERENT business, different line
        ghl_subaccount_id="loc_studio",
        created_at_offset_seconds=-120,
    )
    first_id = await _seed_client(
        async_session,
        email="b@example.com",
        business_name="Fitness First",
        legal_entity="Fitness First",
        postal_code="E8 1AA",
        phone="+44 7700 900123",  # bar 2, shared with the returning row
        ghl_subaccount_id="loc_first",
        created_at_offset_seconds=-60,
    )
    returning_id = await _seed_client(
        async_session,
        email="c@example.com",  # a third email for the same business
        business_name="Fitness First",
        legal_entity="Fitness First",
        postal_code="E8 1AA",
        phone="+44 7700 900123",
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
    """When NO candidate at the key matches on name, the row provisions its own
    sub-account and is NOT flagged (never merged either way).
    CHANGED (review round 6): the row is no longer FLAGGED. Every candidate here
    diverges on name, so this is a PURE prefix collision - the flag used to
    point at `candidates[0]`, a row positively established as a different
    business. That put a possible-duplicate badge on an innocent client naming a
    business it has nothing to do with, and - because the GHL leg is subordinate
    to any collision - suppressed its own GHL lookup, so its real existing
    location was never found. It still provisions its own sub-account, which was
    always the safe outcome; only the misleading flag is gone.
    """
    await _seed_client(
        async_session,
        email="a@example.com",
        business_name="Fitness Studio",
        legal_entity="Fitness Studio",
        postal_code="E8 1AA",
        # Bar 2 must CLEAR (same phone) or it refuses first and the name
        # check below never decides anything - which is what disarmed this test
        # when the shared fixture default was removed (review round 5).
        phone="+44 7700 900123",
        ghl_subaccount_id="loc_studio",
        created_at_offset_seconds=-120,
    )
    await _seed_client(
        async_session,
        email="b@example.com",
        business_name="Fitness Center",
        legal_entity="Fitness Center",
        postal_code="E8 1AA",
        phone="+44 7700 900123",
        ghl_subaccount_id="loc_center",
        created_at_offset_seconds=-60,
    )
    new_id = await _seed_client(
        async_session,
        email="c@example.com",
        business_name="Fitness First",
        legal_entity="Fitness First",
        postal_code="E8 1AA",
        phone="+44 7700 900123",
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
    assert is_dup is False, "no candidate cleared bar 1, so there is nothing to compare"
    assert dup_of is None
    assert ghl.lookup_calls != []


@pytest.mark.db
async def test_null_identity_key_email_fallback_links_when_phone_corroborates(
    async_session: AsyncSession,
) -> None:
    """A NULL identity_key still links when email + name + phone + the
    SIGNING CONTACT's name all agree.

    Round-1 finding 5 restored an email sibling query so these documents kept
    a DB-side dedup signal. Round-2 finding 5 made linking on it
    detection-only, since name+email+phone alone reintroduces franchise
    conflation with no postcode to anchor it. Review round 4, finding 4:
    that still left a genuinely corroborated returning client with a
    GUARANTEED duplicate whenever `Company.Zip` was blank. The fix adds bar 3
    (`require_contact_name`) rather than dropping the postcode requirement.

    Both bars are seeded EXPLICITLY below. They used to be inherited from
    `_seed_client`'s shared defaults, which meant this test could not tell
    the difference between bar 3 working and bar 3 being absent (review
    round 5). The shared signer name is the whole scenario - the same person
    signing again for one business - so it belongs at the call site.
    """
    parent_id = await _seed_client(
        async_session,
        email="nopostcode@example.com",
        postal_code=None,
        phone="+44 7700 900123",  # bar 2
        contact_first_name="Sample",  # bar 3 - the same person, both times
        contact_last_name="Signer",
        ghl_subaccount_id="loc_parent",
        created_at_offset_seconds=-60,
    )
    child_id = await _seed_client(
        async_session,
        email="nopostcode@example.com",
        postal_code=None,
        phone="+44 7700 900123",
        contact_first_name="Sample",
        contact_last_name="Signer",
    )
    event_id = await _seed_onboarding_event(async_session)
    # A create location is configured but must NEVER be used on this path.
    ghl = FakeGhlClient(location=_ghl_location("loc_unused"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=child_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    # Linked to the root parent, NOT its own sub-account.
    assert result.skipped is True
    assert result.created is False
    assert result.ghl_subaccount_id == "loc_parent"
    assert result.parent_client_id == parent_id
    assert ghl.calls == []
    assert ghl.lookup_calls == []

    row = await async_session.execute(
        text(
            "SELECT parent_client_id, ghl_subaccount_id, possible_duplicate "
            "FROM clients WHERE id = :id"
        ),
        {"id": child_id},
    )
    parent_client_id, ghl_id, is_dup = row.one()
    assert parent_client_id == parent_id
    assert ghl_id == "loc_parent"
    assert is_dup is False


@pytest.mark.db
async def test_null_identity_key_email_fallback_flags_when_contact_name_disagrees(
    async_session: AsyncSession,
) -> None:
    """THE regression test for review round 4's actual risk: two franchise
    sites sharing one `ops@` mailbox and one head-office number must NOT
    auto-link just because email, business name and phone all agree - bar 3
    (`require_contact_name`) is what tells them apart when there is no
    postcode to. This is exactly the "Brand Gym" scenario `_pick_sibling`'s
    docstring and `_SIBLING_BY_EMAIL_SQL`'s comment describe: without this
    test, a future edit that weakens bar 3 back to phone-only would silently
    reopen round 2's finding 5 and no test would catch it.
    """
    parent_id = await _seed_client(
        async_session,
        email="ops@franchise.example.com",
        postal_code=None,
        # Bar 2 must CLEAR, or it refuses first and bar 3 never decides -
        # which is exactly what disarmed this test when the shared phone
        # default was removed (review round 5). The shared head-office
        # number IS the scenario: it is why bar 3 has to exist.
        phone="+44 7700 900123",
        contact_first_name="Alice",
        contact_last_name="Hackney",
        ghl_subaccount_id="loc_parent",
        created_at_offset_seconds=-60,
    )
    child_id = await _seed_client(
        async_session,
        email="ops@franchise.example.com",
        postal_code=None,
        phone="+44 7700 900123",
        contact_first_name="Bob",
        contact_last_name="Croydon",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_own"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=child_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    # Its OWN sub-account - NOT the sibling's - despite matching email, name,
    # AND phone.
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
async def test_null_identity_key_email_fallback_flags_when_phone_disagrees(
    async_session: AsyncSession,
) -> None:
    """A NULL identity_key detects a candidate by email but must NOT merge
    without phone corroboration - the flag-only path finding 4 preserves.

    Same business name, same email, but a DIFFERENT phone: bar 2 fails, so
    this still flags for review and provisions its own sub-account rather
    than linking on name+email alone.
    """
    parent_id = await _seed_client(
        async_session,
        email="nopostcode@example.com",
        phone="+44 7700 900111",
        postal_code=None,
        # Bar 3 must CLEAR so the DIVERGENT PHONE is the sole refusal;
        # otherwise this test is over-determined and proves nothing
        # about bar 2 (review round 5).
        contact_first_name="Sam",
        contact_last_name="Taylor",
        ghl_subaccount_id="loc_parent",
        created_at_offset_seconds=-60,
    )
    child_id = await _seed_client(
        async_session,
        email="nopostcode@example.com",
        phone="+44 7700 900999",
        postal_code=None,
        contact_first_name="Sam",
        contact_last_name="Taylor",
    )
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
    mailbox with a different business name is NOT linked.
    CHANGED (review round 6): the row is no longer FLAGGED. Every candidate here
    diverges on name, so this is a PURE prefix collision - the flag used to
    point at `candidates[0]`, a row positively established as a different
    business. That put a possible-duplicate badge on an innocent client naming a
    business it has nothing to do with, and - because the GHL leg is subordinate
    to any collision - suppressed its own GHL lookup, so its real existing
    location was never found. It still provisions its own sub-account, which was
    always the safe outcome; only the misleading flag is gone.
    """
    await _seed_client(
        async_session,
        email="ops@brandgyms.com",
        business_name="Hackney Gym",
        legal_entity="Hackney Gym",
        postal_code=None,
        # Bars 2 AND 3 must clear (unkeyed path) so the divergent NAME is
        # the sole refusal - otherwise bar 1 is never reached.
        phone="+44 7700 900123",
        contact_first_name="Sam",
        contact_last_name="Taylor",
        ghl_subaccount_id="loc_hackney",
        created_at_offset_seconds=-60,
    )
    croydon_id = await _seed_client(
        async_session,
        email="ops@brandgyms.com",
        business_name="Croydon Gym",
        legal_entity="Croydon Gym",
        postal_code=None,
        phone="+44 7700 900123",
        contact_first_name="Sam",
        contact_last_name="Taylor",
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
    assert is_dup is False, "two genuinely different businesses are not duplicates"
    assert dup_of is None


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
    not reused even when it carries a postcode AND a matching phone.

    The phone is seeded on BOTH sides (round 8) so the divergent NAME is the
    only refusal - without it, phone-absence refused the reuse first and the
    name guard could be deleted with this test still green, which is exactly
    what kept it out of the mutation manifest.
    """
    client_id = await _seed_client(
        async_session, email="shared@example.com", phone="+44 7700 900123"
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_new"),
        lookup_result=_ghl_location(
            "loc_other",
            name="Totally Other Gym",
            postal_code="E8 1AA",
            phone="+44 7700 900123",
        ),
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
    client_id = await _seed_client(
        async_session, email="nested@example.com", phone="+44 7700 900123"
    )
    event_id = await _seed_onboarding_event(async_session)
    nested = GhlLocation(
        id="loc_nested",
        name="Sample Gym Ltd",
        company_id=COMPANY_ID,
        raw={
            "id": "loc_nested",
            "business": {"postalCode": "E8 1AA", "phone": "+44 7700 900123"},
        },
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


def test_handler_never_dead_letters_a_bare_runtime_error() -> None:
    """Review round 5: `except RuntimeError` was too broad to be safe.

    `httpx.StreamError` inherits `RuntimeError`, so catching the base class to
    dead-letter an empty-API-key misconfiguration ALSO dead-lettered
    transport-level streaming failures - fully recoverable signings - each then
    needing a human to re-drive. `GhlNotConfiguredError` gives the config case
    its own type.

    Asserted against the SOURCE rather than by driving the handler, matching
    how the other handler-contract tests here work (a real `inngest.Context` is
    not constructible in a unit test). This fails the moment the narrow catch
    is widened back.
    """
    import ast
    import inspect
    import textwrap

    # `httpx.StreamError` really is a RuntimeError - the premise of the fix.
    assert issubclass(httpx.StreamError, RuntimeError)
    # ...and it is NOT the narrow type, which is what makes the catch safe.
    assert not issubclass(httpx.StreamError, GhlNotConfiguredError)

    # `create_ghl_subaccount` is an Inngest `Function`; the decorated coroutine
    # is on `_handler`, same accessor `test_inngest_handlers.py` uses.
    source = textwrap.dedent(inspect.getsource(create_ghl_subaccount._handler))
    caught: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            targets = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            caught += [t.id for t in targets if isinstance(t, ast.Name)]

    assert "RuntimeError" not in caught, (
        "catching bare RuntimeError here also catches httpx.StreamError, "
        "dead-lettering a recoverable signing - catch GhlNotConfiguredError"
    )
    assert "GhlNotConfiguredError" in caught


@pytest.mark.db
async def test_ghl_hit_with_no_name_is_undecidable_not_a_confident_negative(
    async_session: AsyncSession,
) -> None:
    """Review round 5: absence read two different ways on two signals.

    `names_materially_diverge` returns True on an empty stem, which is right
    where it guards a corroborating bar and wrong here, because the
    no-postcode branch treats "name differs" as positive evidence of a
    different business and deliberately does NOT flag. So a legacy location
    with `"name": null` and no postcode was classed DIFFERENT_BUSINESS and
    passed silently, while the identical absence of a POSTCODE one line later
    yielded UNDECIDABLE and a flag.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@nonamegym.com",
        business_name="No Name Gym",
        legal_entity="No Name Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900555",
    )
    event_id = await _seed_onboarding_event(async_session)
    nameless = GhlLocation(
        id="loc_nameless",
        name=None,
        company_id=COMPANY_ID,
        raw={"id": "loc_nameless"},  # no name, no postcode: a legacy sub-account
    )
    ghl = FakeGhlClient(
        location=_ghl_location("loc_own", name="No Name Gym", postal_code="E8 1AA"),
        lookup_result=nameless,
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.ghl_subaccount_id == "loc_own"
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_ghl_id FROM clients WHERE id = :id"),
        {"id": client_id},
    )
    is_dup, dup_ghl_id = flagged.one()
    assert is_dup is True, "nothing is known about this location, so a human must see it"
    assert dup_ghl_id == "loc_nameless"


@pytest.mark.db
async def test_possible_duplicate_flag_survives_an_ordinary_create_failure(
    async_session: AsyncSession,
) -> None:
    """The flag must ride the terminal commit, INCLUDING the failure one.

    `_flag_possible_duplicate` deliberately does not commit - because an extra
    commit between the committed `in_progress` row
    and its terminal state is one more thing that can raise and strand the
    action. It therefore depends on `fail_action`'s commit to persist.

    This is the regression test for the FIRST attempt at the round-5 zombie
    fix, which rolled back unconditionally in `_record_failure`. That closed
    the zombie and silently discarded this flag - dropping the human's only
    signal that a signing was ambiguous, in exactly the case they most need it.
    The rollback is now on-demand.

    **It has to be the GHL-UNDECIDABLE flag, not the DB-collision one.** The
    first version of this test used a DB-sibling collision and the mutation
    SURVIVED: that flag is written BEFORE `begin_action`, whose own commit
    sweeps it in, so no later rollback can lose it. Only the undecidable-hit
    flag is written after `begin_action` and genuinely rides the terminal
    commit. A test on the wrong path reports the guard defended while leaving
    it wide open - the exact failure this whole review round is about.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@dupfail.com",
        business_name="Dup Fail Gym",
        legal_entity="Dup Fail Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900222",
    )
    event_id = await _seed_onboarding_event(async_session)
    # Lookup returns an UNDECIDABLE hit (name + postcode agree, no phone to
    # corroborate) so the flag is written AFTER begin_action; the create then
    # fails with an ORDINARY error, leaving the transaction perfectly usable.
    ghl = FakeGhlClient(
        error=GhlServerError(503, "unavailable"),
        lookup_result=_ghl_location(
            "loc_ambiguous", name="Dup Fail Gym", postal_code="E8 1AA", phone=None
        ),
    )

    with pytest.raises(GhlServerError):
        await create_ghl_subaccount_core(
            async_session,
            ghl,
            client_id=client_id,
            onboarding_event_id=event_id,
            company_id=COMPANY_ID,
        )

    row = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_ghl_id FROM clients WHERE id = :id"),
        {"id": client_id},
    )
    is_dup, dup_ghl_id = row.one()
    assert is_dup is True, "the flag must survive the failure commit, not be rolled back"
    assert dup_ghl_id == "loc_ambiguous"

    status = await async_session.execute(
        text("SELECT status FROM platform_actions WHERE client_id = :id AND action = :action"),
        {"id": client_id, "action": GHL_CREATE_SUBACCOUNT_ACTION},
    )
    assert status.scalar_one() == "failed"


@pytest.mark.db
async def test_db_error_while_flagging_still_records_the_action_failed(
    async_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 5: the guard could not record its own failure.

    `_flag_possible_duplicate` is wrapped so a raise there cannot strand the
    action. But the recovery path calls `fail_action`, which is another DB
    write - and when the original error is a DB error, SQLAlchemy has already
    deactivated the transaction, so that write raises `PendingRollbackError`
    instead. The action never reaches `failed` and is stranded `in_progress`
    forever: the exact zombie the guard exists to prevent, reintroduced by the
    guard itself. `_record_failure` now rolls back first.

    Simulated with a REAL deactivating DB error (invalid SQL on the live
    session), not a synthetic exception - a plain `RuntimeError` would leave
    the transaction usable and the test would pass without the fix.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@flagfail.com",
        business_name="Flag Fail Gym",
        legal_entity="Flag Fail Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900444",
    )
    event_id = await _seed_onboarding_event(async_session)

    async def _explode(session: AsyncSession, **_: object) -> None:
        # Deactivates the transaction exactly as a constraint violation would.
        await session.execute(text("SELECT * FROM a_table_that_does_not_exist"))

    monkeypatch.setattr(ghl_subaccount_module, "_flag_possible_duplicate", _explode)

    ghl = FakeGhlClient(
        location=_ghl_location("loc_own", name="Flag Fail Gym", postal_code="E8 1AA"),
        # Undecidable: name and postcode agree, phone absent -> triggers the flag.
        lookup_result=_ghl_location(
            "loc_ambiguous", name="Flag Fail Gym", postal_code="E8 1AA", phone=None
        ),
    )

    # Narrowed from bare `Exception` (review round 6): that also accepts an
    # exception raised BEFORE the code under test, so the test could pass
    # without ever reaching the branch it exists to cover. asyncpg's
    # `InFailedSQLTransactionError` arrives wrapped as a SQLAlchemy DBAPIError.
    with pytest.raises(SQLAlchemyError):
        await create_ghl_subaccount_core(
            async_session,
            ghl,
            client_id=client_id,
            onboarding_event_id=event_id,
            company_id=COMPANY_ID,
        )

    # THE assertion: the action reached a terminal state rather than being
    # stranded. Without the rollback, `fail_action` raises and this row is
    # still `in_progress`.
    status = await async_session.execute(
        text("SELECT status FROM platform_actions WHERE client_id = :id AND action = :action"),
        {"id": client_id, "action": GHL_CREATE_SUBACCOUNT_ACTION},
    )
    assert status.scalar_one() == "failed"


@pytest.mark.db
async def test_torn_action_with_no_id_anywhere_falls_through_and_self_heals(
    async_session: AsyncSession,
) -> None:
    """The OTHER half of the torn-state repair (review round 7).

    Round 6's test only exercised the branch where `external_id` IS
    recoverable from the action row. When NEITHER the client row NOR the
    action row holds an id, the recorded success cannot be trusted - the old
    code returned `(None, skipped=True)`, a clean success with no sub-account,
    and replacing the whole block with that bare return left the suite green.
    The correct route is FALLING THROUGH to the lookup-then-create path, which
    self-heals: the lookup reuses an existing location rather than minting a
    duplicate.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@torn2.com",
        business_name="Torn Two Gym",
        legal_entity="Torn Two Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900888",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_first_run"), lookup_result=None)
    first = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )
    assert first.created is True

    # Tear BOTH sides: success recorded, id lost everywhere.
    await async_session.execute(
        text("UPDATE clients SET ghl_subaccount_id = NULL WHERE id = :id"), {"id": client_id}
    )
    await async_session.execute(
        text(
            "UPDATE platform_actions SET external_id = NULL "
            "WHERE client_id = :id AND action = :action"
        ),
        {"id": client_id, "action": GHL_CREATE_SUBACCOUNT_ACTION},
    )
    await async_session.commit()

    # The replay's lookup finds the orphaned location, so the fall-through
    # reuses it - no duplicate POST, and never a success with no id.
    replay = FakeGhlClient(
        location=_ghl_location("loc_should_not_create"),
        lookup_result=_ghl_location(
            "loc_first_run",
            name="Torn Two Gym",
            postal_code="E8 1AA",
            phone="+44 7700 900888",
        ),
    )
    result = await create_ghl_subaccount_core(
        async_session,
        replay,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.ghl_subaccount_id == "loc_first_run", (
        "a success with no recoverable id must fall through, never report clean"
    )
    assert replay.calls == [], "self-healing must reuse, not mint a duplicate"
    repaired = await async_session.execute(
        text("SELECT ghl_subaccount_id FROM clients WHERE id = :id"), {"id": client_id}
    )
    assert repaired.scalar_one() == "loc_first_run"


@pytest.mark.db
async def test_a_confident_link_clears_an_earlier_possible_duplicate_flag(
    async_session: AsyncSession,
) -> None:
    """Review round 6: `_clear_possible_duplicate` was revert-green.

    Every DB-link test seeds the returning row FRESH, so the call's
    `AND possible_duplicate = true` predicate matched zero rows in all of them -
    delete the call entirely and the suite stayed green. The flag was therefore
    never actually proven to clear.

    Here the row arrives ALREADY flagged, as it would after an earlier
    uncorroborated signing, and a later run reaches a corroborated sibling. That
    run has answered the question the flag was asking, so it clears it - without
    which the badge stays up forever and the board saturates before S1-26e's
    merge action lands.
    """
    parent = await _seed_client(
        async_session,
        email="ops@clearflag.com",
        business_name="Clear Flag Gym",
        legal_entity="Clear Flag Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900321",
        ghl_subaccount_id="loc_parent",
        created_at_offset_seconds=-60,
    )
    returning = await _seed_client(
        async_session,
        email="billing@clearflag.com",
        business_name="Clear Flag Gym",
        legal_entity="Clear Flag Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900321",
    )
    # Pre-existing flag from an earlier, uncorroborated run.
    await async_session.execute(
        text(
            "UPDATE clients SET possible_duplicate = true, possible_duplicate_of = :other "
            "WHERE id = :id"
        ),
        {"id": returning, "other": parent},
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_unused"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=returning,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is False
    assert result.parent_client_id == parent
    cleared = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_of FROM clients WHERE id = :id"),
        {"id": returning},
    )
    is_dup, dup_of = cleared.one()
    assert is_dup is False, "a corroborated link answers the question the flag was asking"
    assert dup_of is None


@pytest.mark.db
async def test_commit_failure_after_successful_create_still_records_failed(
    async_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 8: the ONLY unguarded raise point on the success path.

    After a SUCCESSFUL `create_location`, `complete_action` + the client UPDATE
    + `commit()` sat outside any try/except. A commit failure (Neon resets are
    routine per this module's own comments) discarded a location id held in
    memory, left the action `in_progress` with no retry_count bump, and handed
    the retry to an eventually-consistent lookup that may mint a second
    location. Every other external-call failure routes through
    `_record_failure`; now this one does too.

    The first commit AFTER the create raises once (a real SQLAlchemyError, as
    a reset produces); `_record_failure`'s own recovery then lands `failed`.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@commitfail.com",
        business_name="Commit Fail Gym",
        legal_entity="Commit Fail Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900666",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_created_ok"), lookup_result=None)

    real_commit = async_session.commit
    state = {"armed": False, "raised": False}

    async def flaky_commit() -> None:
        if state["armed"] and not state["raised"]:
            state["raised"] = True
            raise SQLAlchemyError("simulated connection reset at COMMIT")
        await real_commit()

    monkeypatch.setattr(async_session, "commit", flaky_commit)

    # Arm AFTER the in_progress commit: the create succeeds, then the terminal
    # commit is the one that dies.
    original_create = ghl.create_location

    async def create_then_arm(payload: dict) -> GhlLocation:
        location = await original_create(payload)
        state["armed"] = True
        return location

    monkeypatch.setattr(ghl, "create_location", create_then_arm)

    with pytest.raises(SQLAlchemyError):
        await create_ghl_subaccount_core(
            async_session,
            ghl,
            client_id=client_id,
            onboarding_event_id=event_id,
            company_id=COMPANY_ID,
        )

    status = await async_session.execute(
        text(
            "SELECT status, external_id FROM platform_actions "
            "WHERE client_id = :id AND action = :action"
        ),
        {"id": client_id, "action": GHL_CREATE_SUBACCOUNT_ACTION},
    )
    row = status.one()
    assert row.status == "failed", "the action must be visibly failed, never stranded in_progress"


@pytest.mark.db
async def test_torn_action_success_with_no_id_is_repaired_not_reported_clean(
    async_session: AsyncSession,
) -> None:
    """Review round 6: `already_succeeded` appeared nowhere in this file.

    The branch only runs when the already-provisioned check ABOVE did not fire,
    i.e. precisely when `clients.ghl_subaccount_id` is NULL - so the torn state
    it exists to guard returned `(None, skipped=True)`, which Inngest records as
    a clean success. No failed action, no flag, no retry, and a client left
    permanently without a sub-account with nothing anywhere saying so.

    Here the action row recorded the id but the write-back to `clients` did not
    land, which is exactly what a crash between the two produces.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@torn.com",
        business_name="Torn Gym",
        legal_entity="Torn Gym",
        postal_code="E8 1AA",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_should_not_create"), lookup_result=None)

    # First run provisions normally.
    first = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )
    assert first.created is True

    # Tear the state: the action still says success and still holds the id, but
    # the client row loses it.
    await async_session.execute(
        text("UPDATE clients SET ghl_subaccount_id = NULL WHERE id = :id"), {"id": client_id}
    )
    await async_session.commit()

    replay = FakeGhlClient(location=_ghl_location("loc_duplicate"), lookup_result=None)
    result = await create_ghl_subaccount_core(
        async_session,
        replay,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    # Recovered from the action row - NOT reported as a success with no id, and
    # NOT re-POSTed as a duplicate.
    assert result.ghl_subaccount_id == "loc_should_not_create"
    assert result.created is False
    assert replay.calls == [], "the torn state must not mint a second location"

    repaired = await async_session.execute(
        text("SELECT ghl_subaccount_id FROM clients WHERE id = :id"), {"id": client_id}
    )
    assert repaired.scalar_one() == "loc_should_not_create", "the client row is repaired too"


@pytest.mark.db
async def test_create_payload_carries_the_address_so_our_own_locations_self_identify(
    async_session: AsyncSession,
) -> None:
    """Bar 4 on the GHL leg is only reachable if we SEND the address.

    `_classify_ghl_hit` can only disqualify on an address the location actually
    carries. Every location we create is a future lookup hit, so omitting the
    field here would leave the whole GHL-side bar 4 permanently inert against
    our own sub-accounts - the same shape as the `postalCode` fix in round 5.

    Asserted on the payload, not on a hand-built `GhlLocation.raw`: the
    scenario tests construct their fixtures directly, so they cannot notice the
    send being removed.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@payload.com",
        business_name="Payload Gym",
        legal_entity="Payload Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900777",
        address="1 Mare Street",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_new"), lookup_result=None)

    await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert len(ghl.calls) == 1
    assert ghl.calls[0]["address"] == "1 Mare Street"
    # The other two self-identifying fields, so a future edit cannot quietly
    # drop one of the three.
    assert ghl.calls[0]["postalCode"] == "E8 1AA"
    assert ghl.calls[0]["phone"] == "+44 7700 900777"


@pytest.mark.db
async def test_ghl_leg_applies_bar_4_when_the_db_leg_saw_no_candidates(
    async_session: AsyncSession,
) -> None:
    """Review round 6, P1: the subordination guard cannot engage on an empty set.

    `collision` is None whenever `candidates` is empty, so a sibling EXCLUDED
    from the candidate set is refused nothing and re-enters via the GHL leg.
    The exclusion is real and routine: franchise A's `create_location` succeeds
    but the response is lost, so A's row keeps `ghl_subaccount_id IS NULL` and
    `_SIBLING_SELECT` skips it.

    B then signs - same brand, same head-office Zip, shared `ops@`, same owner
    phone. Zero candidates, no collision, and the GHL search returns A's orphan
    carrying the name, postcode and phone WE SENT. Every corroborating bar
    clears. Only the street address differs, and until this fix the GHL leg
    could not see it, so B merged into A with no flag and no parent link.
    """
    b_id = await _seed_client(
        async_session,
        email="ops@brandgym.com",
        business_name="Brand Gym",
        legal_entity="Brand Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900111",
        address="Studio Two, 99 Kingsland Road",
    )
    event_id = await _seed_onboarding_event(async_session)
    orphan = GhlLocation(
        id="loc_a_orphan",
        name="Brand Gym",
        company_id=COMPANY_ID,
        raw={
            "id": "loc_a_orphan",
            "postalCode": "E8 1AA",
            "phone": "+44 7700 900111",
            "address": "Studio One, 1 Mare Street",  # A's site, not B's
        },
    )
    ghl = FakeGhlClient(
        location=_ghl_location("loc_b_own", name="Brand Gym", postal_code="E8 1AA"),
        lookup_result=orphan,
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=b_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True, "B must provision its OWN sub-account"
    assert result.ghl_subaccount_id == "loc_b_own"
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_ghl_id FROM clients WHERE id = :id"),
        {"id": b_id},
    )
    is_dup, dup_ghl_id = flagged.one()
    assert is_dup is True
    assert dup_ghl_id == "loc_a_orphan"


@pytest.mark.db
async def test_legacy_location_with_no_address_is_still_reused_when_corroborated(
    async_session: AsyncSession,
) -> None:
    """Bar 4's absence posture on the GHL leg: ABSTAIN, exactly as the DB leg.

    INVERTED in round 7. The round-6 version of this test asserted the
    opposite - "we hold an address the location cannot corroborate" vetoed the
    reuse - and that veto quietly doomed the ENTIRE legacy cohort: every
    pre-existing GHL location carries no address, so the reuse leg refused all
    of them behind a flag `_clear_possible_duplicate`'s own docstring says can
    never clear. Bar 4 is a DISQUALIFIER; it vetoes only on ACTIVE
    disagreement. A hit corroborated on name, postcode AND phone with no
    address to check is a corroborated hit.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@legacyaddr.com",
        business_name="Legacy Addr Gym",
        legal_entity="Legacy Addr Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900222",
        address="1 Mare Street",
    )
    event_id = await _seed_onboarding_event(async_session)
    legacy = GhlLocation(
        id="loc_legacy",
        name="Legacy Addr Gym",
        company_id=COMPANY_ID,
        raw={"id": "loc_legacy", "postalCode": "E8 1AA", "phone": "+44 7700 900222"},
    )
    ghl = FakeGhlClient(
        location=_ghl_location("loc_should_not_create"),
        lookup_result=legacy,
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is False, "the legacy cohort must remain reusable"
    assert result.ghl_subaccount_id == "loc_legacy"
    assert ghl.calls == []
    flagged = await async_session.execute(
        text("SELECT possible_duplicate FROM clients WHERE id = :id"), {"id": client_id}
    )
    assert flagged.scalar_one() is False


@pytest.mark.db
async def test_divergent_address_nested_under_business_still_vetoes(
    async_session: AsyncSession,
) -> None:
    """`_location_address` must read the `business`-nested shape too.

    Review round 7: the nested fallback had no test - the only nested-payload
    test nested `postalCode` and `phone`. The module's own comments cite a live
    21/07 response carrying a `business` object, so if GHL echoes `address`
    nested and the fallback is deleted, bar 4 on the GHL leg reads every real
    payload as address-absent and the veto never fires.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@nestedaddr.com",
        business_name="Nested Addr Gym",
        legal_entity="Nested Addr Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900333",
        address="Studio Two, 99 Kingsland Road",
    )
    event_id = await _seed_onboarding_event(async_session)
    nested = GhlLocation(
        id="loc_nested_addr",
        name="Nested Addr Gym",
        company_id=COMPANY_ID,
        raw={
            "id": "loc_nested_addr",
            "business": {
                "postalCode": "E8 1AA",
                "phone": "+44 7700 900333",
                "address": "Studio One, 1 Mare Street",  # ACTIVELY divergent
            },
        },
    )
    ghl = FakeGhlClient(
        location=_ghl_location("loc_own", name="Nested Addr Gym", postal_code="E8 1AA"),
        lookup_result=nested,
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True, "a divergent nested address must veto the reuse"
    assert result.ghl_subaccount_id == "loc_own"
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_ghl_id FROM clients WHERE id = :id"),
        {"id": client_id},
    )
    is_dup, dup_ghl = flagged.one()
    assert is_dup is True
    assert dup_ghl == "loc_nested_addr"


@pytest.mark.db
async def test_ghl_orphan_of_another_franchisee_is_not_reused(
    async_session: AsyncSession,
) -> None:
    """Review round 5, finding 5 - the GHL leg's own franchise hole.

    The sequence, which no state-based test could see:

    1. Franchise A's `create_location` succeeds but the response is LOST, so
       A's row keeps `ghl_subaccount_id IS NULL`.
    2. `_SIBLING_SELECT` requires a non-NULL sub-account id, so the DB leg
       cannot see A at all.
    3. B signs - same brand, same head-office postcode, shared `ops@` mailbox.
    4. `find_location_by_email` returns A's orphan, carrying the name and
       postcode WE SENT for A.

    Name + postcode agree, which is STRICTLY the bar round 2 rejected for the
    DB leg, so B reused A's location: two franchisees in one sub-account, no
    flag, no `parent_client_id` trace. The phone is on that payload too, and it
    is the one field that differs between the two studios.
    """
    b_id = await _seed_client(
        async_session,
        email="ops@brandgym.com",
        business_name="Brand Gym",
        legal_entity="Brand Gym",
        postal_code="E8 1AA",  # shared head office
        phone="+44 7700 900222",
    )
    event_id = await _seed_onboarding_event(async_session)
    # A's orphan: our own payload for A, so it agrees on name and postcode and
    # differs only on the phone.
    ghl = FakeGhlClient(
        location=_ghl_location("loc_b_own", name="Brand Gym", postal_code="E8 1AA"),
        lookup_result=_ghl_location(
            "loc_a_orphan",
            name="Brand Gym",
            postal_code="E8 1AA",
            phone="+44 7700 900111",  # franchisee A's line, not B's
        ),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=b_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True, "B must provision its OWN sub-account"
    assert result.ghl_subaccount_id == "loc_b_own"
    assert result.ghl_subaccount_id != "loc_a_orphan"

    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_ghl_id FROM clients WHERE id = :id"),
        {"id": b_id},
    )
    is_dup, dup_ghl_id = flagged.one()
    assert is_dup is True, "an uncorroborated GHL hit is undecidable, so a human sees it"
    assert dup_ghl_id == "loc_a_orphan", "the flag names the location a human would merge into"


@pytest.mark.db
async def test_keyed_client_falls_back_to_email_for_a_pre_0013_sibling(
    async_session: AsyncSession,
) -> None:
    """Review round 6, P1: the keyed path never fell back to email.

    `keyed` selected strictly one query, so a row written BEFORE migration 0013
    - which performs no backfill and says so ("pre-existing rows simply carry
    NULL identity_key and fall through to CREATE") - was permanently invisible
    to every later keyed signing. Detection became order-dependent: an
    unkeyed row first then a keyed one never linked, while the reverse linked
    cleanly. That is a regression against S1-26, whose predicate was
    `WHERE email = :email`.

    The prior row here is exactly that shape: a real returning client with a
    NULL identity_key, so the keyed query cannot see it.
    """
    legacy = await _seed_client(
        async_session,
        email="ops@returning.com",
        business_name="Returning Gym",
        legal_entity="Returning Gym",
        postal_code=None,  # pre-0013: no identity_key at all
        phone="+44 7700 900123",
        contact_first_name="Sam",
        contact_last_name="Taylor",
        ghl_subaccount_id="loc_legacy",
        created_at_offset_seconds=-60,
    )
    keyed_now = await _seed_client(
        async_session,
        email="ops@returning.com",
        business_name="Returning Gym",
        legal_entity="Returning Gym",
        postal_code="E8 1AA",  # this signing DOES key
        phone="+44 7700 900123",
        contact_first_name="Sam",
        contact_last_name="Taylor",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_unused"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=keyed_now,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is False, "the legacy sibling must be found via email"
    assert result.ghl_subaccount_id == "loc_legacy"
    assert result.parent_client_id == legacy


@pytest.mark.db
async def test_email_fallback_never_reconsiders_a_row_the_key_already_separated(
    async_session: AsyncSession,
) -> None:
    """The regression the round-6 P1 fix caused on its first attempt.

    "Brand Gym" Hackney (E8 1AA) and Croydon (CR0 1AA) are two franchises on one
    `ops@` mailbox. Their identity keys DIFFER, which is positive evidence they
    are different clients - the key is name + postcode, and the postcode says
    so. An UNRESTRICTED email fallback threw that evidence away: Croydon's keyed
    query found nothing, the fallback matched Hackney on email alone, bar 2
    refused it, and a CONFIDENT franchise separation became an ambiguous flag
    with the GHL lookup suppressed behind it.

    A keyed client therefore only falls back to rows with `identity_key IS NULL`
    - the pre-0013 population the keyed query structurally cannot see. Absence
    of a key is absence of evidence; a DIFFERENT key is evidence of difference,
    and the two must not be conflated.
    """
    await _seed_client(
        async_session,
        email="ops@brandgyms.com",
        business_name="Brand Gym",
        legal_entity="Brand Gym",
        postal_code="E8 1AA",  # keyed, and a DIFFERENT key from Croydon's
        ghl_subaccount_id="loc_hackney",
        created_at_offset_seconds=-60,
    )
    croydon = await _seed_client(
        async_session,
        email="ops@brandgyms.com",
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
        client_id=croydon,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.parent_client_id is None
    # THE assertions: no false flag, and the GHL leg was NOT suppressed.
    flagged = await async_session.execute(
        text("SELECT possible_duplicate FROM clients WHERE id = :id"), {"id": croydon}
    )
    assert flagged.scalar_one() is False, (
        "a postcode mismatch is a CONFIDENT different site, not an ambiguous one"
    )
    assert ghl.lookup_calls == [("ops@brandgyms.com", COMPANY_ID)], (
        "suppressing the GHL lookup here would hide the client's own real location"
    )


@pytest.mark.db
async def test_keyed_email_fallback_still_demands_the_contact_name(
    async_session: AsyncSession,
) -> None:
    """The fallback must carry the UNKEYED bar, not the keyed one.

    Its candidates share only a literal email, with no postcode anchoring
    them - exactly the condition bar 3 exists for. Two franchise sites on one
    `ops@` mailbox and one head-office number must still be told apart, or the
    round-6 fix would reopen round 2's finding 5 through a new door.
    """
    other = await _seed_client(
        async_session,
        email="ops@brandgym.com",
        business_name="Brand Gym",
        legal_entity="Brand Gym",
        postal_code=None,
        phone="+44 7700 900123",
        contact_first_name="Alice",
        contact_last_name="Hackney",
        ghl_subaccount_id="loc_hackney",
        created_at_offset_seconds=-60,
    )
    croydon = await _seed_client(
        async_session,
        email="ops@brandgym.com",
        business_name="Brand Gym",
        legal_entity="Brand Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900123",
        contact_first_name="Bob",  # a DIFFERENT signer - bar 3 refuses
        contact_last_name="Croydon",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_croydon", name="Brand Gym"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=croydon,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True, "different signer -> its own sub-account"
    assert result.parent_client_id is None
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_of FROM clients WHERE id = :id"),
        {"id": croydon},
    )
    is_dup, dup_of = flagged.one()
    assert is_dup is True
    assert dup_of == other


@pytest.mark.db
async def test_numeric_phone_and_postcode_from_json_still_corroborate(
    async_session: AsyncSession,
) -> None:
    """An unquoted number in the GHL payload must not read as "absent".

    JSON parses `"postalCode": 75008` and `"phone": 442070000000` as ints, and
    both `_location_postcode` and `_location_phone` coerce them. Untested, both
    coercions could be deleted with the suite still green - so a genuinely
    corroboratable INT location would be sent down the undecidable path and
    flagged for no reason, on every signing.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@intgym.com",
        business_name="Int Gym",
        legal_entity="Int Gym",
        postal_code="75008",
        phone="442070000000",
    )
    event_id = await _seed_onboarding_event(async_session)
    numeric = GhlLocation(
        id="loc_int",
        name="Int Gym",
        company_id=COMPANY_ID,
        raw={"id": "loc_int", "postalCode": 75008, "phone": 442070000000},
    )
    ghl = FakeGhlClient(location=_ghl_location("loc_unused"), lookup_result=numeric)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    # Corroborated on all three signals -> REUSED, not duplicated-and-flagged.
    assert result.created is False
    assert result.ghl_subaccount_id == "loc_int"
    flagged = await async_session.execute(
        text("SELECT possible_duplicate FROM clients WHERE id = :id"), {"id": client_id}
    )
    assert flagged.scalar_one() is False


@pytest.mark.db
async def test_phoneless_client_loses_the_at_least_once_backstop(
    async_session: AsyncSession,
) -> None:
    """The precondition the F5 phone bar adds to the backstop, pinned.

    A client with no `Client.Phone` gets a create payload with no phone, so our
    own lost-response orphan comes back carrying none either. Mutual absence is
    not agreement, so the verdict is UNDECIDABLE and the retry provisions a
    SECOND location instead of reusing the orphan - where before F5 it reused.

    This is the deliberate trade, not a regression to fix: those clients also
    fail DB bar 2 permanently, so treating mutual absence as corroboration
    would reuse a location on nothing but a shared brand, postcode and `ops@`
    mailbox - round 2's franchise conflation exactly. The cost here is one
    spare, FLAGGED, deletable sub-account; the cost the other way is one
    client's assets inside another's account.

    It gets a test so the narrowing is a stated contract rather than something
    rediscovered later from a duplicate-sub-account report.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@nophone.com",
        business_name="No Phone Gym",
        legal_entity="No Phone Gym",
        postal_code="E8 1AA",
        phone=None,  # THE precondition under test
    )
    event_id = await _seed_onboarding_event(async_session)
    # Our own orphan from a lost create: it carries exactly what we sent, which
    # for this client did NOT include a phone.
    ghl = FakeGhlClient(
        location=_ghl_location("loc_second", name="No Phone Gym", postal_code="E8 1AA"),
        lookup_result=_ghl_location(
            "loc_orphan", name="No Phone Gym", postal_code="E8 1AA", phone=None
        ),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    # NOT reused - a second location, and the orphan is named for a human.
    assert result.created is True
    assert result.ghl_subaccount_id == "loc_second"
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_ghl_id FROM clients WHERE id = :id"),
        {"id": client_id},
    )
    is_dup, dup_ghl_id = flagged.one()
    assert is_dup is True, "the spare must be visible, or the trade is not payable"
    assert dup_ghl_id == "loc_orphan"


@pytest.mark.db
async def test_ghl_hit_without_a_phone_is_undecidable_not_reused(
    async_session: AsyncSession,
) -> None:
    """Absence is not agreement on the GHL leg either.

    A legacy sub-account (created before we sent phone or postcode) that agrees
    on name and postcode but carries NO phone cannot corroborate. It provisions
    its own and flags, rather than being merged on the round-2 bar.
    """
    client_id = await _seed_client(
        async_session,
        email="ops@legacygym.com",
        business_name="Legacy Gym",
        legal_entity="Legacy Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900333",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(
        location=_ghl_location("loc_own", name="Legacy Gym", postal_code="E8 1AA"),
        lookup_result=_ghl_location(
            "loc_legacy", name="Legacy Gym", postal_code="E8 1AA", phone=None
        ),
    )

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=client_id,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is True
    assert result.ghl_subaccount_id == "loc_own"
    flagged = await async_session.execute(
        text("SELECT possible_duplicate, possible_duplicate_ghl_id FROM clients WHERE id = :id"),
        {"id": client_id},
    )
    is_dup, dup_ghl_id = flagged.one()
    assert is_dup is True
    assert dup_ghl_id == "loc_legacy"


@pytest.mark.db
async def test_franchisee_two_studios_same_phone_is_separated_by_address(
    async_session: AsyncSession,
) -> None:
    """Review round 5, finding 1 - the case the test above could NOT see.

    `test_franchisee_two_studios_one_head_office_is_not_auto_linked` seeds two
    DIFFERENT phone numbers, so what it demonstrates is bar 2 (phone) doing its
    job - not franchise logic. Equalize the phones, as one owner signing both
    of their own studios personally actually would, and bars 1, 2 and 3 all
    clear: same brand, same head-office postcode, same contact, same number.
    Before the address bar, studio two linked into studio one's sub-account
    with NO flag, and `_clear_possible_duplicate` even fired.

    The street addresses are the only thing on these two rows that differs -
    which is what this test proves, and ALL it proves. See
    `test_one_company_record_two_deals_still_auto_links` directly below for the
    case this does NOT close, and why the docstrings no longer claim it does
    (review round 6).
    """
    shared_phone = "+44 7700 900111"
    studio_one = await _seed_client(
        async_session,
        email="owner@f45franchise.com",
        business_name="F45 Training",
        legal_entity="F45 Training",
        postal_code="E8 1AA",  # head office, not the studio
        phone=shared_phone,
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
        phone=shared_phone,  # the SAME owner, signing personally, both times
        address="Studio Two, 99 Kingsland Road",
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
async def test_one_company_record_two_deals_still_auto_links(
    async_session: AsyncSession,
) -> None:
    """THE KNOWN GAP, pinned so nobody mistakes bar 4 for closing it.

    Review round 6 was right that the round-5 claim overreached.
    `Company.Address` and `Company.Zip` are read from the SAME HubSpot company
    record, so one owner with ONE company record and TWO deals produces two rows
    byte-identical on name, postcode, phone AND address. Every bar clears and
    site 2 auto-links with no flag.

    This test asserts that outcome deliberately - it is the current, documented
    behaviour, not an aspiration. Closing it needs `hubspot_company_id`, which
    Bullet's documents do not currently carry (verified 28/07: `linked_objects`
    holds the deal only), so it is blocked on the client asks rather than on
    code. If a later change closes the gap, this test SHOULD fail - and that
    failure is the signal to update it, not to weaken the new guard.
    """
    site_one = await _seed_client(
        async_session,
        email="owner@onecompany.com",
        business_name="One Company Gym",
        legal_entity="One Company Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900123",
        address="1 Mare Street",  # the COMPANY address, identical on both deals
        ghl_subaccount_id="loc_site_one",
        created_at_offset_seconds=-60,
    )
    site_two = await _seed_client(
        async_session,
        email="owner@onecompany.com",
        business_name="One Company Gym",
        legal_entity="One Company Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900123",
        address="1 Mare Street",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_unused"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=site_two,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    # Documented gap: indistinguishable from a genuine returning client on
    # every signal we currently hold, so it links.
    assert result.created is False
    assert result.parent_client_id == site_one


@pytest.mark.db
async def test_returning_client_same_address_still_links(
    async_session: AsyncSession,
) -> None:
    """The address bar must REFUSE without also breaking the genuine case.

    One business re-signing carries the same address, so bar 4 abstains and the
    link proceeds. Without this, a fix for finding 1 that simply refused every
    address comparison would pass the separation test above and silently turn
    every returning client into a duplicate.
    """
    existing = await _seed_client(
        async_session,
        email="ops@onegym.com",
        business_name="One Gym",
        legal_entity="One Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900321",
        address="1 Mare Street",
        ghl_subaccount_id="loc_existing",
        created_at_offset_seconds=-60,
    )
    returning = await _seed_client(
        async_session,
        email="newbilling@onegym.com",  # a different mailbox, same business
        business_name="One Gym",
        legal_entity="One Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900321",
        address="1 Mare Street",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_unused"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=returning,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is False
    assert result.ghl_subaccount_id == "loc_existing"
    assert result.parent_client_id == existing


@pytest.mark.db
async def test_returning_client_links_when_address_absent_on_one_side(
    async_session: AsyncSession,
) -> None:
    """Absence ABSTAINS on bar 4 - the opposite posture to the corroborating
    bars, and deliberately so (see `addresses_materially_diverge`).

    A corroborator that is missing must fail closed, or a missing signal reads
    as a match. A DISQUALIFIER that is missing must abstain, or it vetoes links
    it has no evidence against - which for the ~100 legacy rows carrying no
    address at all would mean refusing every genuine returning client.
    """
    existing = await _seed_client(
        async_session,
        email="ops@twogym.com",
        business_name="Two Gym",
        legal_entity="Two Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900654",
        address=None,  # legacy row, never captured an address
        ghl_subaccount_id="loc_existing",
        created_at_offset_seconds=-60,
    )
    returning = await _seed_client(
        async_session,
        email="ops@twogym.com",
        business_name="Two Gym",
        legal_entity="Two Gym",
        postal_code="E8 1AA",
        phone="+44 7700 900654",
        address="14 Dalston Lane",
    )
    event_id = await _seed_onboarding_event(async_session)
    ghl = FakeGhlClient(location=_ghl_location("loc_unused"), lookup_result=None)

    result = await create_ghl_subaccount_core(
        async_session,
        ghl,
        client_id=returning,
        onboarding_event_id=event_id,
        company_id=COMPANY_ID,
    )

    assert result.created is False
    assert result.ghl_subaccount_id == "loc_existing"
    assert result.parent_client_id == existing


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
    # Assert the flag FIRES before asserting what it implies. Gating the
    # assertions behind `if flagged.scalar_one():` (review round 5) made this
    # test pass silently in precisely the case it exists to catch: a row that
    # got merged WITHOUT being flagged skipped the body entirely and reported
    # green, so the dashboard sentence could go false without a failure.
    assert flagged.scalar_one() is True, (
        "this fixture must produce a flagged row - same name and postcode, "
        "divergent phone - or the contract below is never actually exercised"
    )
    assert result.created is True, "flagged rows must be provisioned their OWN sub-account"
    assert result.parent_client_id is None, "a flagged row must not be linked to a parent"
