# Phase 1 Product Requirements Document

**Project**: Bullet Digital Media x IzzyAgents - Onboarding Process Automation
**Phase**: 1 (Months 1-2)
**Date**: 03/05/2026
**Status**: v1.0 - Initial PRD
**Plan source**: `docs/phase-1-plan.md` v3.2 (locked)

This PRD operationalises the Phase 1 plan into concrete product requirements. Read it alongside the plan, not instead of it. The plan answers "what are we building and why"; this PRD answers "exactly how, with which contracts, against which acceptance criteria".

---

## 1. Product Context and Goals

### 1.1 What this product is

An internal-facing operations platform for Bullet Digital Media. It listens to a single PandaDoc signing event and orchestrates every downstream onboarding action across ~12 platforms (HubSpot, PandaDoc, GoHighLevel, Asana, Stripe, Xero, Timely, Slack, Google Workspace, Meta, Canva, Loom). The platform is also the team's live single source of truth: every client's step, knowledge profile, and per-action health is queryable from one dashboard.

### 1.2 Goals (Phase 1)

1. Compress agreement-to-go-live from ~2 weeks toward 1 day.
2. Eliminate manual handoffs between platforms during onboarding.
3. Make every fan-out action idempotent, retryable, and individually auditable.
4. Build a per-client knowledge profile that accumulates from every touchpoint, laying the foundation for Phase 2+ AI agents.
5. Replace the unreliable Zapier + Pabbly chain (47+ steps, partly broken) with direct API integrations governed by Inngest.

### 1.3 Non-goals (explicit)

- Strategy recommendations or autonomous strategic advice.
- Custom-branded client portal (deferred to Phase 2 engagement).
- Replacing GoHighLevel's existing portal or its post-signing automated reminder emails.
- Internal client knowledge bank as a standalone product (Phase 2).
- Client-facing Telegram bot (Phase 2).
- Steve AI digital twin (Phase 3).
- Productised AI platform (Phase 4).

### 1.4 Primary success metric

Agreement-to-go-live time, measured per pilot client in Sprint 4. Baseline ~2 weeks. Target: 1 day on the happy path.

---

## 2. User Roles and Permissions

Single-tenant tool: only Bullet's team plus the IzzyAgents engineering team have access.

| Role | Who | Dashboard access |
|------|-----|------------------|
| `founder` | John, Stephen | Everything, including admin (user management, pricing constants, integration credentials view-only) |
| `performance_director` | Max, Luchiano | Everything except admin. Can confirm `Ready for PD Review` -> `Ready for AM to Send` and edit AI-drafted email content |
| `account_manager` | AM team | Read-only on PD-only fields. Can pick up a client in `Ready for AM to Send` and trigger the email send |
| `izzyagents_engineer` | IzzyAgents team | Same as founder for Phase 1 (we need full visibility for ops); narrowed in Phase 2 |

Roles enforced in FastAPI endpoint dependencies and mirrored in Next.js route groups. No per-client scoping in Phase 1 (single-tenant); audit log is comprehensive.

---

## 3. Tech Stack (Locked)

| # | Area | Choice |
|---|------|--------|
| 1 | Backend language | Python (FastAPI) |
| 2 | Job queue / orchestration | Inngest |
| 3 | Database | Neon Postgres |
| 4 | ORM + migrations | SQLAlchemy 2.x async + Alembic |
| 5 | Frontend | Next.js (App Router) + TypeScript strict + Tailwind + shadcn/ui, dark mode default |
| 6 | Auth | Username/password + Resend confirmation email + 7-day session cookie |
| 7 | AI/LLM SDKs | Anthropic Python SDK direct (with prompt caching) for sales summaries + kick-off email; Claude Agent SDK for the research agent (Sprint 4) |
| 8 | Transcription | Native Zoom / Google Meet transcripts first; OpenAI Whisper API as fallback |
| 9 | Vector / embeddings | pgvector inside Neon |
| 10 | Object storage | Cloudflare R2 (transcript audio, scraped HTML, system-generated docs); Google Drive remains the client-asset store |
| 11 | Observability | Sentry + Inngest UI + Postgres `platform_actions` audit table |
| 12 | Hosting | Render.com (web service + worker + cron + dashboard); Neon for Postgres |
| 13 | Repo structure | Monorepo: `apps/api` (Python via uv) + `apps/dashboard` (TS via pnpm workspaces) + `packages/shared` |
| 14 | API contract | FastAPI OpenAPI -> codegen TS client into `packages/shared` |
| 15 | Real-time updates | TanStack Query polling every 5-10s on active dashboard views |
| 16 | Testing | pytest (backend) + Playwright (E2E) + Vitest (dashboard unit/component); TDD discipline |
| 17 | Staging | Yes from day one - separate Render services + separate Neon DB |
| 18 | Secrets management | Render env groups |
| 19 | Web scraping (Sprint 4) | Firecrawl for site/competitor-page deep scrapes; Claude `web_search` for competitor discovery |
| 20 | Slack integration | Incoming webhooks only (one-way notifications) |
| 21 | Local development | Docker Compose for Postgres + Inngest dev server |

