"""S1-25: GoHighLevel sub-account creation from `client.created`.

`create_ghl_subaccount` is the first onboarding fan-out. Triggered by
`client.created` (emitted by the S1-25a orchestrator once a `clients` row
exists), it creates the client's GHL sub-account directly against the
agency API (retiring the old Pabbly middleman, per the 21/04/2026
decision), records the attempt in `platform_actions`, and writes the new
`ghl_subaccount_id` back onto the `clients` row.

This function does NOT create the client - S1-25a owns that. The clients
row + `client_id` are guaranteed present when this runs.

Correctness rules:

- **Idempotent via `platform_actions.idempotency_key`.** The key
  `{client_id}:ghl:create_subaccount:{onboarding_event_id}` is stable per
  client (there is exactly one `pandadoc.signed` onboarding_events row per
  document, so exactly one `client.created` event id per client). A
  replay re-derives the same key, the begin INSERT is a no-op, and an
  already-succeeded action short-circuits without a second GHL POST.
- **Already-provisioned short-circuit.** If `clients.ghl_subaccount_id` is
  already set (a returning client whose row pre-existed, or a prior
  success), the function records the action as `success` against the
  existing id and returns without calling GHL - never double-create a
  location.
- **Returning-client check (S1-26 / S1-26c).** Before provisioning, the
  function looks for an existing sub-account two ways and reuses it instead
  of creating a duplicate: (1) a prior `clients` row with the same IDENTITY
  KEY (S1-26c: `first6(normalized business name) + "|" + normalized postcode`)
  that already holds a `ghl_subaccount_id` - the new row is linked via
  `parent_client_id` to the original root and the id is reused; this now
  unites the same business signing under DIFFERENT emails and separates
  franchises (same brand, different postcode) that email-keying conflated;
  (2) no DB sibling, so a live GHL lookup-by-email runs on EVERY create
  attempt before the POST - this catches a client that exists in GHL but not
  our DB AND closes S1-25's at-least-once duplicate-create window (a retry
  after a lost create response finds the orphaned location rather than
  creating a second one). Either reuse is recorded as a `success` action
  carrying a `response.skipped_existing` + `response.reason` marker (no new
  enum value; see plan). Dedup guard (S1-26c): the key truncates the name to
  6 chars, so two DIFFERENT businesses sharing a name prefix AND a postcode
  collide on it; when the key matches but the FULL normalized names diverge,
  the row is flagged `possible_duplicate` (+ `possible_duplicate_of`) and
  provisioned as its OWN client rather than auto-merged. A NULL identity_key
  (no usable name/postcode) matches nothing and falls through to CREATE
  (fail-safe). A per-identity concurrency cap serialises same-identity
  processing so two returning signings cannot both pass the check
  concurrently.
- **Commit the `in_progress` row BEFORE the GHL call**, then commit the
  terminal (`success`/`failed`) state after. A crash mid-call therefore
  leaves a visible `in_progress` row in the dashboard rather than a silent
  gap - partial failures must be visible, never silent.
- **Concurrency.** Two guards (Inngest's per-function max): a per-client cap
  of 1 eliminates a concurrent double-create for the same client, and a
  per-identity cap of 1 (`event.data.dedup_key` = identity_key when present,
  else the unique client_id) serialises rows for the same business so two
  returning signings (even under different emails) cannot both pass the
  returning-client check concurrently. The former global in-flight cap was
  dropped (S1-26a) - it made THREE constraints, exceeding Inngest's 2-per-
  function limit, which failed the whole `/fn/register` sync (verified live:
  the sync 400s with 3, returns 200 with 2). The DB idempotency key +
  returning-client check protect duplicate-create correctness without it. A
  keyless `throttle=` (rate-over-time, a SEPARATE param that does not re-trip
  the 2-constraint limit) bounds the aggregate GHL START-rate across all
  clients (e.g. a `reconcile_pandadoc` multi-signing heal) that the per-key
  caps cannot. Note it bounds starts, NOT in-flight calls - under sustained
  GHL slowness overlapping runs can still exceed the old in-flight cap of 3,
  so the throttle is a weaker politeness bound, not a restoration of the
  dropped cap (see the decorator comment for the exact GCRA semantics).
- **Retriable vs not.** `GhlClientError` (4xx - bad payload / auth) and an
  empty API key / company id are NonRetriable (cannot self-heal), so
  Inngest dead-letters. `GhlServerError` (5xx / 429) and transient errors
  propagate so Inngest retries.

The function exposes a pure testable core `create_ghl_subaccount_core`
(session + ghl_client + ids) plus a thin Inngest-bound wrapper, matching
the `create_client_record` split.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

import inngest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.config import get_settings
from bullet_api.db.session import AsyncSessionLocal
from bullet_api.ghl.client import GhlClient, GhlClientError, HttpGhlClient
from bullet_api.worker._inngest import inngest_client
from bullet_api.worker.events import CLIENT_CREATED_EVENT
from bullet_api.worker.identity_key import names_materially_diverge
from bullet_api.worker.platform_actions import (
    begin_action,
    build_idempotency_key,
    complete_action,
    fail_action,
)

log = logging.getLogger(__name__)

GHL_PLATFORM = "ghl"
GHL_CREATE_SUBACCOUNT_ACTION = "create_subaccount"

# S1-26 returning-client reuse reasons, recorded on the `success` action's
# `response.reason` marker. There is no `skipped_existing` value in the
# `platform_action_status` enum (and we deliberately do not add one - see the
# S1-26 plan); a reuse is a terminal success against the reused id, marked by
# `response.skipped_existing` + `response.reason` for auditing.
GHL_SKIP_REASON_DB_SIBLING = "db_sibling"
GHL_SKIP_REASON_GHL_LOOKUP = "ghl_lookup"


class ClientNotFoundError(LookupError):
    """The `client.created` event references a `clients` row that does not exist.

    A data-consistency bug: the orchestrator commits the client row before
    emitting `client.created`, so a missing row means the event id is wrong
    or the row was deleted. Cannot self-heal on retry, so the Inngest
    wrapper translates this into `inngest.NonRetriableError`.
    """

    def __init__(self, client_id: uuid.UUID) -> None:
        self.client_id = client_id
        super().__init__(f"clients row {client_id} not found; cannot create GHL sub-account.")


@dataclass(frozen=True)
class CreateSubaccountResult:
    """Outcome of one `create_ghl_subaccount_core` run.

    `ghl_subaccount_id` is the surviving sub-account id. `created` is True
    only when a fresh GHL location was provisioned on this run. `skipped`
    is True when no fresh location was created - either the client already
    had a sub-account, the action had already succeeded, or S1-26's
    returning-client check reused an existing sub-account (DB sibling or
    live GHL lookup). `parent_client_id` is set only when a DB sibling was
    found and this row was linked to the original client's root id.
    """

    ghl_subaccount_id: str | None
    created: bool
    skipped: bool
    parent_client_id: uuid.UUID | None = None


def _build_location_payload(
    *,
    company_id: str,
    snapshot_id: str,
    business_name: str | None,
    legal_entity: str,
    contact_first_name: str | None,
    contact_last_name: str | None,
    email: str,
    phone: str | None,
) -> dict:
    """Build the GHL create-location body from the client row.

    `name` prefers the trading `business_name` and falls back to the
    NOT-NULL `legal_entity`. Optional keys are omitted entirely when their
    source value is missing so we never send empty strings GHL might
    reject. `snapshotId` is included only when a snapshot is configured.
    """
    payload: dict = {
        "name": business_name or legal_entity,
        "companyId": company_id,
    }
    if phone:
        payload["phone"] = phone

    prospect: dict = {}
    if contact_first_name:
        prospect["firstName"] = contact_first_name
    if contact_last_name:
        prospect["lastName"] = contact_last_name
    if email:
        prospect["email"] = email
    if prospect:
        payload["prospectInfo"] = prospect

    if snapshot_id:
        payload["snapshotId"] = snapshot_id

    return payload


async def create_ghl_subaccount_core(
    session: AsyncSession,
    ghl_client: GhlClient,
    *,
    client_id: uuid.UUID,
    onboarding_event_id: uuid.UUID | None,
    company_id: str,
    snapshot_id: str = "",
    inngest_run_id: str | None = None,
) -> CreateSubaccountResult:
    """Create (or resume / reuse / skip) the GHL sub-account for one client.

    Steps: load the client; short-circuit if already provisioned; reuse a
    DB sibling's sub-account if this is a returning client (link
    `parent_client_id`); else record an `in_progress` action + COMMIT, look
    GHL up by email, reuse if found, else POST create; on success record
    `success` + write `ghl_subaccount_id` back + COMMIT, or on error record
    `failed` + COMMIT and re-raise.

    Raises:
        ClientNotFoundError: no `clients` row for `client_id`. Rolls back.
        Exception: any failure of the GHL lookup OR create-location call
            (GhlClientError 4xx, GhlServerError 5xx/429, an httpx timeout /
            transport error, or an empty-config RuntimeError) is recorded as
            a `failed` action and committed, then re-raised unchanged so the
            wrapper can decide retriable vs not.
    """
    client = (
        await session.execute(
            text(
                "SELECT email, business_name, legal_entity, "
                "       contact_first_name, contact_last_name, phone, "
                "       ghl_subaccount_id, identity_key "
                "FROM clients WHERE id = :client_id"
            ),
            {"client_id": client_id},
        )
    ).one_or_none()
    if client is None:
        raise ClientNotFoundError(client_id)

    idempotency_key = build_idempotency_key(
        client_id, GHL_PLATFORM, GHL_CREATE_SUBACCOUNT_ACTION, onboarding_event_id
    )

    # Already provisioned: record the action as success against the existing
    # id and return. Never POST a second location for a client that has one.
    if client.ghl_subaccount_id:
        begun = await begin_action(
            session,
            client_id=client_id,
            event_id=onboarding_event_id,
            platform=GHL_PLATFORM,
            action=GHL_CREATE_SUBACCOUNT_ACTION,
            idempotency_key=idempotency_key,
            payload=None,
            inngest_run_id=inngest_run_id,
        )
        if not begun.already_succeeded:
            await complete_action(
                session,
                action_id=begun.action_id,
                external_id=client.ghl_subaccount_id,
                response={"skipped": "client already has ghl_subaccount_id"},
            )
        await session.commit()
        log.info(
            "S1-25 GHL sub-account skipped (client already provisioned)",
            extra={
                "client_id": str(client_id),
                "ghl_subaccount_id": client.ghl_subaccount_id,
            },
        )
        return CreateSubaccountResult(
            ghl_subaccount_id=client.ghl_subaccount_id, created=False, skipped=True
        )

    # S1-26 returning-client check (1/2): local DB sibling. If a PRIOR
    # clients row with the same IDENTITY KEY (S1-26c: first6(normalized name)
    # + postcode) already holds a ghl_subaccount_id, this is a returning
    # client (e.g. signing for a second gym/site, or the same business under a
    # different email). Link this row to the original ROOT
    # (`COALESCE(parent_client_id, id)`, which keeps a flat two-level tree
    # rather than chains) and reuse the existing sub-account id - never
    # provision a second location for the same client. Earliest row wins so
    # the root is stable across multiple returning signings.
    #
    # A NULL identity_key (no usable name/postcode) matches nothing and skips
    # the branch entirely - fail-safe to CREATE rather than mis-merge.
    sibling = None
    if client.identity_key is not None:
        sibling = (
            await session.execute(
                text(
                    "SELECT id, COALESCE(parent_client_id, id) AS root_id, "
                    "       ghl_subaccount_id, business_name "
                    "FROM clients "
                    "WHERE identity_key = :identity_key AND id <> :client_id "
                    "      AND ghl_subaccount_id IS NOT NULL "
                    "ORDER BY created_at ASC "
                    "LIMIT 1"
                ),
                {"identity_key": client.identity_key, "client_id": client_id},
            )
        ).one_or_none()

    # S1-26c dedup guard: the identity key truncates the name to 6 chars, so
    # two DIFFERENT businesses that share a name prefix AND a postcode collide
    # on it (e.g. "Fitness First" vs "Fitness Studio" at the same postcode).
    # When the key matches but the FULL normalized names diverge, do NOT merge
    # - flag a possible duplicate for a human and provision this signing as its
    # own client (never auto-merge; merges are hard to undo).
    if sibling is not None and names_materially_diverge(
        client.business_name, sibling.business_name
    ):
        await session.execute(
            text(
                "UPDATE clients "
                "SET possible_duplicate = true, possible_duplicate_of = :sibling_id "
                "WHERE id = :client_id"
            ),
            {"sibling_id": sibling.id, "client_id": client_id},
        )
        log.warning(
            "S1-26c possible duplicate: identity_key collision with divergent names",
            extra={
                "client_id": str(client_id),
                "sibling_client_id": str(sibling.id),
                "identity_key": client.identity_key,
            },
        )
        sibling = None  # fall through to normal provisioning (own root)

    if sibling is not None:
        begun = await begin_action(
            session,
            client_id=client_id,
            event_id=onboarding_event_id,
            platform=GHL_PLATFORM,
            action=GHL_CREATE_SUBACCOUNT_ACTION,
            idempotency_key=idempotency_key,
            payload=None,
            inngest_run_id=inngest_run_id,
        )
        if not begun.already_succeeded:
            await complete_action(
                session,
                action_id=begun.action_id,
                external_id=sibling.ghl_subaccount_id,
                response={
                    "skipped_existing": True,
                    "reason": GHL_SKIP_REASON_DB_SIBLING,
                    "ghl_subaccount_id": sibling.ghl_subaccount_id,
                    "parent_client_id": str(sibling.root_id),
                    "sibling_client_id": str(sibling.id),
                },
            )
            # Link the new row to the original root and reuse the sub-account
            # id. Guard with `ghl_subaccount_id IS NULL` so a concurrent
            # writer is never clobbered.
            await session.execute(
                text(
                    "UPDATE clients "
                    "SET parent_client_id = :root_id, ghl_subaccount_id = :ghl_id "
                    "WHERE id = :client_id AND ghl_subaccount_id IS NULL"
                ),
                {
                    "root_id": sibling.root_id,
                    "ghl_id": sibling.ghl_subaccount_id,
                    "client_id": client_id,
                },
            )
        await session.commit()
        log.info(
            "S1-26 GHL sub-account reused (returning client, DB sibling)",
            extra={
                "client_id": str(client_id),
                "ghl_subaccount_id": sibling.ghl_subaccount_id,
                "parent_client_id": str(sibling.root_id),
            },
        )
        return CreateSubaccountResult(
            ghl_subaccount_id=sibling.ghl_subaccount_id,
            created=False,
            skipped=True,
            parent_client_id=sibling.root_id,
        )

    payload = _build_location_payload(
        company_id=company_id,
        snapshot_id=snapshot_id,
        business_name=client.business_name,
        legal_entity=client.legal_entity,
        contact_first_name=client.contact_first_name,
        contact_last_name=client.contact_last_name,
        email=client.email,
        phone=client.phone,
    )

    begun = await begin_action(
        session,
        client_id=client_id,
        event_id=onboarding_event_id,
        platform=GHL_PLATFORM,
        action=GHL_CREATE_SUBACCOUNT_ACTION,
        idempotency_key=idempotency_key,
        payload=payload,
        inngest_run_id=inngest_run_id,
    )
    # COMMIT the in_progress row before the external calls so a crash
    # mid-lookup / mid-POST leaves a visible row rather than a silent gap.
    await session.commit()

    # A replay whose action already succeeded short-circuits without a
    # second GHL call. (The already-provisioned check above normally
    # catches this first; this guards the torn-state edge.)
    if begun.already_succeeded:
        return CreateSubaccountResult(
            ghl_subaccount_id=client.ghl_subaccount_id, created=False, skipped=True
        )

    async def _record_failure(exc: Exception) -> None:
        # Record ANY failure of the lookup OR create call as `failed`, then
        # the caller re-raises unchanged. Catching only GhlClientError/
        # GhlServerError would let a transport-level error (an httpx timeout /
        # connection reset, which carries no HTTP status) bypass fail_action
        # and leave the row stuck `in_progress`; a response-lost read timeout
        # is also the worst case of the at-least-once create window, so it
        # must be visible. The wrapper still decides retriable-vs-not from the
        # re-raised exception type.
        await fail_action(session, action_id=begun.action_id, last_error=str(exc))
        await session.commit()
        log.warning(
            "S1-25 GHL sub-account creation failed",
            extra={
                "client_id": str(client_id),
                "action_id": str(begun.action_id),
                "error": str(exc),
            },
        )

    # S1-26 returning-client check (2/2): no DB sibling, so look GHL up
    # directly by email BEFORE POSTing. This catches a client that exists in
    # GHL but not in our DB, and - critically - closes S1-25's at-least-once
    # duplicate-create window: a retry after a lost create response finds the
    # orphaned location here instead of creating a second one. The lookup
    # runs on EVERY create attempt (rider a).
    try:
        existing = await ghl_client.find_location_by_email(client.email, company_id=company_id)
    except Exception as exc:
        await _record_failure(exc)
        raise

    if existing is not None:
        await complete_action(
            session,
            action_id=begun.action_id,
            external_id=existing.id,
            response={
                "skipped_existing": True,
                "reason": GHL_SKIP_REASON_GHL_LOOKUP,
                "ghl_subaccount_id": existing.id,
            },
        )
        await session.execute(
            text(
                "UPDATE clients SET ghl_subaccount_id = :ghl_id "
                "WHERE id = :client_id AND ghl_subaccount_id IS NULL"
            ),
            {"ghl_id": existing.id, "client_id": client_id},
        )
        await session.commit()
        log.info(
            "S1-26 GHL sub-account reused (returning client, GHL lookup)",
            extra={
                "client_id": str(client_id),
                "ghl_subaccount_id": existing.id,
                "action_id": str(begun.action_id),
            },
        )
        return CreateSubaccountResult(ghl_subaccount_id=existing.id, created=False, skipped=True)

    try:
        location = await ghl_client.create_location(payload)
    except Exception as exc:
        await _record_failure(exc)
        raise

    await complete_action(
        session,
        action_id=begun.action_id,
        external_id=location.id,
        response=location.raw,
    )
    # Guard with `ghl_subaccount_id IS NULL` so a concurrent writer cannot
    # be clobbered; the per-client concurrency cap makes that race
    # near-impossible, but the guard is cheap insurance.
    await session.execute(
        text(
            "UPDATE clients SET ghl_subaccount_id = :ghl_id "
            "WHERE id = :client_id AND ghl_subaccount_id IS NULL"
        ),
        {"ghl_id": location.id, "client_id": client_id},
    )
    await session.commit()

    log.info(
        "S1-25 GHL sub-account created",
        extra={
            "client_id": str(client_id),
            "ghl_subaccount_id": location.id,
            "action_id": str(begun.action_id),
        },
    )
    return CreateSubaccountResult(ghl_subaccount_id=location.id, created=True, skipped=False)


@inngest_client.create_function(
    fn_id="create-ghl-subaccount",
    trigger=inngest.TriggerEvent(event=CLIENT_CREATED_EVENT),
    # Global GHL-politeness rate limit (S1-26a follow-up). Bounds the START rate
    # across ALL clients, which the two per-key concurrency caps below cannot: a
    # `reconcile_pandadoc` nightly pass can heal several dropped signings at once,
    # each fanning out to a DISTINCT `client.created` (distinct client_id + email),
    # so neither `limit=1` keyed cap bounds that aggregate burst - without this the
    # heal would fire N simultaneous GHL creates. `throttle=` is rate-over-time and
    # is a SEPARATE Inngest param, so it does NOT count against the 2-constraint
    # concurrency limit that the S1-26a bug tripped.
    #
    # Semantics, precisely: Inngest throttle is GCRA over run STARTS - at most
    # `limit + burst` starts per `period` window (burst is the SDK default 1, so
    # up to 6 starts can land in a single 10s window; the sustained rate is
    # 5/10s). It does NOT bound in-flight calls: under sustained GHL slowness,
    # runs started across successive windows can overlap, so concurrency can
    # still exceed the old in-flight cap of 3 - this is a start-rate politeness
    # bound, weaker than (not a restoration of) the dropped Concurrency cap.
    # Keyless so it caps the whole function, not per-client.
    throttle=inngest.Throttle(limit=5, period=timedelta(seconds=10)),
    concurrency=[
        # Inngest allows a MAX of 2 concurrency constraints per function, and
        # exceeding it fails the WHOLE (all-or-nothing) function registration, so
        # NO function registers. We therefore keep only the two correctness
        # guards below and drop the former global `Concurrency(limit=3)` GHL-
        # politeness cap (S1-26a) - a THIRD constraint. That cap bounded
        # simultaneous IN-FLIGHT runs; the `throttle=` above approximates its
        # politeness intent as a start-rate bound only (see its comment - it is
        # weaker, not a restoration), and the DB idempotency key
        # (`platform_actions` ON CONFLICT) + the S1-26 returning-client check
        # protect against duplicate accounts regardless.
        #
        # Per-client cap: at most one creation in flight for a given client,
        # so two concurrent `client.created` deliveries cannot both POST a
        # location before either commits its `ghl_subaccount_id`.
        inngest.Concurrency(key="event.data.client_id", limit=1, scope="fn"),
        # Per-identity cap (S1-26c): serialise processing for the same identity
        # key so two DIFFERENT client rows for the same business (two
        # near-simultaneous returning signings, possibly under different emails)
        # cannot both pass the returning-client check and create duplicate
        # locations - the second waits, then finds the first as a committed DB
        # sibling. `dedup_key` (event payload) = the identity_key when present,
        # else the unique client_id, so unidentifiable signings (NULL
        # identity_key) each get their own bucket instead of contending on a
        # shared empty key. The identity_key is already normalized (lowercased,
        # punctuation-stripped), so this also closes the old S1-26 residual
        # where the raw-string email key failed to serialise case-only email
        # differences.
        inngest.Concurrency(key="event.data.dedup_key", limit=1, scope="fn"),
    ],
)
async def create_ghl_subaccount(ctx: inngest.Context) -> dict:
    """Inngest wrapper: build production deps, run the core, translate
    NonRetriable failures.

    Raises:
        inngest.NonRetriableError: a structural error (missing client row,
            GHL 4xx, empty API key / company id). Inngest dead-letters.
        GhlServerError / transient errors: propagate so Inngest retries.
    """
    client_id = uuid.UUID(ctx.event.data["client_id"])
    raw_event_id = ctx.event.data.get("onboarding_event_id")
    onboarding_event_id = uuid.UUID(raw_event_id) if raw_event_id else None

    settings = get_settings()
    if not settings.ghl_company_id:
        raise inngest.NonRetriableError(
            "GHL_COMPANY_ID is empty; cannot create sub-account. Set it on the Render env group."
        )

    ghl_client = HttpGhlClient(
        api_key=settings.ghl_agency_api_key,
        base_url=settings.ghl_api_base_url,
        version=settings.ghl_api_version,
    )

    async with AsyncSessionLocal() as session:
        try:
            result = await create_ghl_subaccount_core(
                session,
                ghl_client,
                client_id=client_id,
                onboarding_event_id=onboarding_event_id,
                company_id=settings.ghl_company_id,
                snapshot_id=settings.ghl_snapshot_id,
                inngest_run_id=ctx.run_id,
            )
        except (ClientNotFoundError, GhlClientError) as exc:
            raise inngest.NonRetriableError(str(exc)) from exc
        except RuntimeError as exc:
            # HttpGhlClient raises RuntimeError when the API key is empty.
            raise inngest.NonRetriableError(str(exc)) from exc

    return {
        "client_id": str(client_id),
        "ghl_subaccount_id": result.ghl_subaccount_id,
        "created": result.created,
        "skipped": result.skipped,
        "parent_client_id": (
            str(result.parent_client_id) if result.parent_client_id is not None else None
        ),
    }


__all__ = [
    "GHL_CREATE_SUBACCOUNT_ACTION",
    "GHL_PLATFORM",
    "GHL_SKIP_REASON_DB_SIBLING",
    "GHL_SKIP_REASON_GHL_LOOKUP",
    "ClientNotFoundError",
    "CreateSubaccountResult",
    "create_ghl_subaccount",
    "create_ghl_subaccount_core",
]
