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
  `parent_client_id` to the original root and the id is reused; this unites
  the same business signing under DIFFERENT emails and separates franchises
  (same brand, different postcode) that email-keying conflated. When the
  identity key is NULL (no usable name or postcode) the sibling query falls
  back to EMAIL, S1-26's original key, so those documents keep a DB-side dedup
  path instead of losing it entirely. (2) no DB sibling, so a live GHL
  lookup-by-email runs on EVERY create attempt before the POST - this catches
  a client that exists in GHL but not our DB. It does NOT close S1-25's
  at-least-once duplicate-create window, despite the original design saying so:
  the search is eventually consistent (S1-26d), so a create whose response was
  lost and is retried seconds later cannot see its own orphan and mints a second
  location. That window is OPEN and owned by S1-26d. Either reuse is
  recorded as a `success` action carrying a `response.skipped_existing` +
  `response.reason` marker (no new enum value; see plan).

  **Corroboration, both ways (S1-26c review fix).** Neither check reuses on
  its lookup key alone, because both keys are lossy: the identity key
  truncates the name to 6 chars (so "Fitness First" and "Fitness Studio" at
  one postcode collide), and email is shared across a brand's franchises (so
  "Brand Gym" Hackney and Croydon collide). A DB sibling must clear FOUR bars:
  the FULL normalized names agree - and every candidate at the key is scanned
  for that match, not just the earliest, so an unrelated business reaching the
  key first cannot hide a genuine returning client behind it - AND the PHONE
  agrees. The second bar exists because `Company.Zip` is the COMPANY postcode
  rather than the studio's, so a franchisee running two studios who enters only
  the brand ("F45 Training") plus their head-office postcode produces an
  identical key AND identical names; without it, studio 2 is silently linked
  into studio 1's sub-account. Bar 3 (the SIGNING CONTACT's name) applies on
  the unkeyed email-fallback path only, which has no postcode to anchor it.
  Bar 4 is the ADDRESS, and it reads in ONE DIRECTION ONLY: it can refuse a
  link, never grant one. Address cannot CORROBORATE, because it comes from the
  same HubSpot company record as the postcode and so agrees exactly when the
  key does - but a DIFFERING address is still the only thing that separates one
  owner's two sites when brand, head-office postcode and phone are all
  identical (review round 5, finding 1). Absence is NOT agreement on the
  corroborating bars; on bar 4 absence ABSTAINS. This NARROWS the franchisee
  case; it does not eliminate it, and no docstring here should claim
  otherwise.
  A GHL hit is accepted only when the location's name, postcode AND PHONE all
  agree, which is why `postalCode` and `phone` are both sent on create: it
  makes our own locations self-identifying, so a later signing can prove a hit
  is the same site. Name + postcode alone is exactly the bar round 2 rejected
  for the DB leg, and the GHL leg carried it until round 5, finding 5.
  Anything short of full corroboration is NOT merged - the signing is
  provisioned its OWN sub-account. That direction is deliberate: a spare empty
  sub-account is visible in the dashboard and deletes cleanly, whereas a wrong
  reuse puts one client's assets, contacts and workflows inside another
  client's account and is not realistically undoable.

  **Flagging is for ambiguity, not for every non-reuse.** `possible_duplicate`
  (+ `possible_duplicate_of` for a client collision, `possible_duplicate_ghl_id`
  for a location one) is raised only when the evidence is INCONCLUSIVE - an
  identity-key collision whose names diverge, or a GHL hit we cannot decide
  because a postcode is missing on either side. A confident negative (the
  postcode is present and different: a franchise at another site) is the normal
  path and raises nothing, because a flag that fires on every franchise signing
  is a flag the team stops reading. A per-identity concurrency cap serialises
  same-identity processing so two returning signings cannot both pass the check
  concurrently.
- **Commit the `in_progress` row BEFORE the GHL call**, then commit the
  terminal (`success`/`failed`) state after. A crash mid-call therefore
  leaves a visible `in_progress` row in the dashboard rather than a silent
  gap - partial failures must be visible, never silent.