### 3.1 Provisional (pending Bullet)

| Area | Provisional | Pending |
|------|-------------|---------|
| Outbound email provider | Resend (single system mailbox) | **Q-01** in `docs/openquestions.md` |

---

## 4. Data Model

Extends Section 6 of the plan with column-level detail. All timestamps `TIMESTAMPTZ`. All ids `UUID v4`. All money in USD `NUMERIC(12,2)` unless noted.

### 4.1 `clients`

Master record per client. One row per onboarding instance (a returning client signing for a second site creates a new row, linked via `parent_client_id`).

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `email` | citext NOT NULL | Indexed; not unique on its own (returning-client case) |
| `business_name` | text | |
| `legal_entity` | text NOT NULL | UK or International - drives Xero/Stripe routing |
| `contact_first_name` | text | |
| `contact_last_name` | text | |
| `phone` | text | |
| `service_tier` | text | |
| `monthly_fee_usd` | numeric(12,2) | Drives Timely time-budget = `monthly_fee_usd / 100` |
| `campaign_flow_type` | enum (`low_ticket_checkout`, `high_ticket_consultation`) NULL | Set by PD on kick-off; drives email variant (Section 3.5 of plan). Default suggestion inferred from portal answers |
| `current_step` | enum (`sales_call`, `agreement`, `signed`, `portal`, `kickoff`, `build`, `live`) NOT NULL | |
| `step_entered_at` | timestamptz NOT NULL | For time-in-step metric |
| `parent_client_id` | uuid FK clients(id) NULL | For returning-client second-site case |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |
| `updated_at` | timestamptz NOT NULL DEFAULT now() | |
| Platform IDs | | One column per platform: `hubspot_contact_id`, `pandadoc_document_id`, `ghl_contact_id`, `ghl_subaccount_id`, `asana_project_id`, `asana_finance_task_id`, `stripe_customer_id`, `stripe_subscription_id`, `xero_contact_id`, `timely_client_id`, `timely_project_id`, `meta_ad_account_id`, `drive_folder_id`, `sheet_row_id`, `slack_thread_ts` |

**Indexes**: `email`, `current_step`, `created_at DESC`, `parent_client_id`.

### 4.2 `client_knowledge`

Append-only knowledge profile. Source-tagged structured facts.

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `client_id` | uuid FK clients(id) NOT NULL | |
| `source` | enum (`sales_call`, `agreement`, `portal`, `research`, `kickoff`, `manual`) NOT NULL | |
| `key` | text NOT NULL | e.g. `business_goals`, `target_audience`, `red_flags` |
| `value` | jsonb NOT NULL | Structured payload |
| `value_text` | text | Searchable representation for full-text + embedding |
| `embedding` | vector(1536) | OpenAI/Anthropic-compatible; populated when `value_text` is non-empty |
| `captured_at` | timestamptz NOT NULL DEFAULT now() | |
| `captured_by` | uuid FK users(id) NULL | NULL when system-captured |

**Indexes**: `client_id`, `(client_id, source)`, `captured_at DESC`, GIN on `value`, `ivfflat` on `embedding`.

### 4.3 `onboarding_events`

Append-only event log. The trigger record for everything Inngest does.

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `client_id` | uuid FK clients(id) NULL | NULL until the signing event creates the client |
| `event_type` | text NOT NULL | `pandadoc.signed`, `ghl.portal_completed`, `stripe.payment_succeeded`, `kickoff.booked`, `kickoff.completed`, etc. |
| `external_id` | text | Webhook id from the source platform; unique per `(event_type, external_id)` |
| `payload` | jsonb NOT NULL | Raw webhook payload |
| `verified_at` | timestamptz | When signature verification passed |
| `processed_at` | timestamptz | When orchestrator picked it up |
| `occurred_at` | timestamptz NOT NULL DEFAULT now() | |

**Indexes**: `client_id`, `event_type`, `(event_type, external_id) UNIQUE`, `occurred_at DESC`.

### 4.4 `platform_actions`

