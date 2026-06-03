"""Slack incoming-webhook notifier for the PandaDoc reconciliation cron (S1-23).

The daily reconciliation cron compares signed PandaDoc documents against the
onboarding events the live webhook actually produced. When it finds a signed
document the webhook never delivered, it creates the missing event and posts a
Slack alert so the team knows a webhook was dropped and was healed after the
fact. This module owns only the Slack side of that flow:

- `SlackNotifier` - the Protocol the cron orchestrator depends on, so it never
  binds to Slack specifically (a future PagerDuty / email notifier can drop in).
- `HttpSlackNotifier` - production wiring. It POSTs `{"text": ...}` to a Slack
  incoming-webhook URL. Disabled-by-default: when `webhook_url` is empty (local
  dev, CI, the test suite, or an unconfigured env group) `post()` logs a warning
  and makes NO HTTP call, mirroring the `empty -> no-op` pattern used by the
  Sentry init (`bullet_api.observability.sentry`) and the PandaDoc secret guard.
- `FakeSlackNotifier` - test double that records posted text for assertions.
- `format_reconciliation_alert` - a PURE function that builds the alert text.

`format_reconciliation_alert` is decoupled from the PandaDoc client slice on
purpose: `PandaDocDocument` is imported only under `TYPE_CHECKING`, and at
runtime the formatter duck-types on `.document_id` / `.name` (and the optional
`.date_completed`). That keeps this module independent of the parallel
`bullet_api.integrations.pandadoc_client` slice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from bullet_api.integrations.pandadoc_client import PandaDocDocument

log = logging.getLogger(__name__)


class SlackNotifier(Protocol):
    """Posts a single line of text to a Slack channel.

    The cron orchestrator depends on this Protocol rather than on
    `HttpSlackNotifier` directly, so the notifier can be swapped (or faked in
    tests) without touching the orchestration code.
    """

    async def post(self, text: str) -> None:
        """Post `text` to the configured Slack destination."""
        ...


class HttpSlackNotifier:
    """Production notifier - POSTs to a Slack incoming-webhook URL.

    Disabled-by-default: when `webhook_url` is empty (local dev, CI, tests, or
    an unconfigured Render env group) `post()` logs a warning and returns
    WITHOUT creating an httpx client or making any request. This mirrors the
    `empty -> no-op` pattern already used by the Sentry init and PandaDoc
    secret guard, so a missing `SLACK_WEBHOOK_URL` degrades quietly instead of
    erroring the reconciliation cron.

    Accepts an optional httpx transport so tests can inject a `MockTransport`
    without a network call (the only testability hook; production passes None
    and httpx uses its default transport).
    """

    def __init__(
        self,
        webhook_url: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._timeout = timeout
        self._transport = transport

    async def post(self, text: str) -> None:
        if not self._webhook_url:
            log.warning(
                "SLACK_WEBHOOK_URL is empty; skipping Slack post (no-op). "
                "Set it on the Render env group to enable reconciliation alerts."
            )
            return
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.post(self._webhook_url, json={"text": text})
        response.raise_for_status()


@dataclass
class FakeSlackNotifier:
    """Test double. Records every posted line in `posted` for assertions."""

    posted: list[str] = field(default_factory=list)

    async def post(self, text: str) -> None:
        self.posted.append(text)


def format_reconciliation_alert(doc: PandaDocDocument) -> str:
    """Build the Slack alert text for a signed document the webhook missed.

    PURE: no I/O, no side effects. Duck-types on `doc.document_id` and
    `doc.name` (and the optional `doc.date_completed`) so it stays decoupled
    from the PandaDoc client slice. The message states plainly that the live
    webhook never delivered this signed document and that the daily
    reconciliation cron created the missing onboarding event, and it includes
    the document id and name so the team can trace it in PandaDoc.
    """
    completed = getattr(doc, "date_completed", None)
    completed_suffix = f" (completed {completed})" if completed else ""
    return (
        ":warning: PandaDoc webhook missed a signed document. "
        f'The reconciliation cron created the missing onboarding event for "{doc.name}" '
        f"(document id `{doc.document_id}`){completed_suffix}. "
        "The live webhook did not deliver this signing; the event was healed after the fact."
    )