- **Concurrency.** Two guards (Inngest's per-function max): a per-client cap
  of 1 eliminates a concurrent double-create for the same client, and a
  per-identity cap of 1 (`event.data.dedup_key` = identity_key when present,
  else `email:<normalized>`, else the unique client_id) serialises rows for
  the same business so two returning signings (even under different emails)
  cannot both pass the returning-client check concurrently. The email tier
  matters because a NULL identity_key still has a DB-side dedup path, so those
  rows must contend with each other rather than each taking a private bucket.
  The former global in-flight cap was
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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

import inngest
from sqlalchemy import Row, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.config import get_settings
from bullet_api.db.session import AsyncSessionLocal
from bullet_api.ghl.client import (
    GhlClient,
    GhlClientError,
    GhlLocation,
    GhlNotConfiguredError,
    HttpGhlClient,
)
from bullet_api.worker._inngest import inngest_client
from bullet_api.worker.events import CLIENT_CREATED_EVENT
from bullet_api.worker.identity_key import (
    addresses_materially_diverge,
    contact_name_agrees,
    corroborating_signal_agrees,
    identity_name,
    names_materially_diverge,
    normalize_name,
    normalize_postcode,
)
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
    postal_code: str | None,
) -> dict:
    """Build the GHL create-location body from the client row.

    `name` is `identity_name(...)` and falls back to the NOT-NULL
    `legal_entity`, so the name GHL holds is the same string the identity key
    and the divergence guard are computed from. Optional keys are omitted
    entirely when their source value is missing so we never send empty strings
    GHL might reject. `snapshotId` is included only when a snapshot is
    configured.

    `postalCode` (S1-26c review fix) makes the locations we create
    SELF-IDENTIFYING. The returning-client GHL lookup finds locations by email
    alone, which cannot tell two franchises of one brand apart; sending the
    postcode gives that lookup a second signal to corroborate against, so a
    later signing can prove a hit is the same site rather than assuming it.
    Locations created before this change carry no postcode and therefore
    cannot be corroborated - see `_classify_ghl_hit`.
    """
    payload: dict = {
        "name": identity_name(business_name, legal_entity) or legal_entity,
        "companyId": company_id,
    }
    if phone:
        payload["phone"] = phone
    if postal_code:
        payload["postalCode"] = postal_code

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


# Bound on the candidate sibling fetch. A key realistically has a handful of
# rows (a business signing for its second/third site); the cap only exists so a
# pathological key cannot pull an unbounded result set into memory. Hitting it
# is logged rather than silently truncated - a silent cap would read as "no
# match found" when the match was simply past the cap.
_SIBLING_CANDIDATE_LIMIT = 50

# Candidate returning-client siblings: prior clients rows that already hold a
# sub-account and match on the given predicate. ALL candidates are fetched (not
# `LIMIT 1`) because the caller must pick the one whose FULL normalized name
# matches, which SQL cannot evaluate - the identity key is a 6-char prefix, so
# the earliest row at a key is not necessarily the right business. `created_at
# ASC, id ASC` makes both the pick and the flagged-against candidate
# deterministic (`created_at` alone ties under the savepoint test fixture, and
# in any same-transaction batch).
_SIBLING_SELECT = (
    "SELECT id, COALESCE(parent_client_id, id) AS root_id, "
    "       ghl_subaccount_id, business_name, legal_entity, phone, "
    "       contact_first_name, contact_last_name, address "
    "FROM clients "
    "WHERE {predicate} AND id <> :client_id "
    "      AND ghl_subaccount_id IS NOT NULL "
    "ORDER BY created_at ASC, id ASC "
    f"LIMIT {_SIBLING_CANDIDATE_LIMIT}"
)

_SIBLING_BY_IDENTITY_KEY_SQL = text(
    _SIBLING_SELECT.format(predicate="identity_key = :identity_key")
)

# Fallback for a NULL identity_key. A document with no usable name or
# postcode would otherwise have NO DB-side dedup signal at all, which was a
# regression against S1-26 where email WAS the sibling key.
#
# LINKS on a STRONGER bar than the keyed path, not a weaker one (review round
# 4, finding 4 - supersedes round 2, finding 5). Round 2's first
# implementation auto-linked here under the SAME bar as the keyed path (name +
# phone), but that bar leans on the postcode to separate franchises - and the
# postcode is precisely what is absent here - so two "Brand Gym" rows sharing
# one `ops@` mailbox and one head-office number would have linked with no
# flag at all (F5). Round 4 found the opposite failure: refusing to link AT
# ALL turned a genuinely corroborated returning client into a GUARANTEED
# duplicate sub-account whenever `Company.Zip` happened to be blank. The fix
# is not to drop the postcode requirement, it is to REPLACE it: this path now
# ALSO requires the signing CONTACT's name to agree
# (`identity_key.contact_name_agrees`), a signal the keyed path does not need
# because it already has the postcode. Two different franchise sites under
# one shared mailbox and one shared head-office line are still expected to
# have DIFFERENT individuals signing for their own site (the client's
# franchisees have no shared access); the SAME person signing twice, with the
# same email, name and phone, absent a postcode, is the strongest evidence
# available that this is one business re-signing, not two.
_SIBLING_BY_EMAIL_SQL = text(_SIBLING_SELECT.format(predicate="lower(email) = lower(:email)"))