The audit trail for every fan-out job. This is the durable record surfaced in the dashboard.

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `client_id` | uuid FK clients(id) NOT NULL | |
| `event_id` | uuid FK onboarding_events(id) | Action's triggering event |
| `platform` | enum (`hubspot`, `pandadoc`, `ghl`, `asana`, `stripe`, `xero`, `timely`, `slack`, `gsheets`, `gdocs`, `gdrive`, `gmail`, `gcal`, `meta`, `resend`, `firecrawl`) NOT NULL | |
| `action` | text NOT NULL | e.g. `create_subaccount`, `create_drive_folder`, `send_kickoff_email` |
| `idempotency_key` | text NOT NULL UNIQUE | `{client_id}:{platform}:{action}:{event_id}` |
| `status` | enum (`pending`, `in_progress`, `success`, `failed`, `dead_lettered`) NOT NULL | |
| `payload` | jsonb | What we sent |
| `response` | jsonb | What came back |
| `external_id` | text | The created resource's id on the target platform |
| `retry_count` | int NOT NULL DEFAULT 0 | |
| `last_error` | text | |
| `inngest_run_id` | text | For deep-link to Inngest UI |
| `started_at` | timestamptz | |
| `completed_at` | timestamptz | |

**Indexes**: `client_id`, `(client_id, platform, action)`, `status`, `started_at DESC`, `idempotency_key UNIQUE`.

### 4.5 `documents`

Pointers to client artefacts (Drive folders, generated Docs, transcripts, scraped pages, kick-off email body).

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `client_id` | uuid FK clients(id) NOT NULL | |
| `kind` | enum (`drive_folder`, `gdoc_export`, `gsheet_row`, `transcript_audio`, `transcript_text`, `scraped_page`, `kickoff_followup_email_body`, `pandadoc_signed_pdf`) NOT NULL | |
| `external_url` | text | When stored on third-party (Drive, Docs) |
| `r2_key` | text | When stored in R2 |
| `metadata` | jsonb | |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |

### 4.6 `research_results`

Output from the Sprint 4 research agent.

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `client_id` | uuid FK clients(id) NOT NULL | |
| `kind` | enum (`website_summary`, `competitors`, `audience_size`, `offer_suggestions`) NOT NULL | |
| `payload` | jsonb NOT NULL | Structured output |
| `citations` | jsonb | Source URLs |
| `confidence` | numeric(3,2) | 0.00 to 1.00 |
| `generated_at` | timestamptz NOT NULL DEFAULT now() | |

### 4.7 `client_assets`

Replaces the 16-branch GHL Outstanding Elements workflow.

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `client_id` | uuid FK clients(id) NOT NULL | |
| `asset_type` | enum (`ad_account_access`, `regulatory_docs`, `headshot`, `brand_guidelines`, `face_to_camera_video`, `logo_files`, `font_files`, `images`, `body_scan_assets`, etc.) NOT NULL | |
| `status` | enum (`required`, `requested`, `received`, `approved`, `waived`) NOT NULL | |
| `requested_at` | timestamptz | |
| `fulfilled_at` | timestamptz | |
| `source_url` | text | Where the artefact lives (Drive, Leadsy, etc.) |
| `notes` | text | |

**Index**: `(client_id, status)`.

### 4.8 `users`

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `email` | citext UNIQUE NOT NULL | |
| `password_hash` | text NOT NULL | argon2id |
| `full_name` | text NOT NULL | |
| `role` | enum (`founder`, `performance_director`, `account_manager`, `izzyagents_engineer`) NOT NULL | |
| `email_confirmed` | bool NOT NULL DEFAULT false | |
| `email_confirmed_at` | timestamptz | |
| `last_login_at` | timestamptz | |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |

### 4.9 `sessions`

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `user_id` | uuid FK users(id) NOT NULL | |
| `token_hash` | text UNIQUE NOT NULL | sha256 of the cookie value |
| `expires_at` | timestamptz NOT NULL | now() + 7 days |
| `ip` | inet | |
| `user_agent` | text | |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |

### 4.10 `audit_log`

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `actor_user_id` | uuid FK users(id) NULL | NULL for system actions |
| `action` | text NOT NULL | e.g. `client.email_updated`, `pd.confirmed_offer_name` |
| `entity_type` | text NOT NULL | |
| `entity_id` | uuid | |
| `before` | jsonb | |
| `after` | jsonb | |
| `ip` | inet | |
| `occurred_at` | timestamptz NOT NULL DEFAULT now() | |

---

## 5. Integration Surfaces

One subsection per platform. All integrations go through `apps/api/integrations/<platform>/` with a thin adapter that exposes typed methods consumed by Inngest functions. Every outbound call writes a `platform_actions` row.

### 5.1 PandaDoc (agreement signing trigger)

- **Auth**: API token in Render env group
- **Mode**: Webhook (best-effort) + daily reconciliation poll against the API
- **Events consumed**: `document_state_changed -> document.completed`
- **Verification**: HMAC signature check on webhook payload
- **Idempotency**: `(event_type=pandadoc.signed, external_id=document.id)` unique in `onboarding_events`
- **What we read**: signed document metadata (template id determines UK / International routing), client fields, signer details, signed PDF
- **What we write**: nothing back to PandaDoc
- **Reconciliation**: daily cron pulls `documents?status=completed&signed_after=<last_check>` and creates missing `onboarding_events` rows
- **Fallback**: manual replay endpoint accepts a PandaDoc document id and emits the synthetic webhook
- **Rate limits**: 50 req/min - reconciliation respects with backoff

