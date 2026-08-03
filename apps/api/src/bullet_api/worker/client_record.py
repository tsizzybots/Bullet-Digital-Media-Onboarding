"""S1-25a: client-record creation from a signed PandaDoc.

`create_client_record` is the orchestrator entry step for every downstream
fan-out. Triggered by `pandadoc.signed` (emitted by the S1-22 webhook), it
fetches the full document body via the PandaDoc API
(`GET /public/v1/documents/{id}/details`), extracts client fields,
idempotently upserts a row in `clients` (keyed on `pandadoc_document_id`
via the 0007 UNIQUE INDEX), backfills `onboarding_events.client_id` +
`processed_at`, commits, then emits a `client.created` event. Every Sprint
1-3 fan-out function (GHL sub-account creation in S1-25, signed-PDF
storage in S1-25b, Asana / Stripe / Xero / Timely in Sprint 2-3) keys off
`client.created` rather than `pandadoc.signed` so `client_id` is
guaranteed to be present.

Why fetch from the API and not read `onboarding_events.payload` JSONB?

The webhook payload that S1-22 captured only carries `data.id` +
`data.status` - it has none of the client values we need (email, business
name, recipients, HubSpot ids). Those live on the document detail
endpoint, where they are populated by HubSpot's PandaDoc integration at
doc-send time as `tokens`, filled in by the client during signing as
`fields`, and tagged by HubSpot as `metadata`. The single fetch here is
the authoritative source.

Correctness rules:

- **Commit BEFORE emit.** The UPSERT + backfill commit first, then the
  `client.created` emit runs. If the emit fails, Inngest retries; on
  retry the UPSERT is a no-op (row exists), the backfill UPDATE is
  idempotent (COALESCE on `processed_at`), and the emit gets another
  shot. Downstream consumers can NEVER receive `client.created` for a
  client that isn't durable in the DB. This is a deliberate reversal of
  the S1-22 webhook ordering (which is emit-then-commit because its
  retry source is PandaDoc, not Inngest).
- **PandaDoc fetch happens OUTSIDE the DB session.** A signed PandaDoc
  fetch can take seconds; holding the pooled connection during that
  window would leak connections under a burst of signings.
- The UPSERT uses ``ON CONFLICT (pandadoc_document_id) DO UPDATE SET
  pandadoc_document_id = EXCLUDED.pandadoc_document_id`` - the SET is a
  no-op but guarantees ``RETURNING id`` populates on both insert and
  conflict, so the orchestrator always learns the surviving row's id in
  a single round-trip. The unique index on `pandadoc_document_id`
  (migration 0007) is the structural idempotency guarantee: a replayed
  `pandadoc.signed` event never creates a second client.
- `current_step = 'signed'` is set on INSERT only; ON CONFLICT DO UPDATE
  deliberately does NOT touch it. A replay for a client that has since
  progressed (to `portal`, `kickoff`, etc.) must not regress the step.
- `legal_entity` is NOT NULL in the schema; if the form-field extraction
  returns None we fall back to `business_name` and finally to a
  clearly-marked placeholder. `business_name` itself stays NULL when
  missing (the column is NULL-able); the placeholder only ever lands in
  `legal_entity` so dashboard alerts on "client needing review" are not
  duplicated across both columns.
- Missing `Client.Email` token IS a hard failure: `extract_client_fields`
  raises `PandaDocPayloadError("tokens[Client.Email]")`, the transaction
  rolls back, no partial row persists. Wrapped in
  `inngest.NonRetriableError` so Inngest dead-letters immediately - the
  document body cannot self-heal on retry.
- A PandaDoc 404 (document deleted between webhook and orchestrator) and
  an empty PandaDoc API key are likewise wrapped as NonRetriable.
  Other PandaDoc errors (5xx, 429, timeout) propagate naturally so
  Inngest retries them.
- An orphan `onboarding_events.id` (audit row not visible) raises
  `OnboardingEventNotFoundError` and **propagates** so Inngest retries.
  The common cause is the producer's emit-before-commit visibility race
  (S1-22 webhook does INSERT -> emit -> commit, so Inngest can dispatch
  our function in the small window before the producer's commit lands).
  A single retry absorbs the race. A genuinely-orphaned row (manual
  deletion) is rare and still dead-letters after Inngest's default
  retry budget is exhausted.
- A per-document concurrency cap (`limit=1`,
  `key="event.data.document_id"`) prevents duplicate PandaDoc fetches and
  duplicate `client.created` emits when two events fire for the same
  signing.

The function exposes a pure testable core `create_client_record_core`
that takes a session + already-fetched document body + emitter, plus a
thin Inngest-bound wrapper. This matches the reconcile-cron split in
`bullet_api.crons.reconcile_pandadoc`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import inngest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.config import get_settings
from bullet_api.db.enums import CURRENT_STEP_SIGNED, DOCUMENT_KIND_TRANSCRIPT_TEXT
from bullet_api.db.session import AsyncSessionLocal
from bullet_api.pandadoc.accounts import PANDADOC_ACCOUNT_UK, api_key_for
from bullet_api.pandadoc.client import HttpPandaDocClient, PandaDocClient, PandaDocNotFound
from bullet_api.transcripts.linking import (
    LINK_METHOD_EMAIL_SIGNING,
    link_transcript_to_client,
)
from bullet_api.worker._inngest import inngest_client
from bullet_api.worker.clients_payload import (
    PandaDocPayloadError,
    extract_client_fields,
)
from bullet_api.worker.events import (
    CLIENT_CREATED_EVENT,
    PANDADOC_SIGNED_EVENT,
    TRANSCRIPT_LINKED_EVENT,
    EventEmitter,
    InngestEventEmitter,
)
from bullet_api.worker.identity_key import (
    LEGAL_ENTITY_PLACEHOLDER,
    compute_identity_key,
    identity_name,
)

log = logging.getLogger(__name__)

# LEGAL_ENTITY_PLACEHOLDER is the value written when the legal-trading-name form
# field is missing AND no business_name token was extracted either. The clients
# table requires legal_entity NOT NULL, and we deliberately keep the orchestrator
# insertable rather than dead-lettering on a soft field: the placeholder is
# obvious in the dashboard so the team can correct it manually. Only ever written
# to `legal_entity` - never to `business_name`, which is NULL-able and stays NULL
# when missing. It is DEFINED in `worker.identity_key` (and re-exported here for
# the existing importers) because the identity helpers must reject it: it is a
# marker, not a name, and keying on it would unite every unidentifiable signing.


class OnboardingEventNotFoundError(LookupError):
    """The backfill UPDATE matched 0 rows for the given event id.

    Common cause is the S1-22 webhook's emit-before-commit ordering:
    the producer INSERTs the audit row, emits `pandadoc.signed`, THEN
    commits. Inngest can dispatch our orchestrator inside the small
    window before the producer's commit becomes visible to our separate
    transaction, in which case the backfill UPDATE matches nothing and
    we raise this. The Inngest wrapper does NOT translate this into a
    NonRetriableError - it propagates so Inngest's default retry policy
    (4 attempts with exponential backoff over ~minutes) absorbs the
    visibility race. A genuinely-orphaned row (e.g. someone manually
    deleted the audit row) still dead-letters after retries are
    exhausted. Asymmetric cost: a transient race self-heals on one
    retry, vs. a real orphan which would dead-letter under either
    policy.
    """

    def __init__(self, event_id: uuid.UUID) -> None:
        self.event_id = event_id
        super().__init__(
            f"onboarding_events row {event_id} not found; cannot backfill "
            "client_id for pandadoc.signed event."
        )


@dataclass(frozen=True)
class CreateClientResult:
    """Outcome of one `create_client_record_core` run.

    `created` is True when the UPSERT inserted a new row, False when it
    matched an existing one (replay). `client_id` is the surviving row's
    id in either case. `client_created_emitted` reflects whether
    `client.created` was emitted on this run.
    """

    client_id: uuid.UUID
    created: bool
    client_created_emitted: bool


async def fetch_document_for_orchestrator(
    pandadoc_client: PandaDocClient, document_id: str
) -> dict:
    """Fetch the PandaDoc document body, translating NonRetriable causes.

    PandaDocNotFound (404 - document deleted) and RuntimeError (empty
    PandaDoc API key - mis-configured deploy) both indicate failures
    that retry will not fix; wrap them in `inngest.NonRetriableError` so
    Inngest dead-letters the run immediately rather than burning the
    default retry budget. Other errors (httpx 5xx, 429, timeout)
    propagate naturally and Inngest will retry them.
    """
    try:
        return await pandadoc_client.fetch_document_details(document_id)
    except PandaDocNotFound as exc:
        raise inngest.NonRetriableError(
            f"PandaDoc returned 404 for document {document_id}; cannot create client record."
        ) from exc
    except RuntimeError as exc:
        # `HttpPandaDocClient` raises RuntimeError when api_key is empty.
        raise inngest.NonRetriableError(str(exc)) from exc


async def _link_parked_transcripts(
    session: AsyncSession, *, client_id: uuid.UUID, email: str
) -> None:
    """Link every unlinked sales-call transcript whose invite attendees include
    this client's email.

    Match is byte-exact jsonb containment against the lowercased email, which is
    why `participant_emails` is stored lowercased (jsonb cannot use the citext
    semantics `clients.email` has). Does not commit; the post-commit emit is
    re-derived separately from the committed rows.
    """
    candidates = (
        (
            await session.execute(
                text(
                    "SELECT id FROM sales_call_transcripts "
                    "WHERE client_id IS NULL "
                    "  AND participant_emails @> to_jsonb(cast(:email AS text)) "
                    "ORDER BY captured_at"
                ),
                {"email": email.lower()},
            )
        )
        .scalars()
        .all()
    )
    for transcript_id in candidates:
        await link_transcript_to_client(
            session,
            transcript_id=transcript_id,
            client_id=client_id,
            link_method=LINK_METHOD_EMAIL_SIGNING,
        )


async def _emit_signing_transcript_links(
    emitter: EventEmitter, session: AsyncSession, client_id: uuid.UUID
) -> None:
    """Emit `transcript.linked` for every signing-linked transcript of this
    client that has a documents row. Re-derived from the committed state (not
    just this pass's links) so an Inngest retry after a post-commit-pre-emit
    crash still delivers the trigger - the same at-least-once safety the capture
    worker has. S1-29 dedupes per transcript."""
    rows = (
        await session.execute(
            text(
                "SELECT t.id AS transcript_id, t.r2_key, t.source, d.id AS document_id "
                "FROM sales_call_transcripts t "
                "JOIN documents d "
                "  ON d.client_id = t.client_id AND d.kind = :kind AND d.r2_key = t.r2_key "
                "WHERE t.client_id = :cid AND t.link_method = :method"
            ),
            {
                "cid": client_id,
                "kind": DOCUMENT_KIND_TRANSCRIPT_TEXT,
                "method": LINK_METHOD_EMAIL_SIGNING,
            },
        )
    ).all()
    for row in rows:
        await emitter.send(
            TRANSCRIPT_LINKED_EVENT,
            {
                "client_id": str(client_id),
                "transcript_id": str(row.transcript_id),
                "r2_key": row.r2_key,
                "source": row.source,
                "document_id": str(row.document_id),
            },
        )


async def create_client_record_core(
    session: AsyncSession,
    *,
    onboarding_event_id: uuid.UUID,
    document_id: str,
    document: dict,
    emitter: EventEmitter,
    account: str = PANDADOC_ACCOUNT_UK,
) -> CreateClientResult:
    """Run the S1-25a orchestrator step against an explicit session +
    already-fetched document body + emitter.

    The PandaDoc HTTP fetch is the wrapper's responsibility; the core is
    pure DB + extraction work so the integration tests inject a
    synthetic `document` dict directly and never touch a PandaDoc client.

    Steps: extract client fields, upsert `clients`, backfill
    `onboarding_events`, COMMIT, emit `client.created`. The emit happens
    after the commit so downstream consumers never see a `client.created`
    for a client that isn't durable. If the emit fails, Inngest retries
    and the UPSERT is a no-op on the second pass.

    Raises:
        PandaDocPayloadError: the document is missing `Client.Email` or
            another required field. The transaction rolls back.
            Inngest wrapper translates to NonRetriableError.
        OnboardingEventNotFoundError: the backfill UPDATE matched 0 rows
            for the given event id. The transaction rolls back. The
            Inngest wrapper does NOT translate this - it propagates so
            Inngest retries (absorbs the producer's emit-before-commit
            visibility race). See the class docstring for details.
    """
    fields = extract_client_fields(document)

    # business_name is NULL-able in the schema; only legal_entity (NOT NULL)
    # gets the placeholder when missing. Do NOT write the placeholder into
    # business_name - downstream displays would render "Unknown - needs
    # review" as the customer's business name.
    legal_entity = fields.legal_entity or fields.business_name or LEGAL_ENTITY_PLACEHOLDER
    business_name = fields.business_name

    # S1-26c: the returning-client identity key = first6(normalized name) +
    # "|" + normalized postcode. None when it cannot be computed (no usable
    # name, or no usable postcode) - the returning-client check (in the GHL
    # worker) self-skips on a NULL key and falls back to an email-keyed sibling
    # match, so an unidentifiable signing becomes a fresh client rather than
    # being merged into the wrong one (fail-safe to CREATE).
    #
    # Keyed on `identity_name` (business_name, else the signed legal entity),
    # NOT business_name alone: Bullet's template fills `Company.Name` from
    # HubSpot but the legal-trading-name is a form field filled in during
    # signing, so a document carrying only the latter would otherwise opt out
    # of returning-client matching entirely. Same expression the GHL location
    # `name` is built from, so the key, the divergence guard and what GHL sees
    # all agree on who this client is.
    identity_key = compute_identity_key(
        identity_name(fields.business_name, legal_entity), fields.postal_code
    )

    # ON CONFLICT DO UPDATE so RETURNING id populates on both insert and
    # conflict. current_step is set only on INSERT (ON CONFLICT path does not
    # touch it); a replay must not regress a downstream step.
    #
    # `postal_code` / `identity_key` ARE refreshed on conflict, under COALESCE
    # (fill-if-NULL, never overwrite). Without this they are written on first
    # insert only, so a row that predates migration 0013 - or one whose first
    # extraction could not compute a key - keeps a NULL key forever while the
    # re-emitted event carries a real `dedup_key`, which silently disables the
    # returning-client match for that client. COALESCE rather than a blind
    # overwrite so a value corrected by hand is not clobbered by a replay.
    #
    # `(xmax = 0) AS inserted` reads the system column to detect insert
    # vs conflict in one round-trip. xmax is 0 for a fresh insert and
    # non-zero for an updated row; reliable across PG >= 9.0.
    # step_entered_at is not in the INSERT - the column's server_default
    # (now()) populates it on insert, and ON CONFLICT DO UPDATE doesn't
    # touch it. Same outcome with one fewer redundant value.
    upserted = await session.execute(
        text(
            "INSERT INTO clients ("
            "  email, business_name, legal_entity,"
            "  contact_first_name, contact_last_name, phone,"
            "  pandadoc_document_id, hubspot_contact_id,"
            "  postal_code, address, identity_key,"
            "  current_step"
            ") VALUES ("
            "  :email, :business_name, :legal_entity,"
            "  :contact_first_name, :contact_last_name, :phone,"
            "  :pandadoc_document_id, :hubspot_contact_id,"
            "  :postal_code, :address, :identity_key,"
            "  :current_step"
            ") "
            "ON CONFLICT (pandadoc_document_id) DO UPDATE "
            "  SET pandadoc_document_id = EXCLUDED.pandadoc_document_id, "
            "      postal_code = COALESCE(clients.postal_code, EXCLUDED.postal_code), "
            "      address = COALESCE(clients.address, EXCLUDED.address), "
            "      identity_key = COALESCE(clients.identity_key, EXCLUDED.identity_key) "
            "RETURNING id, (xmax = 0) AS inserted, identity_key"
        ),
        {
            "email": fields.email,
            "business_name": business_name,
            "legal_entity": legal_entity,
            "contact_first_name": fields.contact_first_name,
            "contact_last_name": fields.contact_last_name,
            "phone": fields.phone,
            "pandadoc_document_id": document_id,
            "hubspot_contact_id": fields.hubspot_contact_id,
            "postal_code": fields.postal_code,
            # S1-26c review fix (finding 4): persisted because it is one of the
            # two corroborating signals a returning-client auto-link requires.
            "address": fields.address,
            "identity_key": identity_key,
            "current_step": CURRENT_STEP_SIGNED,
        },
    )
    client_row = upserted.one()
    client_id: uuid.UUID = client_row.id
    created: bool = bool(client_row.inserted)
    # The SURVIVING key, not the one just computed: on a conflict COALESCE
    # keeps whatever the row already held, and the concurrency bucket below
    # must name the same key the GHL worker's sibling query will match on.
    identity_key = client_row.identity_key
    normalized_email = fields.email.strip().lower()
    dedup_key = identity_key or (
        f"email:{normalized_email}" if normalized_email else str(client_id)
    )

    # Backfill the audit row. `processed_at` uses COALESCE so a retry
    # preserves the first-success timestamp; bumping it on every retry
    # would obscure the actual first-process moment.
    # rowcount==0 means the event row is gone - hard failure, not a
    # silent no-op (would mask a real data-consistency bug).
    backfill = await session.execute(
        text(
            "UPDATE onboarding_events "
            "SET client_id = :client_id, "
            "    processed_at = COALESCE(processed_at, now()) "
            "WHERE id = :event_id"
        ),
        {"client_id": client_id, "event_id": onboarding_event_id},
    )
    if backfill.rowcount == 0:
        raise OnboardingEventNotFoundError(onboarding_event_id)

    # S1-27 layer 2b: claim any sales-call transcripts parked for this client's
    # email (the call precedes signing, so a transcript captured days ago waits
    # unlinked until now). Match is on the lowercased email against the
    # jsonb attendee array; `link_transcript_to_client`'s `WHERE client_id IS
    # NULL` guard makes this replay-safe (a retry re-links nothing). Runs in the
    # same transaction as the client upsert so a partial failure rolls back
    # together; the emits happen after the commit below.
    await _link_parked_transcripts(session, client_id=client_id, email=fields.email)

    # COMMIT BEFORE EMIT. After this point the client row is durable;
    # downstream consumers of `client.created` are guaranteed to find it.
    # If the emit below fails, Inngest retries the whole function; on
    # retry the UPSERT is a no-op, the backfill is idempotent (COALESCE),
    # and the emit gets another shot.
    await session.commit()

    await emitter.send(
        CLIENT_CREATED_EVENT,
        {
            "client_id": str(client_id),
            "onboarding_event_id": str(onboarding_event_id),
            "document_id": document_id,
            "email": fields.email,
            # Propagate the PandaDoc account (S1-25c) so the signed-PDF worker
            # downloads with the matching account's API key.
            "account": account,
            # S1-26c: concurrency key for the GHL returning-client check. Two
            # signings that share an identity must not run the sibling check
            # concurrently (both would see "no sibling" and both create).
            #
            # Falls back through EMAIL before the per-row id. A NULL
            # identity_key still has a DB-side dedup path (the GHL worker's
            # email sibling query), so those signings must still serialise with
            # each other - keying straight to the unique client_id would give
            # every one its own bucket and reopen exactly the concurrent
            # double-create the old `event.data.email` key used to close.
            # `str(client_id)` is the last resort for the degenerate case where
            # even the email is blank. Namespaced so an email can never collide
            # with an identity_key.
            "dedup_key": dedup_key,
        },
    )

    # Emit transcript.linked for this client's signing-linked transcripts AFTER
    # commit. Re-derived from the committed rows (not just the rows linked on
    # THIS pass) so an Inngest retry after a post-commit-pre-emit crash re-emits
    # rather than silently dropping the summary trigger - matching the capture
    # worker's re-derive-on-retry posture. S1-29 must be idempotent per
    # transcript (it can receive a duplicate on a rare create_client_record retry).
    await _emit_signing_transcript_links(emitter, session, client_id)

    log.info(
        "S1-25a client record %s",
        "created" if created else "matched (replay)",
        extra={
            # NOTE: avoid `created` as a key - it collides with
            # LogRecord.created (timestamp) when a logging handler
            # iterates the extra dict.
            "client_id": str(client_id),
            "document_id": document_id,
            "onboarding_event_id": str(onboarding_event_id),
            "row_inserted": created,
            "has_business_name": fields.business_name is not None,
            "has_legal_entity": fields.legal_entity is not None,
            "has_hubspot_contact_id": fields.hubspot_contact_id is not None,
            "has_hubspot_deal_id": fields.hubspot_deal_id is not None,
            "has_template_id": fields.pandadoc_template_id is not None,
            "has_monthly_service_fee": fields.monthly_service_fee is not None,
            "deal_currency": fields.deal_currency,
            # S1-26c: identity_key is NULL when name or postcode is missing;
            # log its presence so a "why did this not match a returning
            # client" question is answerable from production logs.
            "has_identity_key": identity_key is not None,
            # postal_code is now persisted (0013); address/state/country are
            # still extracted-but-not-stored (no columns yet) - logging their
            # presence until a follow-up migration adds them.
            "has_address": fields.address is not None,
            "has_state": fields.state is not None,
            "has_postal_code": fields.postal_code is not None,
            "has_country": fields.country is not None,
        },
    )

    return CreateClientResult(
        client_id=client_id,
        created=created,
        client_created_emitted=True,
    )


@inngest_client.create_function(
    fn_id="create-client-record",
    trigger=inngest.TriggerEvent(event=PANDADOC_SIGNED_EVENT),
    concurrency=[
        inngest.Concurrency(
            # One in-flight invocation per document. Eliminates duplicate
            # PandaDoc fetches + duplicate `client.created` emits when
            # two events fire for the same signing (PandaDoc retries,
            # webhook + reconcile-cron racing, etc.). Different
            # documents process in parallel, unbounded.
            key="event.data.document_id",
            limit=1,
            scope="fn",
        ),
    ],
)
async def create_client_record(ctx: inngest.Context) -> dict:
    """Inngest wrapper: build production deps, fetch document, run the
    core, translate NonRetriable failures.

    The function body is intentionally tiny so the testable seam
    (`create_client_record_core`) carries all the correctness logic.

    Raises:
        inngest.NonRetriableError: a structural data error that cannot
            self-heal on retry: PandaDoc 404, empty API key, missing
            required token (`Client.Email`). Inngest dead-letters
            immediately.
        Other exceptions propagate naturally so Inngest's default retry
        policy can absorb them: httpx 5xx / 429 / timeout, SQLAlchemy
        transient errors, and `OnboardingEventNotFoundError` (which is
        most commonly the producer's emit-before-commit visibility race
        and self-heals on a single retry; persistent orphan still
        dead-letters after retries are exhausted).
    """
    onboarding_event_id = uuid.UUID(ctx.event.data["onboarding_event_id"])
    document_id = str(ctx.event.data["document_id"])
    # PandaDoc account this signing came from (S1-25c). Default to UK for events
    # produced before S1-25c (or any producer that omits it), so in-flight
    # `pandadoc.signed` events keep working through the deploy.
    account = ctx.event.data.get("account", PANDADOC_ACCOUNT_UK)

    settings = get_settings()
    pandadoc_client = HttpPandaDocClient(
        api_key=api_key_for(account, settings),
        base_url=settings.pandadoc_api_base_url,
    )

    # Fetch OUTSIDE the session so the pooled connection is not held
    # during PandaDoc HTTP latency.
    document = await fetch_document_for_orchestrator(pandadoc_client, document_id)

    async with AsyncSessionLocal() as session:
        try:
            result = await create_client_record_core(
                session,
                onboarding_event_id=onboarding_event_id,
                document_id=document_id,
                document=document,
                emitter=InngestEventEmitter(inngest_client),
                account=account,
            )
        except PandaDocPayloadError as exc:
            # Structural data error - retry will not produce the missing
            # token. Dead-letter immediately.
            raise inngest.NonRetriableError(str(exc)) from exc
        # OnboardingEventNotFoundError propagates: Inngest's default
        # retry policy absorbs the producer's emit-before-commit
        # visibility race (the row exists, our separate transaction
        # just cannot see it yet); a persistent orphan still
        # dead-letters after retries are exhausted.

    return {
        "client_id": str(result.client_id),
        "created": result.created,
        "document_id": document_id,
        "account": account,
    }


__all__ = [
    "CreateClientResult",
    "LEGAL_ENTITY_PLACEHOLDER",
    "OnboardingEventNotFoundError",
    "create_client_record",
    "create_client_record_core",
    "fetch_document_for_orchestrator",
]