def _pick_sibling(
    candidates: Sequence[Row],
    *,
    client_name: str | None,
    client_phone: str | None,
    client_contact_first_name: str | None,
    client_contact_last_name: str | None,
    client_address: str | None,
    require_contact_name: bool,
) -> tuple[Row | None, Row | None]:
    """Split candidates into (matched, collided).

    `matched` is the EARLIEST candidate that clears every bar - the genuine
    returning-client link. `collided` is the best near-miss, returned only when
    nothing matched, so the possible-duplicate flag points at something
    concrete: a name-matching-but-uncorroborated candidate is preferred over an
    unrelated one, because that is the row a human most likely wants to compare.

    **Bar 1 - the full normalized name agrees.** Every candidate is scanned,
    not just the earliest, so a real returning client is still findable when an
    unrelated business got to the shared key first: "Fitness Studio" and
    "Fitness First" both key `fitnes|E81AA`, so a second "Fitness First"
    signing must see past the earlier "Fitness Studio" row.

    **Bar 2 - the PHONE agrees.** Name + postcode is not proof of one business
    (`Company.Zip` is the company postcode, not the studio's). Absence is not
    agreement. The address cannot serve as a second CORROBORATOR because it
    comes from the same company record as the postcode and therefore agrees
    exactly when the key already does - see `corroborating_signal_agrees`.

    **Bar 4 - the ADDRESS must not actively disagree.** Distinct from bar 2 in
    direction: it can only ever REFUSE a link, never grant one, so the
    same-company-record objection above does not apply to it (a signal that
    collapses with the key simply never fires). This is what closes review
    round 5's finding 1 - one owner, two sites, same brand, same head-office
    `Company.Zip` and the SAME `Client.Phone` on both documents clears bars 1
    and 2 and previously auto-linked site 2 into site 1's sub-account with no
    flag. The street addresses are the only thing on those rows that differ.
    Absence ABSTAINS here rather than failing closed (see
    `addresses_materially_diverge` for why the two postures are opposite).

    **Bar 3 (`require_contact_name=True` only) - the SIGNING CONTACT's name
    also agrees.** The identity-key path (`require_contact_name=False`) does
    not need this: its candidates already share a postcode by construction
    (the SQL filters on `identity_key`), which is the anchor that keeps bars
    1+2 safe against the franchise case (see `corroborating_signal_agrees`'s
    docstring). The NULL-identity-key email fallback has no such anchor - its
    candidates share only a literal email - so review round 4 (finding 4)
    replaces the missing postcode with `identity_key.contact_name_agrees`
    instead of dropping the requirement: two franchise sites sharing one
    `ops@` mailbox and one head-office number are still expected to have
    DIFFERENT people signing for their own site (the client's franchisees
    have no shared access), so requiring the SAME signer too is the
    postcode-shaped anchor this path was missing, not a weaker substitute
    for one.

    This SUPERSEDES round 2's finding 5, which made the email fallback
    detection-only after finding that bars 1+2 ALONE (the keyed path's bar,
    with no postcode anchor under it) reopened exactly the franchise
    conflation the identity key exists to prevent. Round 4 found the
    opposite failure: refusing to link AT ALL turned a genuinely corroborated
    returning client - same business, same `ops@` email, same phone, a
    second signing with a blank `Company.Zip` - into a GUARANTEED duplicate
    sub-account. Bar 3 resolves both: it demands MORE evidence than bars 1+2
    alone (closing round 2's gap) while still linking the genuine case round
    4 found broken.
    """
    collided: Row | None = None
    for candidate in candidates:
        candidate_name = identity_name(candidate.business_name, candidate.legal_entity)
        if names_materially_diverge(client_name, candidate_name):
            continue
        phone_agrees = corroborating_signal_agrees(phone_a=client_phone, phone_b=candidate.phone)
        contact_ok = (
            contact_name_agrees(
                client_contact_first_name,
                client_contact_last_name,
                candidate.contact_first_name,
                candidate.contact_last_name,
            )
            if require_contact_name
            else True
        )
        address_disqualifies = addresses_materially_diverge(client_address, candidate.address)
        if phone_agrees and contact_ok and not address_disqualifies:
            return candidate, None
        # Name matches but corroboration is incomplete. Remember the FIRST
        # such candidate as the flag target - it is a closer call for a human
        # than an unrelated prefix collision - and keep scanning, since a
        # later candidate may still corroborate properly.
        if collided is None:
            collided = candidate
    if collided is None:
        collided = candidates[0] if candidates else None
    return None, collided