### 5.2 GoHighLevel

- **Auth**: OAuth (sub-account level) + agency API key for sub-account creation
- **Pabbly**: retired - we call the agency-level API directly
- **Returning-client check**: before sub-account creation, lookup existing GHL contact by email; if found, link rather than duplicate
- **Events consumed**: portal completion webhook (`survey_response.created` for the OB V2 survey)
- **Idempotency**: `(event_type=ghl.portal_completed, external_id=response.id)` unique
- **What we write**: triggers existing GHL workflows where they remain (post-signing portal link, survey reminders); we do **not** rebuild those flows. We replace the 16-branch Outstanding Elements workflow entirely (single conditional email template driven by `client_assets`)
- **Rate limits**: 100 req/10s; concurrency cap 3 in Inngest

### 5.3 HubSpot

- **Auth**: private app access token
- **Scopes**: `crm.objects.contacts.read`, `crm.objects.contacts.write`, `crm.objects.deals.read`, webhook subscriptions
- **What we read**: contact fields used at PandaDoc template creation
- **What we write**: stage transitions on the deal as the client moves through onboarding (optional mirror)
- **Rate limits**: 100 req/10s

### 5.4 Asana

- **Auth**: personal access token (service account)
- **What we write**: `Bullet Clients Status` project, finance task, onboarding subtasks per template, due-date updates on kick-off date changes
- **Templates**: stored by id with checksum monitoring; drift alerts to Slack
- **Idempotency**: action key uses `client_id:asana:create_project:event_id`
- **Rate limits**: 150 req/min; concurrency cap 5

### 5.5 Stripe

- **Auth**: restricted key (provisioned 30/04/2026 per Stephen)
- **Capture timing**: card details captured inside PandaDoc at signing; we read the payment method via Stripe webhook (`payment_method.attached`); subscription activated only after kick-off follow-up sign-off (Sprint 3)
- **What we write**: `customer.create`, `payment_method.attach`, `subscription.create` (deferred)
- **UK vs International routing**: account choice driven by `clients.legal_entity`
- **Webhook events consumed**: `payment_method.attached`, `invoice.paid`, `charge.failed`
- **Idempotency**: native Stripe idempotency keys mirror our `idempotency_key`
- **Amex limitation**: PandaDoc can't capture Amex; flagged in dashboard, finance picks up manual capture without stalling the pipeline

### 5.6 Xero

- **Auth**: OAuth 2.0 with offline access
- **What we write**: contact creation, with chart-of-accounts and tracking categories per `legal_entity` (UK or International routing - exact rule pending Section 11 Q-3 in the plan)
- **Rate limits**: 60 req/min

### 5.7 Timely

- **Auth**: API token
- **What we write**: client + project creation; project budget = `clients.monthly_fee_usd / 100` hours; team assignment remains a dashboard-driven action (per Section 3.10 of the plan)
- **Rate limits**: not strictly documented; concurrency cap 3

### 5.8 Slack

- **Mode**: incoming webhook (one-way notifications). No bot user, no slash commands.
- **Channels**: `#bullet_inbound_clients` (primary). Per-platform alert channels added via env config when Bullet specifies.
- **Notifications fired**:
  - New signed agreement (with deep links to dashboard, HubSpot, GHL, Drive, Asana, Stripe, Xero, Timely, Calendar)
  - Kick-off booked
  - Fan-out action escalation after 3 retries
  - Daily reconciliation discrepancy
- **Rate limits**: 1 msg/sec per webhook; backoff with retry

### 5.9 Google Workspace

- **Auth**: service account + domain-wide delegation
- **Sheets**: write `Client Status Sheet` row matching current schema; `SaaS Mode` column passthrough until Section 11 Q-12 in the plan is resolved
- **Docs**: optional sync (export from database; not the primary store)
- **Drive**: folder tree creation; structure mirrors today's actively used folders only (legacy folders skipped pending Section 11 Q-2)
- **Calendar**: book kick-off call via existing GHL calendar link; subscribe to calendar-change events for kick-off-date propagation to Asana
- **Gmail**: reserved for kick-off follow-up email if Q-01 resolves to per-AM Gmail. Provisional default does not use Gmail.

### 5.10 Meta Marketing API

- **Auth**: System User access token + Business Manager scopes
- **Use**: audience size queries for the research agent (Sprint 4); ad-account confirmation
- **Rate limits**: per-account quota; backoff and cache aggressively

### 5.11 Resend (provisional, pending Q-01)

- **Auth**: API key in Render env group
- **Domain**: `bulletdigitalmedia.com` - SPF / DKIM / DMARC required pre-Sprint 1
- **Templates**: React Email components for auth confirmation, kick-off follow-up email (both variants), tech-requirements email, dashboard alerts
- **Reply handling**: catch-all routing rule -> shared inbox or AM forwarding (depending on Q-01 outcome)
- **Idempotency**: header-based dedupe per outbound message id

