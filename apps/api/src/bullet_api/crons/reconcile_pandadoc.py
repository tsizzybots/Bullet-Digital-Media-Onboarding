"""PandaDoc daily reconciliation cron (S1-23).

The PandaDoc webhook (S1-22, ``POST /webhooks/pandadoc``) is the primary path
that turns a signing into an ``onboarding_events`` row and a ``pandadoc.signed``
event. Webhooks are at-least-once but not guaranteed: a delivery can fail, a
deploy can be mid-flight, the shared secret can be briefly misconfigured. If a
signed document never lands an event row, the whole downstream fan-out (Slack,
Asana, Stripe, ...) silently never fires for that client.

This nightly cron is the safety net. It asks the PandaDoc API for documents
completed within a rolling look-back window and, for any with no
``onboarding_events`` row, inserts the missing (synthetic) event, emits the
same ``pandadoc.signed`` event the webhook would have, and posts a Slack alert
so the team knows a webhook was dropped and healed after the fact.

Correctness rules (mirroring the webhook so the two stay in lockstep):

- The insert is the canonical idempotency contract from
  ``bullet_api.webhooks.pandadoc`` - ``INSERT ... ON CONFLICT
  (event_type, external_id) DO NOTHING RETURNING id``. A document already
  present (webhook delivered it, or a prior reconcile created it) hits
  ON CONFLICT, returns no id, and is skipped: no emit, no alert. That makes a
  run with nothing new a pure no-op, and the whole job idempotent.
- ``verified_at`` is stamped ``now()`` in the insert: the document came from an
  authenticated PandaDoc API call, so it is trusted and *must* fan out exactly
  like a signature-verified webhook (a future verified-only orchestrator must
  not skip a reconciled signing). The synthetic ``payload`` is tagged
  ``source = "pandadoc_reconciliation"`` so the row is auditable as healed.
- The session is committed only AFTER the emit(s)/alert(s) succeed, matching
  the webhook: a mid-batch failure rolls the batch back and the next nightly
  run re-runs cleanly (the unique constraint prevents duplicate rows).

Rolling look-back rather than a persisted cursor: over-fetching recent
documents is free because the unique constraint dedupes, so a generous window
is self-healing and can never "advance past" an undelivered document.

This module deliberately uses ``bullet_api.integrations.pandadoc_client`` (the
list-documents client) rather than ``bullet_api.pandadoc.client`` (the S1-24
single-document client); folding the two into one PandaDoc package is a
tracked follow-up once both cards land.

Run locally (needs DATABASE_URL; no PandaDoc account key configured -> disabled no-op):

    uv run python -m bullet_api.crons.reconcile_pandadoc
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.integrations.pandadoc_client import PandaDocClient, PandaDocDocument
from bullet_api.integrations.slack import SlackNotifier, format_reconciliation_alert
from bullet_api.pandadoc.accounts import PANDADOC_ACCOUNT_UK
from bullet_api.worker import PANDADOC_SIGNED_EVENT, EventEmitter

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of one reconciliation pass.

    ``created`` counts synthetic rows inserted, which equals the number of
    Inngest events emitted and Slack alerts posted (one per healed document).
    """

    checked: int
    created: int


def _synthetic_payload(doc: PandaDocDocument) -> dict:
    """Build the ``onboarding_events.payload`` for a reconciled document.

    Webhook-shaped (an ``event`` + ``data`` envelope) so downstream consumers
    can read it like a real webhook event, but tagged ``source`` so it is
    auditable as reconstructed by the cron rather than delivered live. The full
    PandaDoc list result is kept under ``pandadoc_document`` so nothing is lost.
    """
    return {
        "event": "reconciliation.synthetic",
        "source": "pandadoc_reconciliation",
        "data": {
            "id": doc.document_id,
            "name": doc.name,
            "status": doc.status or "document.completed",
            "date_completed": doc.date_completed,
        },
        "pandadoc_document": doc.raw,
    }