def _location_postcode(location: GhlLocation) -> str:
    """The normalized postcode a GHL location carries, or "" if it has none.

    GHL returns address fields at the top level on some payload shapes and
    nested under `business` on others (the 21/07 live create response carried a
    `business` object), so both are read before concluding the location is
    uncorroboratable.
    """
    raw = location.raw or {}
    candidate = raw.get("postalCode")
    if not candidate:
        business = raw.get("business")
        if isinstance(business, dict):
            candidate = business.get("postalCode")
    if candidate is None or isinstance(candidate, bool):
        # `bool` is an `int` subclass, so it would otherwise stringify to
        # "True"/"False" and be treated as a postcode.
        return ""
    if isinstance(candidate, int):
        # A numeric postcode (75008) can arrive unquoted from JSON, where it
        # parses as int. Treating that as "no postcode" would send a perfectly
        # corroboratable INT location down the undecidable path and flag it for
        # no reason. Only `int` - a float is not a postcode in any format, and
        # str(75008.0) would normalize to the wrong digits.
        candidate = str(candidate)
    return normalize_postcode(candidate if isinstance(candidate, str) else None)


def _location_phone(location: GhlLocation) -> str | None:
    """The phone a GHL location carries, or None.

    Same top-level-or-nested-under-`business` shape as `_location_postcode` -
    the 21/07 live create response carried a `business` object - so both are
    read before concluding the location has no phone. Normalization is left to
    `corroborating_signal_agrees`, which already rejects filler by shape.
    """
    raw = location.raw or {}
    candidate = raw.get("phone")
    if not candidate:
        business = raw.get("business")
        if isinstance(business, dict):
            candidate = business.get("phone")
    if isinstance(candidate, int) and not isinstance(candidate, bool):
        # A phone can arrive unquoted from JSON, same as a numeric postcode.
        candidate = str(candidate)
    return candidate if isinstance(candidate, str) else None


# Verdicts on a location returned by the email lookup. Three states, not two,
# because "this is a different business" and "I cannot tell" call for different
# handling: both provision their own sub-account, but only the ambiguous one is
# worth a human's time. Flagging the confident negatives too would put a review
# badge on every franchise signing - Bullet has many - and a flag that fires on
# the normal path is a flag people learn to ignore.
_GHL_HIT_SAME_BUSINESS = "same_business"
_GHL_HIT_DIFFERENT_BUSINESS = "different_business"
_GHL_HIT_UNDECIDABLE = "undecidable"