### 5.12 Firecrawl (Sprint 4)

- **Auth**: API key
- **Use**: client website deep scrape (full-page markdown), competitor-page scrape after Claude `web_search` discovery
- **Output**: stored in `documents` (R2 for raw HTML) + `research_results` (structured)

### 5.13 Anthropic (Claude)

- **Auth**: API key
- **Models**: `claude-opus-4-7` for sales summaries, kick-off email drafting, research synthesis. Prompt caching enabled.
- **Agent SDK**: only for the research agent's tool-using loop
- **Rate limits**: enforce per-tier; retry with backoff

### 5.14 OpenAI (Whisper fallback)

- **Auth**: API key
- **Use**: only when Zoom/Meet native transcripts unavailable
- **Cost cap**: $50/mo soft cap, alerts at 80%

### 5.15 Sentry

- **DSN**: per-environment (api-prod, api-staging, dashboard-prod, dashboard-staging)
- **PII scrubbing**: email, phone, transcript content, signed-PDF urls scrubbed before send

### 5.16 Inngest

- **Environments**: prod, staging, local dev
- **Functions**: one per fan-out action; dashboard reads `inngest_run_id` from `platform_actions` for deep-link

### 5.17 Cloudflare R2

- **Buckets**: `bullet-prod-artefacts`, `bullet-staging-artefacts`
- **Access**: signed URLs for dashboard reads; server-side key for writes
- **Layout**: `clients/{client_id}/transcripts/{event_id}.{ext}`, `clients/{client_id}/scraped/{url-hash}.html`, `clients/{client_id}/emails/{action_id}.html`

---

## 6. Orchestration Contract

Every Inngest function follows the same rules.

### 6.1 Idempotency

- Each function has an idempotency key: `{client_id}:{platform}:{action}:{event_id}`
- Persisted as `platform_actions.idempotency_key UNIQUE`
- Inngest dedupes at trigger time using the same key
- Returning the same external_id from a re-run is treated as success, not as a duplicate

### 6.2 Retry policy

- Exponential backoff: 1m, 5m, 15m, 1h, 6h
- Max 5 attempts before dead-letter
- Dead-lettered actions surface in `/actions` dashboard view with a manual retry button (founder + PD only)
- Slack escalation alert fires after attempt 3

### 6.3 Per-platform concurrency caps

| Platform | Concurrency |
|----------|-------------|
| Asana | 5 |
| Google APIs (combined) | 10 |
| GHL | 3 |
| HubSpot | 5 |
| Stripe | 5 |
| Xero | 3 |
| Timely | 3 |

### 6.4 Status writes

Every function:
1. Writes `platform_actions` row with `status=pending` before any external call
2. Updates to `status=in_progress` when the call is attempted
3. Updates to `status=success` with `external_id` and `response`, OR `status=failed` with `last_error` and incremented `retry_count`
4. On final failure, updates to `status=dead_lettered`

### 6.5 Reconciliation

A daily cron at 03:00 UK time:
- Pulls all PandaDoc completed documents from the last 7 days; verifies each has a corresponding `onboarding_events` row
- Pulls all `clients` in `current_step=signed` for >24h; verifies fan-out completed
- Sends a Slack alert with discrepancies

---

## 7. AI Prompt and Output Specifications

### 7.1 Sales call summary

- **Trigger**: Zoom / Meet recording completed -> transcript captured -> Inngest `summarise_sales_call` function
- **Input**: full transcript text + system prompt (cached) + few-shot examples (cached)
- **Output schema** (JSON, validated by Pydantic):
  - `business_type`: text
  - `business_goals`: array of text
  - `budget_range_usd`: object {min, max, currency}
  - `pain_points`: array of text
  - `red_flags`: array of text
  - `next_steps`: array of text
  - `notable_quotes`: array of {speaker, quote, timestamp_seconds}
- **Storage**: written to `client_knowledge` with `source=sales_call`, one row per top-level field; full transcript stored in `documents` (R2)

### 7.2 Kick-off follow-up email - low-ticket variant (`campaign_flow_type = low_ticket_checkout`)

- **Trigger**: kick-off call completes; PD selects `campaign_flow_type = low_ticket_checkout` in dashboard
- **Input**: kick-off transcript + portal answers + sales summary + brand assets + historical-offers corpus
- **Output schema**:
  - `anchor`: object {type: `subscription` or `class_pack`, calculation: text, value_usd: number}
  - `body_scan_count`: 1, 2, or 3 (sourced from portal)
  - `body_scan_unit_price_usd`: number
  - `consultation_value_usd`: number (0 if not part of offer)
  - `total_value_usd`: number
  - `offer_price_usd`: number
  - `savings_usd`: number
  - `percent_off`: number
  - `offer_name_suggestions`: array of 3 text
  - `bring_a_friend_block`: text or null
  - `mbg_block`: text or null
  - `email_subject`: text
  - `email_body`: text (HTML or markdown)
  - `calculation_transparency`: text (the worked example shown to PD)
