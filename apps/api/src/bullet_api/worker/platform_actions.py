"""Shared audit recorder for the `platform_actions` table.

Every onboarding fan-out (S1-25 GHL sub-account, S1-25b signed-PDF
storage, S2-* Asana / Stripe / Xero / Timely) records its attempt against
the same `platform_actions` table the same way: write an `in_progress`
row keyed on an idempotency key BEFORE the external call, then mark it
`success` (with the external id + response body) or `failed` (with the
error, bumping `retry_count`). This module is that one tested recorder so
each fan-out does not re-implement the `ON CONFLICT` + status-transition
SQL slightly differently and drift the audit trail.

The idempotency-key format and status lifecycle are the contract fixed in
migration `0005_create_platform_actions`:

- `idempotency_key = "{client_id}:{platform}:{action}:{event_id}"`, UNIQUE.
  A retried Inngest run re-derives the same key, so the INSERT conflicts and
  `ON CONFLICT ... DO UPDATE` (a no-op SET) returns the existing row to
  resume from. The no-op UPDATE is used over `DO NOTHING` so RETURNING
  always yields the surviving row in one round-trip - even when a concurrent
  duplicate is still uncommitted, where a `DO NOTHING` + separate SELECT
  would see no row under READ COMMITTED.
- `status` flows pending/in_progress -> success | failed | dead_lettered.
  This recorder uses `in_progress` on begin, `success` on completion, and
  `failed` on error.

Transaction control stays with the caller (the orchestrator commits at
the points that make its retry semantics correct), matching the
`create_client_record_core` style: this module only issues the
INSERT/UPDATE statements, never `commit()`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# platform_action_status values that matter to the recorder. Sourced from
# 0005_create_platform_actions.STATUS_VALUES.
STATUS_IN_PROGRESS = "in_progress"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


def build_idempotency_key(
    client_id: uuid.UUID,
    platform: str,
    action: str,
    event_id: uuid.UUID,
    *extra: str,
) -> str:
    """Build the canonical `{client_id}:{platform}:{action}:{event_id}` key.

    Centralised so every fan-out derives the same key for the same logical
    action and the UNIQUE constraint actually dedupes retries.

    `*extra` appends further colon-separated parts for actions whose identity
    is not fully captured by `event_id` alone. S1-29 passes the transcript's
    `r2_key` so that a re-link to a *corrected* transcript (same transcript_id,
    new object) produces a NEW key and resummarises, rather than replay-
    short-circuiting on the stale original summary. Existing callers pass no
    extra parts, so their key is unchanged.
    """
    parts = [str(client_id), platform, action, str(event_id), *extra]
    return ":".join(parts)


@dataclass(frozen=True)
class BeginActionResult:
    """Outcome of `begin_action`.

    `action_id` is the surviving `platform_actions` row id (whether freshly
    inserted or matched on conflict). `status` is that row's current
    status. `already_succeeded` is the convenience flag callers branch on
    to skip the external call entirely on a replay of an already-completed
    action.

    `inserted` is True when THIS call inserted the row (no conflict) and False
    when it matched an existing row on conflict - so a caller can tell "I
    claimed this" from "someone got here first". `started_at` is the row's
    current start timestamp. Together they let a caller detect a concurrent
    live run (`not inserted and status == 'in_progress'`) and decide whether
    to back off (fresh) or reclaim (stale) - see S1-29's concurrency guard.
    """

    action_id: uuid.UUID
    status: str
    already_succeeded: bool
    inserted: bool
    started_at: datetime


async def begin_action(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    event_id: uuid.UUID | None,
    platform: str,
    action: str,
    idempotency_key: str,
    payload: dict | None = None,
    inngest_run_id: str | None = None,
) -> BeginActionResult:
    """Record (or resume) a fan-out action as `in_progress`.

    `INSERT ... ON CONFLICT (idempotency_key) DO UPDATE SET idempotency_key
    = EXCLUDED.idempotency_key RETURNING id, status`. The no-op SET (re-sets
    the key to itself, leaving every real column - including `status` -
    untouched) makes RETURNING populate the surviving row on BOTH a fresh
    insert and a conflict, in a single round-trip. On conflict the statement
    also takes a row lock that blocks until any concurrent inserter commits,
    so a racing duplicate reads the committed row rather than risking the
    empty read-back a `DO NOTHING` + separate SELECT can hit under READ
    COMMITTED.

    `payload` is the request body we are about to send, persisted on the
    JSONB `payload` column for auditing; pass None to leave it NULL.
    """
    inserted = await session.execute(
        text(
            "INSERT INTO platform_actions ("
            "  client_id, event_id, platform, action, idempotency_key,"
            "  status, payload, inngest_run_id, started_at"
            ") VALUES ("
            "  :client_id, :event_id, :platform, :action, :idempotency_key,"
            "  :status, cast(:payload AS jsonb), :inngest_run_id, now()"
            ") "
            "ON CONFLICT (idempotency_key) DO UPDATE "
            "  SET idempotency_key = EXCLUDED.idempotency_key "
            # (xmax = 0) is true only for a freshly INSERTed row; a row reached
            # via the ON CONFLICT DO UPDATE branch has a non-zero xmax. This is
            # how the caller tells "I inserted this" from "I matched an existing
            # row", without a second round-trip.
            "RETURNING id, status, (xmax = 0) AS inserted, started_at"
        ),
        {
            "client_id": client_id,
            "event_id": event_id,
            "platform": platform,
            "action": action,
            "idempotency_key": idempotency_key,
            "status": STATUS_IN_PROGRESS,
            "payload": json.dumps(payload) if payload is not None else None,
            "inngest_run_id": inngest_run_id,
        },
    )
    row = inserted.one()

    return BeginActionResult(
        action_id=row.id,
        status=row.status,
        already_succeeded=row.status == STATUS_SUCCESS,
        inserted=row.inserted,
        started_at=row.started_at,
    )


async def complete_action(
    session: AsyncSession,
    *,
    action_id: uuid.UUID,
    external_id: str | None,
    response: dict | None = None,
) -> None:
    """Mark an action `success` with the external id + response body.

    `completed_at` is stamped now. `external_id` is the resource id the
    external platform returned (e.g. the GHL sub-account id), surfaced in
    the dashboard and copied onto the relevant `clients.*` column by the
    caller. `last_error` is cleared: an action that previously `failed`
    (e.g. a create whose response was lost, recovered on a later retry by
    S1-26's lookup-and-reuse) must not keep a stale error on the now-success
    row, or the dashboard shows a green action carrying an error string.
    """
    await session.execute(
        text(
            "UPDATE platform_actions "
            "SET status = :status, external_id = :external_id, "
            "    response = cast(:response AS jsonb), completed_at = now(), "
            "    last_error = NULL "
            "WHERE id = :action_id"
        ),
        {
            "status": STATUS_SUCCESS,
            "external_id": external_id,
            "response": json.dumps(response) if response is not None else None,
            "action_id": action_id,
        },
    )


async def fail_action(
    session: AsyncSession,
    *,
    action_id: uuid.UUID,
    last_error: str,
) -> None:
    """Mark an action `failed`, record `last_error`, bump `retry_count`.

    `retry_count` is incremented in-place so the dashboard shows how many
    attempts a flapping action has burned. `completed_at` is deliberately
    left NULL on failure - the action is not done, it will be retried.
    """
    await session.execute(
        text(
            "UPDATE platform_actions "
            "SET status = :status, last_error = :last_error, "
            "    retry_count = retry_count + 1 "
            "WHERE id = :action_id"
        ),
        {
            "status": STATUS_FAILED,
            "last_error": last_error,
            "action_id": action_id,
        },
    )


__all__ = [
    "STATUS_FAILED",
    "STATUS_IN_PROGRESS",
    "STATUS_SUCCESS",
    "BeginActionResult",
    "begin_action",
    "build_idempotency_key",
    "complete_action",
    "fail_action",
]