def _classify_ghl_hit(
    location: GhlLocation,
    *,
    client_name: str | None,
    client_postcode: str | None,
    client_phone: str | None,
) -> str:
    """Decide whether a location found by email is the SAME business.

    The email lookup alone cannot distinguish franchises: "Brand Gym" Hackney
    and "Brand Gym" Croydon are one brand under one `ops@` mailbox but two
    clients, and reusing the first for the second puts one client's assets in
    the other's account. So a hit is judged on the full normalized name, the
    normalized postcode, and - since review round 5, finding 5 - the PHONE:

    | name    | postcode | phone   | verdict            |
    |---------|----------|---------|--------------------|
    | agrees  | agrees   | agrees  | same business      | -> reuse
    | agrees  | agrees   | absent  | undecidable        | -> own sub-account + flag
    | agrees  | agrees   | differs | undecidable        | -> own sub-account + flag
    | agrees  | differs  | any     | different business | -> own sub-account (a franchise)
    | agrees  | unknown  | any     | undecidable        | -> own sub-account + flag
    | differs | agrees   | any     | undecidable        | -> own sub-account + flag
    | differs | differs  | any     | different business | -> own sub-account
    | differs | unknown  | any     | different business | -> own sub-account
    | unknown | agrees   | any     | undecidable        | -> own sub-account + flag
    | unknown | differs  | any     | different business | -> own sub-account
    | unknown | unknown  | any     | undecidable        | -> own sub-account + flag

    **Why phone had to join (review round 5, finding 5).** Name + postcode is
    STRICTLY the bar round 2 proved insufficient for the DB leg, and the GHL
    leg was still merging on it. The sequence that exploits it: franchise A's
    `create_location` succeeds but the response is lost, so A's row keeps
    `ghl_subaccount_id IS NULL` and `_SIBLING_SELECT` excludes it. B signs -
    same brand, same head-office Zip, shared `ops@`. The DB leg finds nothing,
    the GHL lookup returns A's orphan carrying the name and postcode WE SENT,
    and B reuses A's location: two franchisees in one sub-account, no flag, no
    `parent_client_id` trace. The phone is on that payload too
    (`_build_location_payload`), so it is available to separate them - and it
    is the same independent signal bar 2 uses on the DB leg, which keeps the
    two legs consistent rather than leaving the weaker one as the way in.

    "unknown" means EITHER side lacks a postcode - the location (every
    sub-account created before we started sending one) or the client. Absence
    of evidence is not evidence, so those go to a human rather than being
    guessed either way. Where a signal actively points at "different", we take
    it: name-differs + no postcode is a shared ops mailbox across two real
    clients, which is normal at Bullet, not an anomaly.

    Note on the at-least-once backstop, and the precondition the phone bar adds
    to it (stated, review round 5, because the earlier wording claimed more than
    the code delivers). A location WE created and then lost the response for
    carries the name, postcode AND phone we sent, so IF the search returns it,
    it lands in row 1 and is reused rather than duplicated. The search usually
    will NOT return it - it is eventually consistent and the retry follows
    within seconds (S1-26d) - so this classification never closed that window,
    it only avoids making it worse.

    **The backstop now requires the client to HAVE a phone.** For a client with
    `Client.Phone` empty, `_build_location_payload` omits the field, so our own
    orphan comes back carrying no phone either; both sides absent is not
    agreement, so the verdict is UNDECIDABLE and the retry provisions a SECOND
    location (flagged, pointing at the orphan) where it previously reused the
    first. That is a deliberate trade, not an oversight: those same clients also
    fail DB bar 2 permanently, so the alternative - treating mutual absence as
    corroboration - is precisely the round-2 franchise conflation, and it would
    reuse a location on nothing but a shared brand, postcode and `ops@` mailbox.
    The cost of the conservative side is one spare, flagged, deletable
    sub-account; the cost of the other is one client's assets inside another
    client's account. Address (bar 4) does not help here because a GHL location
    payload carries no address we send.
    """
    # A MISSING name is not a name that disagrees (review round 5).
    # `names_materially_diverge` returns True on an empty stem - correct where
    # it guards a corroborating bar, wrong here, because the branch below reads
    # "name differs" as positive evidence of a different business and
    # deliberately does NOT flag. A legacy location with `"name": null` and no
    # postcode would therefore be classed DIFFERENT_BUSINESS and pass silently,
    # while the identical absence of a POSTCODE one line later yields
    # UNDECIDABLE + a flag. Absence has to read the same way on both signals.
    name_known = bool(normalize_name(client_name)) and bool(normalize_name(location.name))
    name_agrees = name_known and not names_materially_diverge(client_name, location.name)
    location_postcode = _location_postcode(location)
    client_postcode_norm = normalize_postcode(client_postcode)

    if not location_postcode or not client_postcode_norm:
        if not name_known:
            # Nothing known on either signal - the most ambiguous case there
            # is, so it must not be a confident verdict in either direction.
            return _GHL_HIT_UNDECIDABLE
        return _GHL_HIT_UNDECIDABLE if name_agrees else _GHL_HIT_DIFFERENT_BUSINESS
    if location_postcode == client_postcode_norm:
        if not name_agrees:
            return _GHL_HIT_UNDECIDABLE
        # Name and postcode agreeing is exactly the bar round 2 rejected. The
        # phone must agree too, and absence is not agreement - an
        # uncorroborated hit goes to a human rather than being merged.
        phone_agrees = corroborating_signal_agrees(
            phone_a=client_phone, phone_b=_location_phone(location)
        )
        return _GHL_HIT_SAME_BUSINESS if phone_agrees else _GHL_HIT_UNDECIDABLE
    return _GHL_HIT_DIFFERENT_BUSINESS


async def _clear_possible_duplicate(session: AsyncSession, *, client_id: uuid.UUID) -> None:
    """Clear a stale duplicate flag when a LATER run reaches a confident link.

    A run that ends in a corroborated DB sibling has answered the question an
    earlier flag was asking, so it clears it rather than leaving the badge up
    forever.

    **SCOPE, corrected (review round 5).** This used to claim it stopped the
    ~100 legacy no-postcode GHL sub-accounts from saturating the board. It
    cannot, and the claim mattered because it made a real gap look closed:

    - the only call site is the DB-sibling LINK path, so a clear requires a
      later signing that finds a corroborated sibling row;
    - flags raised by the GHL-UNDECIDABLE path (which is where the legacy
      no-postcode locations land) belong to rows that have just been given
      their own `ghl_subaccount_id`, so every later run for that client
      short-circuits on the already-provisioned check and never reaches here.

    Those flags are therefore permanent until a human resolves them, which is
    exactly what S1-26e's merge action is for. That is the correct default -
    re-deciding an ambiguous comparison automatically is how a wrong merge
    happens - but it does mean the badge count is load-bearing on S1-26e
    landing, not self-limiting.

    Only clears what THIS design sets; a flag a human raised by hand is not
    distinguishable here, which is the other reason S1-26e owns resolution.
    """
    await session.execute(
        text(
            "UPDATE clients "
            "SET possible_duplicate = false, possible_duplicate_of = NULL, "
            "    possible_duplicate_ghl_id = NULL "
            "WHERE id = :client_id AND possible_duplicate = true"
        ),
        {"client_id": client_id},
    )