async def reconcile_pandadoc(
    session: AsyncSession,
    *,
    pandadoc_client: PandaDocClient,
    emitter: EventEmitter,
    slack: SlackNotifier,
    lookback_days: int,
    now: datetime,
    account: str = PANDADOC_ACCOUNT_UK,
) -> ReconcileResult:
    """Reconcile completed PandaDoc documents against ``onboarding_events``.

    Lists documents completed since ``now - lookback_days`` and, for each that
    has no event row yet, inserts the synthetic event, emits ``pandadoc.signed``
    and posts a Slack alert. Commits once at the end (after all emits/alerts),
    mirroring the webhook. ``now`` is injected so callers/tests control the
    look-back watermark deterministically.

    ``account`` is the PandaDoc account ``pandadoc_client`` lists from (S1-25c);
    it is stamped on ``onboarding_events.pandadoc_account`` and carried on the
    emitted ``pandadoc.signed`` event so the healed signing fans out with the
    matching account's API key. ``_run`` calls this once per configured account.
    """
    completed_from = now - timedelta(days=lookback_days)
    documents = await pandadoc_client.list_completed_documents(completed_from)

    created = 0
    for doc in documents:
        result = await session.execute(
            text(
                "INSERT INTO onboarding_events "
                "  (event_type, external_id, payload, pandadoc_account, verified_at) "
                "VALUES (:et, :eid, cast(:p AS jsonb), :acct, now()) "
                "ON CONFLICT (event_type, external_id) DO NOTHING "
                "RETURNING id"
            ),
            # Canonical contract: bullet_api/webhooks/pandadoc.py
            # (persist_and_emit_signed_documents). Replicated here so the cron
            # can interleave a per-document Slack alert the webhook does not.
            {
                "et": PANDADOC_SIGNED_EVENT,
                "eid": doc.document_id,
                "p": json.dumps(_synthetic_payload(doc)),
                "acct": account,
            },
        )
        new_id = result.scalar()
        if new_id is None:
            # Already present: webhook delivered it, or a prior reconcile
            # created it. No emit, no alert - this is the no-op path.
            continue
        await emitter.send(
            PANDADOC_SIGNED_EVENT,
            {
                "document_id": doc.document_id,
                "onboarding_event_id": str(new_id),
                "account": account,
            },
        )
        await slack.post(f"[{account.upper()}] {format_reconciliation_alert(doc)}")
        created += 1

    # Commit AFTER the emit(s)/alert(s) succeed, like the webhook: a failure
    # propagates, the session rolls back, no row persists, and tonight's run is
    # retried on the next nightly pass (the unique constraint keeps it clean).
    await session.commit()

    log.info(
        "pandadoc reconciliation pass complete",
        extra={
            "checked": len(documents),
            "created_count": created,
            "completed_from": completed_from.isoformat(),
        },
    )
    return ReconcileResult(checked=len(documents), created=created)


async def _run() -> ReconcileResult:
    """Wire production dependencies from settings and run one pass PER ACCOUNT.

    S1-25c: Bullet runs two PandaDoc accounts (UK + International), each with
    its own API key. We reconcile each configured account in turn (skipping any
    whose key is empty), building a list-client per account so the right
    account-scoped key is used, and accumulate the totals. A single shared
    session + look-back window covers all accounts; per-account stamping keeps
    each healed row + event tagged with the account it came from.

    Each per-account `reconcile_pandadoc` call commits at its end, so the
    accounts are NOT all-or-nothing: if the second account raises, the first's
    healed rows stay committed and only the failed account is re-processed on
    the next nightly run (idempotent via the unique constraint). That is the
    desired behaviour for a healing cron - a transient INT failure must not
    roll back already-healed UK signings.
    """
    # Production-only wiring imported on the entry path, so the testable
    # `reconcile_pandadoc` core's module-level imports stay limited to its direct
    # collaborators (the Protocols, the event constant) rather than the concrete
    # HTTP clients / DB engine the cron process needs.
    from bullet_api.config import get_settings
    from bullet_api.db import AsyncSessionLocal
    from bullet_api.integrations.pandadoc_client import HttpPandaDocClient
    from bullet_api.integrations.slack import HttpSlackNotifier
    from bullet_api.pandadoc.accounts import api_accounts
    from bullet_api.worker import InngestEventEmitter, inngest_client

    settings = get_settings()
    emitter = InngestEventEmitter(inngest_client)
    slack = HttpSlackNotifier(settings.slack_webhook_url)
    now = datetime.now(UTC)

    checked = 0
    created = 0
    async with AsyncSessionLocal() as session:
        for creds in api_accounts(settings):
            client = HttpPandaDocClient(
                api_key=creds.api_key,
                base_url=settings.pandadoc_api_base_url,
            )
            result = await reconcile_pandadoc(
                session,
                pandadoc_client=client,
                emitter=emitter,
                slack=slack,
                lookback_days=settings.reconciliation_lookback_days,
                now=now,
                account=creds.account,
            )
            checked += result.checked
            created += result.created
    return ReconcileResult(checked=checked, created=created)


def main() -> None:
    """CLI entry point for the Render cron service.

    Disabled-by-default: when NO PandaDoc account has an API key the job logs
    and exits 0 without calling PandaDoc, so an unconfigured deployment is a
    safe no-op rather than a crash. (S1-25c: checks every configured account,
    not just a single key.)
    """
    from bullet_api.config import get_settings
    from bullet_api.logging_config import configure_logging
    from bullet_api.pandadoc.accounts import api_accounts

    configure_logging()
    if not api_accounts(get_settings()):
        log.warning("No PandaDoc API key configured; reconciliation disabled, exiting 0.")
        return
    result = asyncio.run(_run())
    log.info(
        "pandadoc reconciliation finished",
        extra={"checked": result.checked, "created_count": result.created},
    )


if __name__ == "__main__":
    main()
