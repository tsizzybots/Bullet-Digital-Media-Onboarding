"""Shared Pydantic response/request models for the public API surface.

These exist so FastAPI emits a precise OpenAPI schema for every endpoint
(named components with typed properties) rather than the loose
`additionalProperties` object you get from a bare `dict[str, str]` return.
The S1-17 codegen pipeline turns these schemas into exact TypeScript types
(`{ status: string }` instead of `Record<string, string>`), which is what
lets the dashboard consume the API without `any`.

Adding `response_model=` to an existing endpoint is purely additive: the
JSON sent on the wire is unchanged; only the documented schema gains
precision.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr


class StatusResponse(BaseModel):
    """Generic `{"status": "..."}` envelope.

    Reused by the auth endpoints (login / logout / confirm /
    resend-confirmation) which all report progress through a single
    `status` string (`ok`, `confirmed`, `already_confirmed`,
    `sent_if_unconfirmed`).
    """

    status: str


class HealthzResponse(BaseModel):
    """Liveness probe payload - `{"status": "ok"}`."""

    status: str


class VersionResponse(BaseModel):
    """`/version` payload - the running API version string."""

    version: str


class MeResponse(BaseModel):
    """Authenticated user profile returned by `/me`.

    `id` is serialised as a string (the handler stringifies the UUID) so
    the generated client treats it as an opaque identifier.
    """

    id: str
    email: str
    full_name: str
    role: str


class AdminPingResponse(BaseModel):
    """`/admin/ping` payload - the `require_founder` smoke endpoint."""

    status: str
    email: str


class ResendConfirmationRequest(BaseModel):
    """Body for `POST /auth/resend-confirmation`.

    Replaces the previous bare `dict[str, str]` so the email is validated
    and the request schema is precise in the generated client.
    """

    email: EmailStr


class ClientListItem(BaseModel):
    """One row of the dashboard `/clients` list view (S1-31).

    `id` is stringified (opaque identifier, same as `MeResponse`).
    `step_entered_at` is the time the client entered `current_step`; the
    dashboard derives the displayed time-in-step from it client-side so the
    duration ticks without a refetch. `last_action_*` describe the client's
    most-recently-started `platform_actions` row (the onboarding-health
    signal); all three are null for a client with no recorded actions yet.
    """

    id: str
    business_name: str | None
    contact_name: str | None
    email: str
    current_step: str
    step_entered_at: datetime
    last_action_status: str | None
    last_action_platform: str | None
    last_action: str | None


class ClientsListResponse(BaseModel):
    """Envelope for `GET /clients` - every client, newest step-change first."""

    clients: list[ClientListItem]