- **Calculator rules** (from plan Section 3.5):
  - Subscription anchor: `monthly_price / 30.4 * offer_days`
  - Class-pack anchor: `drop_in_rate * class_count`
- **Storage**: `documents` row of kind `kickoff_followup_email_body`; calculation transparency rendered alongside in dashboard

### 7.3 Kick-off follow-up email - high-ticket variant (`campaign_flow_type = high_ticket_consultation`)

- **Trigger**: kick-off call completes; PD selects `campaign_flow_type = high_ticket_consultation`
- **Input**: same as low-ticket
- **Output schema**:
  - `agreed_offer_structure`: text
  - `headline_price_usd`: number
  - `ad_budget_usd`: number
  - `start_date`: date
  - `creative_requirements`: array of text
  - `outstanding_items`: array of text
  - `setup_timeline`: text
  - `kickoff_date`: date
  - `consultation_booking_link`: url
  - `email_subject`: text
  - `email_body`: text
- **Storage**: same as low-ticket

### 7.4 Research agent (Sprint 4)

- **Tools** (Claude Agent SDK):
  - `scrape_website(url)` - Firecrawl
  - `search_competitors(location, business_type)` - Claude built-in `web_search`
  - `scrape_competitor_page(url)` - Firecrawl
  - `get_meta_audience_size(geo, demographics)` - Meta Marketing API
- **Output schema**:
  - `website_summary`: {services, pricing, usps, current_offers, brand_voice}
  - `competitors`: array of 5 {name, url, distance_km, services, pricing_signals}
  - `audience_size`: {geo, demographics, estimated_size}
  - `offer_suggestions`: array of 3 {name, structure, rationale, expected_appeal}
- **Storage**: `research_results`

### 7.5 Prompt caching policy

- System prompts cached at 5-minute TTL via `cache_control: ephemeral`
- Few-shot examples cached
- Per-call client context (transcript, portal answers) not cached

---

## 8. Dashboard Information Architecture

| Route | Purpose | Polling |
|-------|---------|---------|
| `/login` | Username/password sign-in + email-confirmation flow | none |
| `/clients` | List view: every active client + step + time-in-step + last-action status | 10s |
| `/clients/[id]` | Master detail: status, knowledge profile, action health, deep links, AI sales summary, follow-up email draft, asset checklist | 5s on active fields |
| `/clients/[id]/knowledge` | Per-client knowledge profile (structured + semantic search) | none |
| `/clients/[id]/research` | Research agent output pre-kickoff (Sprint 4) | none |
| `/actions` | Cross-client action health: failed, retrying, dead-lettered. Manual retry button per action | 10s |
| `/admin/users` | User management (founder/PD only) | none |
| `/admin/integrations` | Per-platform credential health (founder only) | 30s |

### 8.1 Client detail screen states

The dashboard surfaces every Step 4 hand-off state explicitly (per plan Section 3.3):
- `Ready for Performance Director Review` - shows AI-drafted email, suggested offer names, worked pricing (low-ticket) or prose (high-ticket); PD can edit and confirm
- `Ready for Account Manager to Send` - PD confirmed; AM picks up and clicks Send
- `Sent` - email sent via Resend (provisional)

### 8.2 Asset checklist

Replaces the 16-branch GHL Outstanding Elements workflow. Per-client live checklist driven by `client_assets`. Each row shows: asset type, status, requested at, source url. Actions: mark received, mark approved, mark waived, send chase email.

---

## 9. Observability and Alerting

| Concern | Tool | What |
|---------|------|------|
| Uncaught errors | Sentry | FastAPI + Next.js, with PII scrubbing |
| Performance regressions | Sentry | P95 latency tracked on critical endpoints |
| Workflow step inspection | Inngest UI | Step-by-step replay of every Inngest run; deep-linked from dashboard via `inngest_run_id` |
| Per-action audit | Postgres `platform_actions` | Surfaced in `/actions` dashboard view |
| Slack alerts | Incoming webhook | Fan-out failures (3 retries), reconciliation discrepancies, P95 regressions |
| Logs | Render native + Sentry breadcrumbs | Structured JSON logs from FastAPI; basic Render console for ops |

### 9.1 Alert taxonomy

| Severity | Channel | Examples |
|----------|---------|----------|
| Critical | Slack `#bullet_inbound_clients` + Sentry | Webhook signature failure, dead-lettered action, daily reconciliation discrepancy |
| Warn | Slack `#bullet_inbound_clients` | Retry attempt 3 on a fan-out action, rate-limit backoff exceeding 5 minutes |
| Info | Slack `#bullet_inbound_clients` | New client signed, kick-off booked, payment received |

