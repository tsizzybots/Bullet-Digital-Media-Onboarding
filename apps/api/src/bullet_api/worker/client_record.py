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
  an empty `PANDADOC_API_KEY` are likewise wrapped as NonRetriable.
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
from bullet_api.db.enums import CURRENT_STEP_SIGNED
from bullet_api.db.session import AsyncSessionLocal
from bullet_api.pandadoc.client import HttpPandaDocClient, PandaDocClient, PandaDocNotFound
from bullet_api.worker._inngest import inngest_client
from bullet_api.worker.clients_payload import (
    PandaDocPayloadError,
    extract_client_fields,
)
from bullet_api.worker.events import (
    CLIENT_CREATED_EVENT,
    PANDADOC_SIGNED_EVENT,
    EventEmitter,
    InngestEventEmitter,
)

log = logging.getLogger(__name__)


# Placeholder used when the legal-trading-name form field is missing AND
# no business_name token was extracted either. The clients table requires
# legal_entity NOT NULL, and we deliberately keep the orchestrator
# insertable rather than dead-lettering on a soft field: the placeholder
# is obvious in the dashboard so the team can correct it manually. Only
# ever written to `legal_entity` - never to `business_name`, which is
# NULL-able and stays NULL when missing.
LEGAL_ENTITY_PLACEHOLDER = "Unknown - needs review"


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
    PANDADOC_API_KEY - mis-configured deploy) both indicate failures
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


async def create_client_record_core(
    session: AsyncSession,
    *,
    onboarding_event_id: uuid.UUID,
    document_id: str,
    document: dict,
    emitter: EventEmitter,
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

    # ON CONFLICT DO UPDATE with a no-op SET so RETURNING id populates on
    # both insert and conflict. current_step is set only on INSERT (ON
    # CONFLICT path does not touch it); a replay must not regress a
    # downstream step.
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
            "  current_step"
            ") VALUES ("
            "  :email, :business_name, :legal_entity,"
            "  :contact_first_name, :contact_last_name, :phone,"
            "  :pandadoc_document_id, :hubspot_contact_id,"
            "  :current_step"
            ") "
            "ON CONFLICT (pandadoc_document_id) DO UPDATE "
            "  SET pandadoc_document_id = EXCLUDED.pandadoc_document_id "
            "RETURNING id, (xmax = 0) AS inserted"
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
            "current_step": CURRENT_STEP_SIGNED,
        },
    )
    client_row = upserted.one()
    client_id: uuid.UUID = client_row.id
    created: bool = bool(client_row.inserted)

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
        },
    )

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
            # Address fields are extracted but not yet persisted (no
            # columns on `clients` for them); logging presence here so
            # they are visible in production logs until the follow-up
            # migration adds the columns.
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
async def create_client_record(ctx: inngest.Context, step: inngest.Step) -> dict:
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

    settings = get_settings()
    pandadoc_client = HttpPandaDocClient(
        api_key=settings.pandadoc_api_key,
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
    }


__all__ = [
    "CreateClientResult",
    "LEGAL_ENTITY_PLACEHOLDER",
    "OnboardingEventNotFoundError",
    "create_client_record",
    "create_client_record_core",
    "fetch_document_for_orchestrator",
]