async def _flag_possible_duplicate(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    sibling_id: uuid.UUID | None = None,
    ghl_location_id: str | None = None,
) -> None:
    """Mark this client for human review and record what it collided with.

    Set on either kind of near-miss: an identity-key collision whose names
    diverge (`sibling_id`), or a GHL location found by email that could not be
    corroborated (`ghl_location_id`). The row is still provisioned its own
    sub-account - the flag exists so the dashboard can show the candidate and a
    human can merge deliberately, never so the automation merges for them.
    """
    await session.execute(
        text(
            "UPDATE clients "
            "SET possible_duplicate = true, "
            "    possible_duplicate_of = COALESCE(:sibling_id, possible_duplicate_of), "
            "    possible_duplicate_ghl_id = COALESCE(:ghl_id, possible_duplicate_ghl_id) "
            "WHERE id = :client_id"
        ),
        {"sibling_id": sibling_id, "ghl_id": ghl_location_id, "client_id": client_id},
    )


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
    DB sibling's sub-account if this is a returning client whose full name
    matches (link `parent_client_id`); else record an `in_progress` action +
    COMMIT, look GHL up by email, reuse only a hit corroborated on name AND
    postcode, else POST create; on success record `success` + write
    `ghl_subaccount_id` back + COMMIT, or on error record `failed` + COMMIT and
    re-raise. An uncorroborated match at either step flags `possible_duplicate`
    and provisions its own sub-account rather than merging.

    Raises:
        ClientNotFoundError: no `clients` row for `client_id`. Rolls back.
        Exception: any failure of the GHL lookup OR create-location call
            (GhlClientError 4xx, GhlServerError 5xx/429, an httpx timeout /
            transport error, or an empty-config GhlNotConfiguredError) is recorded as
            a `failed` action and committed, then re-raised unchanged so the
            wrapper can decide retriable vs not.
    """
    client = (
        await session.execute(
            text(
                "SELECT email, business_name, legal_entity, "
                "       contact_first_name, contact_last_name, phone, "
                "       ghl_subaccount_id, identity_key, postal_code, address "
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

    client_name = identity_name(client.business_name, client.legal_entity)

    # S1-26 returning-client check (1/2): local DB sibling. If a PRIOR clients
    # row with the same IDENTITY KEY (S1-26c: first6(normalized name) +
    # postcode) already holds a ghl_subaccount_id, this is a returning client
    # (e.g. signing for a second gym/site, or the same business under a
    # different email). Link this row to the original ROOT
    # (`COALESCE(parent_client_id, id)`, which keeps a flat two-level tree
    # rather than chains) and reuse the existing sub-account id - never
    # provision a second location for the same client. The earliest NAME-MATCHING
    # row wins so the root is stable across multiple returning signings.
    #
    # A NULL identity_key (no usable name/postcode) cannot match on identity, so
    # it falls back to the EMAIL sibling query - S1-26's original key. Without
    # that fallback those documents would have no DB-side dedup at all, which is
    # strictly worse than before this ticket.
    keyed = client.identity_key is not None
    if keyed:
        candidates = (
            await session.execute(
                _SIBLING_BY_IDENTITY_KEY_SQL,
                {"identity_key": client.identity_key, "client_id": client_id},
            )
        ).all()
    else:
        candidates = (
            await session.execute(
                _SIBLING_BY_EMAIL_SQL,
                {"email": client.email, "client_id": client_id},
            )
        ).all()

    if len(candidates) == _SIBLING_CANDIDATE_LIMIT:
        # Never silently truncate: a match past the cap would look identical to
        # "no returning client found" and quietly provision a duplicate.
        log.warning(
            "S1-26c sibling candidate cap hit; a match beyond the cap would be missed",
            extra={
                "client_id": str(client_id),
                "identity_key": client.identity_key,
                "limit": _SIBLING_CANDIDATE_LIMIT,
            },
        )

    # S1-26c dedup guard, FOUR bars (see `_pick_sibling`). (1) The identity key
    # truncates the name to 6 chars, so two DIFFERENT businesses sharing a name
    # prefix AND a postcode collide on it ("Fitness First" vs "Fitness Studio");
    # every candidate is scanned for a full-name match, not just the earliest.
    # (2) The PHONE must also agree, because `Company.Zip` is the COMPANY
    # postcode rather than the studio's, so a franchisee entering their brand
    # plus a head-office postcode produces an identical key AND identical names
    # for two different studios. Failing either bar flags a possible duplicate
    # and provisions this signing as its own client (never auto-merge).
    #
    # The email fallback (unkeyed) now links too, on a STRONGER bar than the
    # keyed path (review round 4, finding 4 - supersedes round 2, finding 5):
    # it lacks the postcode anchor bars 1+2 lean on, so `require_contact_name`
    # replaces it with the signing contact's name instead of dropping the
    # requirement. See `_pick_sibling`'s docstring for the full reasoning.
    sibling, collision = _pick_sibling(
        candidates,
        client_name=client_name,
        client_phone=client.phone,
        client_contact_first_name=client.contact_first_name,
        client_contact_last_name=client.contact_last_name,
        client_address=client.address,
        require_contact_name=not keyed,
    )

    if collision is not None:
        await _flag_possible_duplicate(session, client_id=client_id, sibling_id=collision.id)
        log.warning(
            "S1-26c possible duplicate: sibling collision, not corroborated",
            extra={
                "client_id": str(client_id),
                "sibling_client_id": str(collision.id),
                "identity_key": client.identity_key,
                "candidate_count": len(candidates),
            },
        )

    if sibling is not None:
        # A corroborated link answers the question any earlier flag was asking,
        # so clear it rather than leaving the badge up forever (it would
        # otherwise saturate the board before S1-26e lands and stop being read).
        await _clear_possible_duplicate(session, client_id=client_id)
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
        postal_code=client.postal_code,
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
        #
        # RECOVER FROM A DEACTIVATED TRANSACTION (review round 5). When `exc` is
        # itself a DB error - exactly the case the `_flag_possible_duplicate`
        # guards exist to catch - SQLAlchemy has already deactivated the
        # transaction, so this UPDATE raises `PendingRollbackError`. That
        # propagates out of the handler, the action never reaches `failed`, and
        # it is stranded `in_progress` forever: precisely the zombie those
        # guards were added to prevent, reintroduced by their own recovery path.
        #
        # Rolled back ON DEMAND rather than unconditionally, because an
        # unconditional rollback would DISCARD THE POSSIBLE-DUPLICATE FLAG on
        # every ordinary failure. That flag is deliberately uncommitted when it
        # is written - it rides this terminal commit (see the comments at both
        # `_flag_possible_duplicate` call sites) - so a blanket rollback would
        # silently drop the human's only signal that a signing was ambiguous,
        # every time the GHL create failed. Trying the write first keeps the
        # flag on the ordinary path and still recovers the DB-error path, where
        # the flag write is the thing that failed anyway.
        # Catches `SQLAlchemyError`, not just `PendingRollbackError`: which of
        # the two surfaces depends on WHO notices the broken transaction first.
        # If SQLAlchemy has already marked it deactivated it raises
        # `PendingRollbackError`; if the statement reaches Postgres first the
        # server rejects it and asyncpg's `InFailedSQLTransactionError` arrives
        # wrapped as a `DBAPIError`. Both derive from `SQLAlchemyError`, and the
        # savepoint-based test fixture reproduces the SECOND - so pinning only
        # the first left the zombie open on the very path this recovers.
        # The COMMIT is inside the guarded block, not after it. A Neon
        # connection reset between a successful UPDATE and its COMMIT is routine
        # on serverless Postgres, and with the commit outside, that reset
        # propagates, the `failed` state never becomes durable, the row stays
        # `in_progress`, and the original exception is masked so the wrapper
        # classifies retriable-vs-not on the wrong type.
        try:
            await fail_action(session, action_id=begun.action_id, last_error=str(exc))
            await session.commit()
        except SQLAlchemyError:
            await session.rollback()
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
    # GHL but not in our DB. The lookup runs on EVERY create attempt (rider a).
    #
    # A hit is NOT reused on the strength of the email alone - email is the
    # identity we deliberately moved OFF one branch earlier, and reusing on it
    # here re-conflates exactly the franchises the identity key separates.
    # `_classify_ghl_hit` demands the name AND the postcode; anything less
    # provisions its own sub-account, and an UNDECIDABLE verdict also records
    # the suspected location for a human to merge deliberately.
    #
    # THIS LEG IS SUBORDINATE TO THE DB LEG (review round 2, finding 1). If the
    # dedup guard above refused a candidate this run, GHL reuse is skipped
    # entirely rather than re-judged. Two reasons it cannot be trusted to
    # re-decide: (a) it judges a DIFFERENT object - a GHL location, not a
    # clients row - so it cannot see the address bar at all (a location payload
    # carries no address we send); and (b) on precisely the flagged path the
    # postcode
    # test is true BY CONSTRUCTION, because rows sharing an `identity_key` share
    # the normalized postcode - so the verdict collapses to a name check that
    # bar 1 has already passed. The result was a row flagged as a possible
    # duplicate AND merged into the very sibling the DB leg had just refused,
    # while the dashboard told the operator it "was given its own sub-account".
    if collision is not None:
        log.info(
            "S1-26c skipping GHL reuse: the DB guard refused a candidate this run",
            extra={"client_id": str(client_id), "sibling_client_id": str(collision.id)},
        )
        existing = None
    else:
        try:
            existing = await ghl_client.find_location_by_email(client.email, company_id=company_id)
        except Exception as exc:
            await _record_failure(exc)
            raise

    if existing is not None:
        verdict = _classify_ghl_hit(
            existing,
            client_name=client_name,
            client_postcode=client.postal_code,
            client_phone=client.phone,
        )
        if verdict != _GHL_HIT_SAME_BUSINESS:
            # Only the ambiguous verdict is a human's problem. A confident
            # "different business" is the normal franchise / shared-mailbox
            # path, and flagging it would bury the real cases in noise.
            #
            # NOT committed here: the flag rides the terminal commit below
            # (complete_action on create success, or fail_action on failure),
            # exactly as the DB-sibling collision flag does. An extra commit in
            # this gap would be one more thing that can raise between the
            # already-committed `in_progress` row and its terminal state, and
            # anything that raises here bypasses `_record_failure` and strands
            # the action `in_progress` forever - a silent zombie. Riding the
            # terminal commit also makes the flag atomic with the outcome, so
            # "flagged but no action row" is unreachable.
            if verdict == _GHL_HIT_UNDECIDABLE:
                # Guarded: this write sits between the COMMITTED `in_progress`
                # row and its terminal state, so anything it raises would
                # otherwise bypass `_record_failure` and strand the action
                # `in_progress` forever - the very zombie the comment above
                # reasons about avoiding, which the un-wrapped version left open.
                try:
                    await _flag_possible_duplicate(
                        session, client_id=client_id, ghl_location_id=existing.id
                    )
                except Exception as exc:
                    await _record_failure(exc)
                    raise
            log.info(
                "S1-26c GHL lookup hit not reused; provisioning own sub-account",
                extra={
                    "client_id": str(client_id),
                    "ghl_location_id": existing.id,
                    "ghl_location_name": existing.name,
                    "verdict": verdict,
                    "has_location_postcode": bool(_location_postcode(existing)),
                    # The phone is now the deciding signal whenever name and
                    # postcode BOTH agree, so without it an operator reading
                    # "verdict: undecidable, has_location_postcode: true"
                    # cannot tell what was actually missing.
                    "has_location_phone": bool(_location_phone(existing)),
                },
            )
            existing = None

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
        # so two DIFFERENT client rows for the same business (two
        # near-simultaneous returning signings, possibly under different emails)
        # cannot both pass the returning-client check and create duplicate
        # locations - the second waits, then finds the first as a committed DB
        # sibling. `dedup_key` (event payload) = the identity_key when present,
        # else `email:<normalized>`, else the unique client_id. The email tier
        # matters: a NULL identity_key still has a DB-side dedup path (the email
        # sibling query), so those rows must serialise with each other rather
        # than each getting a private bucket. The identity_key is already
        # normalized (lowercased, punctuation-stripped) and the email tier is
        # lowercased at the producer, so this also closes the old S1-26 residual
        # where the raw-string email key failed to serialise case-only email
        # differences.
        #
        # The CEL ternary is back-compat for events already QUEUED when this
        # deploys: they predate `dedup_key` and would otherwise all land in one
        # null bucket, letting a legacy and a fresh event for the same business
        # run concurrently. The fallback is the EMAIL, not `client_id`: a
        # per-row id is a PRIVATE bucket that serialises nothing, so legacy and
        # replayed events would still race each other. `email` is on every
        # `client.created` payload ever emitted, and mirrors the producer's own
        # `email:` tier.
        #
        # WHAT THIS DOES NOT COVER (corrected, review round 5 - this comment
        # used to claim "both shapes land in the same bucket", which is false).
        # A KEYED business emits `dedup_key = identity_key`, while a legacy
        # event queued before S1-26c deployed has no `dedup_key` at all and
        # falls back to `email:...`. Those are different buckets, so during the
        # deploy window a legacy event and a fresh one for the SAME business do
        # NOT serialise against each other. The residual risk is bounded: it
        # needs both to be in flight simultaneously, and `platform_actions`'
        # idempotency key plus the sibling re-check still stand behind it. It
        # closes on its own once the queue drains, and cannot be fixed in CEL
        # because the expression cannot compute an identity_key.
        #
        # CEL has no `??`, and Inngest's docs do not document
        # one, so `has()` + ternary is used rather than an operator that might
        # not exist - a rejected expression fails the ALL-OR-NOTHING function
        # registration, which is precisely the S1-26a outage. Verified against a
        # live registration before merge; still 2 constraints either way.
        inngest.Concurrency(
            key='has(event.data.dedup_key) ? event.data.dedup_key : "email:" + event.data.email',
            limit=1,
            scope="fn",
        ),
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
        except GhlNotConfiguredError as exc:
            # An empty agency key: retrying an unset env var achieves nothing.
            #
            # NARROWED from `except RuntimeError` (review round 5).
            # `httpx.StreamError` IS a `RuntimeError` subclass, so the broad
            # catch dead-lettered transport-level streaming failures - fully
            # recoverable signings - into the same terminal bucket as a
            # misconfiguration, each then needing a human to re-drive.
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