---

## 10. Security and Compliance

### 10.1 Secrets

- All credentials in Render environment groups (per env: prod, staging)
- No secrets in repo; pre-commit hook scans for keys (already enforced by your global `.env` ignore rule)
- Connection to Neon over TLS; pgbouncer connection pooling

### 10.2 Auth and sessions

- Username/password (per user choice 03/05/2026)
- Password hashing: argon2id (Phase 1 default)
- Session cookies: HttpOnly, Secure, SameSite=Lax, 7-day expiry, sha256-hashed token in `sessions` table
- Email confirmation required pre-login: token sent via Resend; link expires in 24h
- Brute-force protection: 5 failed attempts -> 15min IP-level lockout

### 10.3 PII handling

- Sales call transcripts and kick-off transcripts contain PII (names, business details, financial info)
- Sentry breadcrumbs scrub email, phone, transcript content, signed-PDF urls
- R2 objects encrypted at rest (R2 default)
- Database backups encrypted (Neon default)

### 10.4 Retention

- Transcripts: 12 months default in R2 then auto-purge (open question: confirm with Bullet - candidate Q-02 if Bullet has a stricter view)
- `audit_log`: retained indefinitely for Phase 1
- `platform_actions`: 24 months then archived

### 10.5 Role enforcement

- Every FastAPI endpoint declares its required role(s) via a dependency
- Next.js route groups mirror role boundaries; route-level guard enforced server-side, not just client
- Audit log records actor + before/after for every mutation

---

## 11. Environment and Deployment Topology

### 11.1 Render services

| Service | Type | Notes |
|---------|------|-------|
| `bullet-api-prod` | Web service (Python) | FastAPI + uvicorn |
| `bullet-worker-prod` | Background worker | Inngest worker process |
| `bullet-cron-prod` | Cron | Daily reconciliation job at 03:00 UK |
| `bullet-dashboard-prod` | Web service (Node) | Next.js |
| Same shape with `-staging` suffix | | Identical topology |

### 11.2 Neon

- Two projects: `bullet-prod`, `bullet-staging`
- Per-PR DB branching off `bullet-staging` for migration testing
- pgvector extension enabled in both

### 11.3 Inngest

- Two environments: `prod`, `staging`
- Local dev: Inngest CLI dev server in Docker Compose

### 11.4 R2

- Buckets: `bullet-prod-artefacts`, `bullet-staging-artefacts`
- Lifecycle rule: transcript audio purged at 12 months

### 11.5 Resend (provisional)

- Single domain: `bulletdigitalmedia.com`
- DNS records (SPF, DKIM, DMARC) required pre-Sprint 1
- Webhooks: bounces and complaints back into our `/webhooks/resend` endpoint -> `audit_log`

### 11.6 CI / CD

- GitHub repo: `tsizzybots/bullet_digital_media`
- GitHub Actions: lint + typecheck + tests on every PR; deploy to Render staging on merge to `main`; manual promote to production via Render dashboard
- No staging-to-prod auto-promote in Phase 1

### 11.7 Local dev

- `docker compose up` starts: Postgres (with pgvector), Inngest dev server
- `apps/api`: `uv run uvicorn ...`
- `apps/dashboard`: `pnpm dev`

---

## 12. Sprint-Mapped Acceptance Criteria

Every criterion is testable. Failing one blocks sprint sign-off.

### 12.1 Sprint 1 (Weeks 1-2): Foundation + Sales Call Intelligence

- [ ] Postgres schema migrated; pgvector extension enabled; seed users created (founders + PDs)
- [ ] Username/password auth working; Resend confirmation email delivered; 7-day session cookie issued and rejected after expiry
- [ ] PandaDoc webhook receives, verifies signature, persists `onboarding_events` row, dedupes on replay
- [ ] PandaDoc daily reconciliation cron runs at 03:00 UK, flags missing events to Slack
- [ ] Direct GHL agency-API sub-account creation works (Pabbly retired)
- [ ] Returning-client check: signing for second site reuses existing GHL contact, links via `parent_client_id`
- [ ] Sales call transcript capture: native from Zoom and Google Meet; Whisper fallback when native missing
- [ ] AI sales summary generated, written to `client_knowledge` with `source=sales_call`, validated against schema
- [ ] Dashboard `/clients` list shows every signed client + AI sales summary if available
- [ ] Sentry receives errors from both api and dashboard
- [ ] Inngest UI accessible to engineers; runs visible from dashboard via `inngest_run_id` deep-link

### 12.2 Sprint 2 (Weeks 3-4): Core Fan-Out + Follow-Up Email + MVP Milestone

