"""`GET /clients` - the dashboard list view (S1-31).

Returns every client with the fields the onboarding board needs: identity
(business / contact + email), `current_step`, the `step_entered_at` anchor
the dashboard turns into time-in-step, and the status of the client's
most-recently-started platform action (the "is the automation healthy?"
signal). Read-only; any authenticated internal user may call it (same posture
as `/me`) - the dashboard is single-tenant.

One statement, no N+1: a `LEFT JOIN LATERAL` picks each client's latest
`platform_actions` row. `LEFT` so a client with zero actions still appears
(null `last_action_*`). The lateral orders by `started_at DESC NULLS LAST,
pa.id DESC` because `platform_actions` has no `created_at` - `started_at` is
the only monotonic anchor (an action stamps it when it begins; a never-started
row sorts last), and `pa.id` breaks ties so a same-`now()` fan-out batch (or
all-NULL `started_at`) does not flicker the health badge between polls.
The only new schema for this view is the supporting index on
`clients(step_entered_at DESC, id)` (migration 0009); the columns themselves
(`clients.step_entered_at`, `platform_actions.status`) already exist.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.auth import CurrentUser, get_current_user
from bullet_api.db import get_session
from bullet_api.schemas import ClientListItem, ClientsListResponse

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
            last_action_status=row["last_action_status"],
            last_action_platform=row["last_action_platform"],
            last_action=row["last_action"],
        )
        for row in result.mappings().all()
    ]
    return ClientsListResponse(clients=clients)
