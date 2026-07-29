"""Client read API for the dashboard: list (S1-31) + detail (S1-32).

`GET /clients` returns every client with the fields the onboarding board
needs: identity (business / contact + email), `current_step`, the
`step_entered_at` anchor the dashboard turns into time-in-step, and the
status of the client's most-recently-started platform action (the "is the
automation healthy?" signal). One statement, no N+1: a `LEFT JOIN LATERAL`
picks each client's latest `platform_actions` row, ordered
`started_at DESC NULLS LAST, pa.id DESC` because `platform_actions` has no
`created_at` (`started_at` is the only monotonic anchor; `pa.id` breaks ties
so a same-`now()` fan-out batch does not flicker the health badge between
polls). The only new schema for the list is the supporting index on
`clients(step_entered_at DESC, id)` (migration 0009).

`GET /clients/{client_id}` returns the Sprint 1 detail-page payload in one
poll: metadata + platform ids, the latest sales-call summary batch from
`client_knowledge`, and the recent action history. Summary semantics are the
pinned S1-30 contract (PRD §7.1): rows with `source='sales_call'`, one row
per top-level field, and ONLY the most recent `captured_at` batch is returned
(a client can be re-summarised over time; latest capture wins). It needs no
new columns - every field it reads already exists.

Both routes are read-only and open to any authenticated internal user (same
posture as `/me`) - the dashboard is single-tenant.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.auth import CurrentUser, get_current_user
from bullet_api.db import get_session
from bullet_api.schemas import (
    ClientDetailResponse,
    ClientListItem,
    ClientsListResponse,
    KnowledgeEntry,
    PlatformActionItem,
)

clients_router = APIRouter(tags=["clients"])

# Latest platform action per client via LATERAL; `c.id` tiebreaks the step
# ordering so the list is deterministic (matters for tests + stable polling).
_LIST_CLIENTS_SQL = text(
    """
    SELECT
        c.id,
        c.business_name,
        c.contact_first_name,
        c.contact_last_name,
        c.email,
        c.current_step,
        c.step_entered_at,
        c.possible_duplicate,
        la.status   AS last_action_status,
        la.platform AS last_action_platform,
        la.action   AS last_action
    FROM clients c
    LEFT JOIN LATERAL (
        SELECT pa.status, pa.platform, pa.action
        FROM platform_actions pa
        WHERE pa.client_id = c.id
        ORDER BY pa.started_at DESC NULLS LAST, pa.id DESC
        LIMIT 1
    ) la ON true
    ORDER BY c.step_entered_at DESC, c.id
    """
)


def _contact_name(first: str | None, last: str | None) -> str | None:
    """Join first + last into a display name, dropping blanks; None if both empty."""
    parts = [part for part in (first, last) if part]
    return " ".join(parts) if parts else None


@clients_router.get("/clients", response_model=ClientsListResponse)
async def list_clients(
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ClientsListResponse:
    """List every client for the dashboard board. 401 without a live session."""
    result = await db.execute(_LIST_CLIENTS_SQL)
    clients = [
        ClientListItem(
            id=str(row["id"]),
            business_name=row["business_name"],
            contact_name=_contact_name(row["contact_first_name"], row["contact_last_name"]),
            email=row["email"],
            current_step=row["current_step"],
            step_entered_at=row["step_entered_at"],
            possible_duplicate=row["possible_duplicate"],
            last_action_status=row["last_action_status"],
            last_action_platform=row["last_action_platform"],
            last_action=row["last_action"],
        )
        for row in result.mappings().all()
    ]
    return ClientsListResponse(clients=clients)


_GET_CLIENT_SQL = text(
    """
    SELECT
        id, business_name, contact_first_name, contact_last_name, email,
        phone, legal_entity, current_step, step_entered_at, created_at,
        possible_duplicate, possible_duplicate_of,
        hubspot_contact_id, pandadoc_document_id, ghl_contact_id,
        ghl_subaccount_id, asana_project_id, asana_finance_task_id,
        stripe_customer_id, stripe_subscription_id, xero_contact_id,
        timely_client_id, timely_project_id, meta_ad_account_id,
        drive_folder_id, sheet_row_id, slack_thread_ts
    FROM clients
    WHERE id = :client_id
    """
)

# Latest-capture-wins: only the most recent captured_at batch of sales-call
# knowledge rows is the current summary (re-summarising writes a fresh batch;
# the page must never mix fields from two different captures). Served by
# ix_client_knowledge_client_id_source.
#
# DISTINCT ON (key) ... ORDER BY key, id DESC guarantees exactly one row per
# key even if the latest captured_at ever ties across two batches. id is a
# random uuid, so this is a DETERMINISTIC dedup, not a true newest-batch pick:
# it removes the duplicate-key render and the non-deterministic "which row
# wins" the bare `= max()` exposed, but FULL cross-batch isolation on a
# captured_at tie would need a monotonic batch key the writer owns.
# RESOLVED (S1-30, 02/07/2026): a monotonic batch key was judged unnecessary and
# NOT added. Two DISTINCT batches for one client tying on captured_at requires
# two different summaries (a corrected re-link) or two transcripts landing in the
# SAME microsecond - and S1-30's platform_actions idempotency (keyed on
# transcript_id + summary hash) already prevents same-summary duplication. At
# Bullet's human-paced sales-call volume a microsecond tie is unreachable, so
# this DISTINCT ON dedup stays a belt-and-braces no-op on the normal path.
#
# CONTRACT (S1-30 writer side): a summary batch MUST be inserted in ONE
# transaction with ONE shared captured_at value. The single-statement read
# below shares one snapshot, so an atomic batch is always seen whole - but a
# per-row-committing writer could expose a PARTIAL latest batch mid-write.
# Both seeds comply; S1-30's implementation must too.
_GET_SUMMARY_SQL = text(
    """
    SELECT DISTINCT ON (key) key, value, value_text, captured_at
    FROM client_knowledge
    WHERE client_id = :client_id
      AND source = 'sales_call'
      AND captured_at = (
          SELECT max(captured_at)
          FROM client_knowledge
          WHERE client_id = :client_id AND source = 'sales_call'
      )
    ORDER BY key, id DESC
    """
)

# Recent action history, newest first. Same started_at anchor as the list
# view; LIMIT keeps the payload bounded for the 5s poll (silent top-20 cap -
# acceptable for Sprint 1 volumes; a "showing N of M" affordance is a logged
# follow-up for when the Sprint 2 fan-outs multiply rows per client).
# NULL started_at sorts last: today unreachable in production (begin_action
# always stamps started_at = now()) but kept correct for safety.
_GET_ACTIONS_SQL = text(
    """
    SELECT
        platform, action, status, external_id, retry_count, last_error,
        inngest_run_id, started_at, completed_at
    FROM platform_actions
    WHERE client_id = :client_id
    ORDER BY started_at DESC NULLS LAST, id
    LIMIT 20
    """
)


@clients_router.get("/clients/{client_id}", response_model=ClientDetailResponse)
async def get_client_detail(
    client_id: str,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ClientDetailResponse:
    """Detail-page payload for one client. 404 for unknown OR malformed ids
    (a non-UUID path segment is just an id we will never have - not a server
    error and not worth a distinct status), 401 without a live session."""
    try:
        parsed_id = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        ) from None

    client_row = (await db.execute(_GET_CLIENT_SQL, {"client_id": parsed_id})).mappings().first()
    if client_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    summary_rows = (await db.execute(_GET_SUMMARY_SQL, {"client_id": parsed_id})).mappings().all()
    action_rows = (await db.execute(_GET_ACTIONS_SQL, {"client_id": parsed_id})).mappings().all()

    return ClientDetailResponse(
        id=str(client_row["id"]),
        business_name=client_row["business_name"],
        contact_name=_contact_name(
            client_row["contact_first_name"], client_row["contact_last_name"]
        ),
        email=client_row["email"],
        phone=client_row["phone"],
        legal_entity=client_row["legal_entity"],
        current_step=client_row["current_step"],
        step_entered_at=client_row["step_entered_at"],
        created_at=client_row["created_at"],
        possible_duplicate=client_row["possible_duplicate"],
        possible_duplicate_of=(
            str(client_row["possible_duplicate_of"])
            if client_row["possible_duplicate_of"] is not None
            else None
        ),
        sales_summary=[
            KnowledgeEntry(
                key=row["key"],
                value=row["value"],
                value_text=row["value_text"],
                captured_at=row["captured_at"],
            )
            for row in summary_rows
        ],
        actions=[PlatformActionItem(**dict(row)) for row in action_rows],
        hubspot_contact_id=client_row["hubspot_contact_id"],
        pandadoc_document_id=client_row["pandadoc_document_id"],
        ghl_contact_id=client_row["ghl_contact_id"],
        ghl_subaccount_id=client_row["ghl_subaccount_id"],
        asana_project_id=client_row["asana_project_id"],
        asana_finance_task_id=client_row["asana_finance_task_id"],
        stripe_customer_id=client_row["stripe_customer_id"],
        stripe_subscription_id=client_row["stripe_subscription_id"],
        xero_contact_id=client_row["xero_contact_id"],
        timely_client_id=client_row["timely_client_id"],
        timely_project_id=client_row["timely_project_id"],
        meta_ad_account_id=client_row["meta_ad_account_id"],
        drive_folder_id=client_row["drive_folder_id"],
        sheet_row_id=client_row["sheet_row_id"],
        slack_thread_ts=client_row["slack_thread_ts"],
    )