- [ ] Slack incoming webhook fires new-client notification with deep links to dashboard, HubSpot, GHL, Drive, Asana, Calendar
- [ ] Asana integration: `Bullet Clients Status` project + finance task + onboarding subtasks created from template
- [ ] Google Sheets: client row created with current schema (incl. `SaaS Mode` passthrough)
- [ ] Google Drive: actively-used folder tree created; sharing applied; legacy folders not created
- [ ] Google Calendar: kick-off call booked via existing GHL calendar link
- [ ] Inngest fan-out workflow: every action surfaces success/failure/retry in `/actions` dashboard view
- [ ] Kick-off follow-up email generator (low-ticket variant): pricing calculator with subscription and class-pack anchors works against Stephen's worked examples (£200/30.4*21 = £138.16; £30*5 = £150.00)
- [ ] Kick-off follow-up email generator (high-ticket variant): prose-only confirmation generated; no maths block
- [ ] Dashboard surfaces `Ready for PD Review` and `Ready for AM to Send` states explicitly per Step 4
- [ ] PD can edit AI-drafted email body, override offer name, adjust pricing
- [ ] AM can click Send; email sent (via Resend if Q-01 resolves to Resend)
- [ ] End-to-end happy path: PandaDoc signing creates all non-financial artefacts automatically with no human intervention
- [ ] **MVP demo to Bullet team**: validated against simulated onboarding flow

### 12.3 Sprint 3 (Weeks 5-6): Financial Integrations + Comms + Legacy Replacements

- [ ] Stripe: customer + payment method captured at signing (read via `payment_method.attached` webhook)
- [ ] Stripe: subscription activated only after kick-off follow-up sign-off (deferred trigger)
- [ ] Xero: contact created with UK or International routing per `clients.legal_entity`
- [ ] Timely: client + project auto-created; project `time_budget_hours = monthly_fee_usd / 100`
- [ ] `client_assets` table populated per client; dashboard checklist surfaces every required asset with status
- [ ] Single conditional asset-chase email template fires from `client_assets` state (replaces 16-branch GHL workflow)
- [ ] Existing GHL conditional technical-requirements workflows still triggered where they make sense (post-signing portal link, survey reminders)
- [ ] Kick-off date change in Calendar propagates to Asana finance task automatically (eliminates Steve's Monday manual sync)
- [ ] Amex flagged distinctly in dashboard; pipeline does not stall on unpaid agreement

### 12.4 Sprint 4 (Weeks 7-8): Research Agent + Polish + Pilot

- [ ] Research agent: client website scraped via Firecrawl; structured `website_summary` written to `research_results`
- [ ] Research agent: competitors discovered via Claude `web_search`; top 5 stored with distance and pricing signals
- [ ] Research agent: Meta audience size pulled and stored
- [ ] Research agent: 3 offer suggestions generated and surfaced in `/clients/[id]/research`
- [ ] Dashboard polish: time-in-step view, bottleneck identification, per-platform health
- [ ] Reconciliation runbooks documented in `/docs/runbooks/`
- [ ] Pilot: 3 to 5 real new clients onboarded end-to-end through the automated flow
- [ ] Measured agreement-to-go-live time per pilot client; results captured in `docs/pilot-results.md`
- [ ] (Stretch) GHL AI builder integration evaluated; feasibility documented

---

## 13. Cross-References

### 13.1 Open implementation questions (Bullet must answer)

| Question | File | Blocks |
|----------|------|--------|
| Q-01: Outbound email provider - single system mailbox or per-AM Gmail? | `docs/openquestions.md` | Sprint 2, Sprint 3 |

### 13.2 Plan-scope open questions (carried over from Section 11 of the plan)

These are scope/decision questions not implementation-blocking; they live in `docs/phase-1-plan.md` Section 11. Surfaced here for visibility:

- Asana workspace + project template IDs
- Drive folder-tree simplification
- Xero chart-of-accounts and tracking categories per legal entity
- Sales call transcript retention / consent wording
- Returning-client handling default
- Timely project automation default
- Kickoff call trajectory (human-led vs AI-led in Phase 2+)
- Pipeline stage parity with GHL
- `SaaS Mode` column meaning
- Sales handover notes (dashboard vs auto-extract)
- Amex fallback decision
- 30.4-day month divisor confirmation
- Historical offers corpus location
- Class-pack offer shape variants

### 13.3 Source documents

- `docs/phase-1-plan.md` v3.2 (locked spec)
- `docs/phase-1-plan-client.md` (client-facing)
- `docs/loom-video-summaries/OB-Phase-1.md`, `OB-Phase-2.md` (current onboarding mechanics)
- `docs/dashboard-feedback/client-feedback-log.md` (existing feedback)
- `meeting_notes/onboarding/` (most recent: 21/04/2026)
- `emails/Bullet Onboarding Process.pdf` (original brief, 13/04/2026)

---

*Prepared by IzzyAgents | AI Solutions Consultancy*
