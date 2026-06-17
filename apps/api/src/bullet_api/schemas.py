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
from typing import Any

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
    dashboard derives the displayed time-in-step from it client-side (recomputed
    on each poll). `last_action_*` describe the client's
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


class KnowledgeEntry(BaseModel):
    """One `client_knowledge` row surfaced on the detail page (S1-32).

    `key` is a PRD §7.1 top-level field name (`business_type`,
    `business_goals`, `budget_range_usd`, `pain_points`, `red_flags`,
    `next_steps`, `notable_quotes`) - the pinned contract S1-30 writes to.
    `value` is the §7.1 JSONB shape for that key; `value_text` is the prose
    rendering the dashboard can fall back to. The dashboard renders unknown
    keys generically, so additions to §7.1 never break the page.
    """

    key: str
    value: Any
    value_text: str | None
    captured_at: datetime


class PlatformActionItem(BaseModel):
    """One `platform_actions` row in the detail page's action history.

    `inngest_run_id` is returned raw; the dashboard builds the Inngest UI
    deep-link from its own `NEXT_PUBLIC_INNGEST_URL` config (URL policy is
    presentation, not API contract).
    """

    platform: str
    action: str
    status: str
    external_id: str | None
    retry_count: int
    last_error: str | None
    inngest_run_id: str | None
    started_at: datetime | None
    completed_at: datetime | None


class ClientDetailResponse(BaseModel):
    """`GET /clients/{id}` - the Sprint 1 detail page payload (S1-32).

    One payload feeds the whole page (one 5s poll): metadata, step,
    the latest sales-call summary batch from `client_knowledge` (empty list
    until S1-27/29/30 produce one - the dashboard's "no summary yet" state),
    the platform ids the deep-link grid is built from (null = not connected,
    rendered as a placeholder chip), and the recent action history.
    """

    id: str
    business_name: str | None
    contact_name: str | None
    email: str
    phone: str | None
    legal_entity: str
    current_step: str
    step_entered_at: datetime
    created_at: datetime
    sales_summary: list[KnowledgeEntry]
    actions: list[PlatformActionItem]
    hubspot_contact_id: str | None
    pandadoc_document_id: str | None
    ghl_contact_id: str | None
    ghl_subaccount_id: str | None
    asana_project_id: str | None
    asana_finance_task_id: str | None
    stripe_customer_id: str | None
    stripe_subscription_id: str | None
    xero_contact_id: str | None
    timely_client_id: str | None
    timely_project_id: str | None
    meta_ad_account_id: str | None
    drive_folder_id: str | None
    sheet_row_id: str | None
    slack_thread_ts: str | None


class UnlinkedTranscriptItem(BaseModel):
    """One parked sales-call transcript awaiting a client (S1-27 manual fallback).

    These are the ~10% the auto-link-by-email path misses. `participant_emails`
    are the invite attendees (lowercased) the team uses to pick the right client;
    `meeting_start` + `transcript_chars` help them recognise the call.
    """

    id: str
    source: str
    participant_emails: list[str]
    meeting_start: datetime | None
    transcript_chars: int | None
    captured_at: datetime


class UnlinkedTranscriptsResponse(BaseModel):
    """Envelope for `GET /transcripts/unlinked` - parked transcripts, newest first."""

    transcripts: list[UnlinkedTranscriptItem]


class LinkTranscriptRequest(BaseModel):
    """Body for `POST /transcripts/{id}/link` - the client to attach to."""

    client_id: str


class LinkTranscriptResponse(BaseModel):
    """Result of a manual attach: the linked transcript + the documents row it
    created (null only in the rare case the transcript text was not yet stored)."""

    transcript_id: str
    client_id: str
    document_id: str | None
