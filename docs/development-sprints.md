# Phase 1 Development Sprints

**Project**: Bullet Digital Media x IzzyAgents - Onboarding Process Automation
**Phase**: 1 (Months 1-2)
**Date**: 04/05/2026
**Status**: v1.0 - Initial sprint breakdown
**Source**: `docs/PRD.md` v1.0, `docs/phase-1-plan.md` v3.2

This document is the canonical, ordered task list for Phase 1 development. Every task is sized to be testable, follows test-driven development discipline, and lists any blocking dependencies on other tasks.

**Test discipline**: For every task, write the failing tests first, then the implementation, then verify green. Test descriptions in each task are the contract the implementation must satisfy. No task is considered done until its tests are passing in CI.

**Naming**: Tasks are numbered `S{sprint}-{nn}` (e.g. `S1-01` = Sprint 1, task 1). Order within a sprint reflects build order; tasks without dependencies on earlier tasks in the same sprint can run in parallel.

**Resend assumption**: Q-01 (outbound email provider) is treated as resolved to **Resend with a single system mailbox** (`onboarding@bulletdigitalmedia.com`) for the purpose of this plan. If Bullet later mandates per-AM Gmail, Sprint 2 email tasks (`S2-26` through `S2-29`) will be revised; nothing else shifts.

**Definition of "live and testable"**: Sprint 4 closes when the production stack is deployed on Render, the pilot clients have been onboarded end-to-end through the automated flow, and Bullet can self-serve the dashboard, trigger real signings, and observe real outcomes.

---

## Sprint 1 (Weeks 1-2): Foundation + Sales Call Intelligence

Goal: Project scaffold, database, auth, dashboard shell live. PandaDoc signing event ingested. GHL sub-account creation direct. Sales call transcript -> AI summary in dashboard. Team can see AI-generated sales summaries from day one.

### S1-01: Monorepo scaffold

- **Description**: Create the monorepo at `tsizzybots/bullet_digital_media` with `apps/api` (Python via uv), `apps/dashboard` (TS via pnpm workspaces), `packages/shared`. Add root `README.md`, `.gitignore` (including `.env`), `.editorconfig`, pre-commit hook scanning for secrets.
- **Tests**: `pnpm install` and `uv sync` both succeed cleanly from a fresh clone. Pre-commit hook rejects a test commit containing a fake API key. CI workflow file lints successfully.
- **Dependencies**: n/a

### S1-02: Docker Compose local dev environment

- **Description**: `docker-compose.yml` at repo root brings up Postgres 16 with `pgvector` extension and the Inngest dev server. Document the `docker compose up` command in the root README.
- **Tests**: `docker compose up` starts both services. A connection from a local script can `CREATE EXTENSION vector;` against the Postgres instance. Inngest dev server UI loads on its default port.
- **Dependencies**: S1-01

### S1-03: Neon Postgres provisioning (prod + staging)

- **Description**: Create two Neon projects: `bullet-prod` and `bullet-staging`. Enable `pgvector` in both. Configure pgbouncer pooling. Capture connection strings into Render env groups (groundwork for S1-04).
- **Tests**: `psql` connection succeeds against both databases over TLS. `SELECT * FROM pg_extension WHERE extname = 'vector';` returns a row in both.
- **Dependencies**: n/a

### S1-04: Render service provisioning (staging)

- **Description**: Create staging Render services: `bullet-api-staging` (FastAPI), `bullet-worker-staging` (Inngest worker), `bullet-cron-staging` (cron), `bullet-dashboard-staging` (Next.js). Wire env groups to Neon staging. Confirm zero-downtime restart works.
- **Tests**: Each staging service serves a `/healthz` endpoint returning `{"status":"ok"}` over HTTPS. Render env vars are scoped per service via env groups.
- **Dependencies**: S1-01, S1-03

### S1-05: SQLAlchemy + Alembic setup

- **Description**: Configure SQLAlchemy 2.x async with asyncpg driver. Initialise Alembic with an `env.py` that supports per-PR Neon DB branching. First migration: `vector` extension and `citext` extension creation only.
- **Tests**: `alembic upgrade head` runs cleanly against a fresh DB. `alembic downgrade base` reverses cleanly. Async session factory yields a working session in a smoke test.
- **Dependencies**: S1-02

### S1-06: Schema migration - `clients` table

- **Description**: Alembic migration creating `clients` per PRD section 4.1, including `campaign_flow_type` enum, `current_step` enum, `parent_client_id` self-FK, all platform ID columns, and the indexes listed (email, current_step, created_at DESC, parent_client_id).
- **Tests**: Migration applies and reverses cleanly. Inserting a row with `legal_entity='UK'` succeeds; inserting an invalid enum value fails. Self-FK accepts a parent_client_id that exists; rejects one that does not.
- **Dependencies**: S1-05

### S1-07: Schema migration - `client_knowledge` table

- **Description**: Alembic migration per PRD section 4.2 including the `vector(1536)` column, GIN index on `value`, and ivfflat index on `embedding`. Source enum and FK to `clients`.
- **Tests**: Migration up/down. Insert with valid embedding succeeds. GIN index used by query planner for `value @>` lookup (verified via `EXPLAIN`).
- **Dependencies**: S1-06

### S1-08: Schema migration - `onboarding_events` table

- **Description**: Alembic migration per PRD section 4.3 with the `(event_type, external_id) UNIQUE` constraint for idempotent webhook ingestion.
- **Tests**: Migration up/down. Inserting two rows with the same `(event_type, external_id)` raises an integrity error.
- **Dependencies**: S1-06

### S1-09: Schema migration - `platform_actions` table

- **Description**: Alembic migration per PRD section 4.4. `idempotency_key UNIQUE`, status enum, FK to `clients` and `onboarding_events`.
- **Tests**: Migration up/down. Duplicate `idempotency_key` insert raises integrity error. Status transitions through pending -> in_progress -> success without constraint violation.
- **Dependencies**: S1-06, S1-08

### S1-10: Schema migration - remaining tables

- **Description**: One migration covering `documents`, `research_results`, `client_assets`, `users`, `sessions`, `audit_log` per PRD sections 4.5 - 4.10.
- **Tests**: Migration up/down. `users.email` UNIQUE enforced. `sessions.token_hash` UNIQUE enforced. Enums reject invalid values.
- **Dependencies**: S1-06

### S1-11: Seed founders and PDs

- **Description**: Idempotent seeder script that inserts the four named users (John, Stephen, Max, Luchiano) with their roles (`founder`, `founder`, `performance_director`, `performance_director`) and a temporary password each that forces a reset on first login.
- **Tests**: Running the seeder twice produces identical state (no duplicates). Each seeded user's password verifies under argon2id.
- **Dependencies**: S1-10

### S1-12: FastAPI app shell + role dependencies

- **Description**: FastAPI app with structured JSON logging, `/healthz`, `/version`, OpenAPI exposed at `/openapi.json`. Reusable role-based dependencies (`require_founder`, `require_pd`, `require_am`, `require_engineer`).
- **Tests**: `/healthz` returns 200. An endpoint guarded by `require_founder` returns 403 for an AM session and 200 for a founder session. OpenAPI document validates against the OpenAPI 3.1 schema.
- **Dependencies**: S1-04, S1-10

### S1-13: Username/password auth + argon2id

- **Description**: `POST /auth/login` accepts email + password, verifies under argon2id, issues a session cookie. Brute-force protection: 5 failed attempts in 15 minutes from the same IP triggers a 15-minute lockout.
- **Tests**: Valid credentials issue a session cookie. Invalid credentials return 401. Five rapid failures from the same IP trigger lockout; sixth attempt returns 429 even with valid credentials. Lockout expires after 15 minutes.
- **Dependencies**: S1-11, S1-12

### S1-14: Resend email confirmation flow

- **Description**: New users receive a confirmation email via Resend with a 24h-expiring token. `email_confirmed=false` blocks login. `POST /auth/confirm/{token}` flips the flag and timestamps `email_confirmed_at`.
- **Tests**: Unconfirmed user cannot log in (403 with a clear message). Confirmation link within 24h flips the flag. Expired token returns 410. Resend API client is mockable in tests.
- **Dependencies**: S1-13

### S1-15: 7-day session cookie + revocation

- **Description**: Sessions stored in `sessions` table with `sha256(token)`, HttpOnly + Secure + SameSite=Lax cookie, 7-day expiry. `POST /auth/logout` deletes the session row.
- **Tests**: Session cookie works for 7 days. Cookie expires after 7 days and access is rejected. Logout deletes the row and the same cookie is rejected on the next request.
- **Dependencies**: S1-13

### S1-16: Next.js dashboard scaffold

- **Description**: Next.js (App Router) + TypeScript strict + Tailwind + shadcn/ui dark-mode default. Route groups for role boundaries (`(founder)`, `(pd)`, `(am)`). Server-side auth guard reading session cookie.
- **Tests**: `pnpm build` succeeds with strict TypeScript. Unauthenticated access to a protected route redirects to `/login`. Dark mode is the default theme on a fresh load.
- **Dependencies**: S1-04, S1-12

### S1-17: OpenAPI -> TS client codegen + TanStack Query

- **Description**: Codegen pipeline that produces a typed TS client from `apps/api`'s OpenAPI document into `packages/shared`. TanStack Query configured with 5-10s polling defaults on active dashboard views.
- **Tests**: Codegen runs cleanly in CI; generated types compile under strict mode. A sample query against `/healthz` from the dashboard renders the response without `any`.
- **Dependencies**: S1-12, S1-16

### S1-18: Login + email confirmation pages

- **Description**: `/login` page with email + password form and confirmation-flow handling. Confirmation page reads the token from the URL and calls `POST /auth/confirm/{token}`.
- **Tests**: Playwright E2E: a seeded user can log in, see the empty `/clients` page, log out, and is rejected when re-using the cookie. A new user receives a confirmation email (Resend mock), clicks the link, and is logged in.
- **Dependencies**: S1-14, S1-15, S1-16, S1-17

### S1-19: Inngest setup (prod + staging + local)

- **Description**: Inngest accounts and signing keys provisioned for prod and staging. Worker process in `apps/api` registers a no-op function. Local dev uses the dev server from S1-02.
- **Tests**: Sending a test event via Inngest CLI in local dev fires the registered function. Staging Inngest UI shows the worker as connected.
- **Dependencies**: S1-02, S1-04

### S1-20: Sentry setup (api + dashboard, PII scrubbing)

- **Description**: Sentry DSNs per environment. PII scrubbing rules: email, phone, transcript content, signed-PDF URLs scrubbed before send. Source maps uploaded for the dashboard.
- **Tests**: A deliberate `raise` in api lands in Sentry with email scrubbed to `[Filtered]`. Dashboard runtime error lands in Sentry with a useful stack trace from source maps.
- **Dependencies**: S1-04, S1-16

### S1-21: Render env groups + secret hygiene

- **Description**: All Phase 1 secrets configured in Render env groups (per env). Pre-commit hook (already installed in S1-01) verified to scan staged files for common secret patterns.
- **Tests**: Attempting to commit a file containing `sk_test_` is blocked. All staging services start with their env vars resolved (no `MISSING_ENV` errors in logs).
- **Dependencies**: S1-01, S1-04

### S1-22: PandaDoc webhook receiver + HMAC verification

- **Description**: `POST /webhooks/pandadoc` verifies HMAC signature, dedupes against `(event_type=pandadoc.signed, external_id=document.id)`, persists `onboarding_events` row, and triggers an Inngest event for downstream fan-out.
- **Tests**: Valid signature + new payload creates one event row and triggers Inngest. Replay of the same payload creates no new row (idempotent). Tampered signature returns 401 and creates no row. Verified-at timestamp populated only on signature pass.
- **Dependencies**: S1-08, S1-19

### S1-23: PandaDoc daily reconciliation cron

- **Description**: Cron at 03:00 UK time pulls `documents?status=completed&signed_after=<last_check>` from PandaDoc. Any signed documents missing a corresponding `onboarding_events` row trigger a synthetic event and a Slack alert.
- **Tests**: Mocked PandaDoc API returns 3 docs, 1 of which has no event row. Cron creates the missing event and posts to Slack mock. Subsequent run with no new docs is a no-op.
- **Dependencies**: S1-22

### S1-24: PandaDoc manual replay endpoint

- **Description**: `POST /admin/pandadoc/replay/{document_id}` (founder + engineer only) fetches the document via the API and emits the synthetic webhook through the same handler as S1-22.
- **Tests**: Replay of a known signed document creates the event row and triggers Inngest. Replay of an unknown document returns 404. AM role gets 403.
- **Dependencies**: S1-22

### S1-25: GHL sub-account creation (direct API, replaces Pabbly)

- **Description**: Inngest function `create_ghl_subaccount` triggered by `pandadoc.signed`. Calls GHL agency API directly. Writes `platform_actions` row pre/post.
- **Tests**: Mocked GHL agency API returns a sub-account id; row recorded with `status=success`, `external_id` set, `clients.ghl_subaccount_id` populated. API failure marks `status=failed` with `last_error` and increments `retry_count`. Concurrency cap of 3 enforced (verified by parallel test events).
- **Dependencies**: S1-09, S1-22

### S1-26: Returning-client check + parent_client_id linking

- **Description**: Before sub-account creation in S1-25, lookup existing GHL contact by signed-document email. If found, set `parent_client_id` on the new client row to the existing client's id and skip sub-account creation; reuse the existing `ghl_subaccount_id`.
- **Tests**: First signing for an email creates a fresh client + sub-account. Second signing for the same email creates a new client row with `parent_client_id` set and reuses the existing GHL sub-account id; `platform_actions` records a `skipped_existing` outcome.
- **Dependencies**: S1-25

### S1-27: Sales call transcript capture - native (Zoom + Google Meet)

- **Description**: Webhook receivers for Zoom and Google Meet recording-completed events. Pull native transcript when available, store text in `documents` (R2 key) and trigger AI summary.
- **Tests**: Zoom mock event with native transcript stores transcript in R2 and creates a `documents` row of kind `transcript_text`. Same for Google Meet. Event with no transcript falls through to S1-28 path.
- **Dependencies**: S1-10, S1-19, plus R2 buckets created (covered by S1-21 env groups)

### S1-28: Sales call transcript fallback - OpenAI Whisper

- **Description**: When native transcript is missing, download recording audio, send to Whisper API, store text in R2, write `documents` row of kind `transcript_text`. Soft cost cap $50/mo with alert at 80%.
- **Tests**: Mocked recording with no native transcript triggers Whisper path; `documents` row created. Cost-tracker accumulator emits an alert at 80% of cap.
- **Dependencies**: S1-27

### S1-29: AI sales summary generator

- **Description**: Inngest function `summarise_sales_call`. Anthropic SDK call with `claude-opus-4-7`, prompt caching enabled (system prompt + few-shot examples cached, transcript not cached). Output validated against the Pydantic schema in PRD section 7.1.
- **Tests**: Given a fixture transcript, the function produces JSON matching the schema. Prompt-cache hit observed on the second call within 5 minutes (verified via response metadata). Schema-invalid output fails the function and surfaces in `platform_actions` with a clear `last_error`.
- **Dependencies**: S1-27 (transcript present)

### S1-30: Write summary to client_knowledge with embeddings

- **Description**: For each top-level field of the sales summary, insert one row into `client_knowledge` with `source=sales_call`. Compute embedding for `value_text` and store in `embedding` column.
- **Tests**: A summary with 7 fields produces 7 `client_knowledge` rows. Each row has a non-null embedding. Re-running the summariser for the same client + transcript does not duplicate rows (idempotent on `(client_id, source, key, captured_at)`).
- **Dependencies**: S1-07, S1-29

### S1-31: Dashboard `/clients` list view

- **Description**: Server-rendered table of every client with current_step, time-in-step, last platform action status, and a link to the detail page. 10s polling.
- **Tests**: Playwright: with 3 seeded clients in different steps, the list renders all 3 with correct steps. Polling fetches a fresh row when a fourth client is inserted server-side.
- **Dependencies**: S1-06, S1-17, S1-18

### S1-32: Dashboard `/clients/[id]` detail view (Sprint 1 slice)

- **Description**: Detail page shows client metadata, current_step, AI sales summary (if present), platform deep-links (placeholders for platforms not yet integrated). 5s polling on active fields.
- **Tests**: Playwright: opening a seeded client with a sales summary renders the structured summary fields. Opening a client without a summary shows a clear "no summary yet" state. Inngest run id deep-link opens the Inngest UI for a recorded action.
- **Dependencies**: S1-30, S1-31

### S1-33: GitHub Actions CI

- **Description**: PR workflow runs lint (`ruff` + `eslint`), typecheck (`mypy` + `tsc --noEmit`), and tests (`pytest` + `vitest` + Playwright). Per-PR Neon branch is created and torn down.
- **Tests**: A PR introducing a lint error fails CI. A PR adding a passing test passes CI. Per-PR Neon branch is visible in the Neon dashboard during the run and removed after merge/close.
- **Dependencies**: S1-01, S1-03

### S1-34: Auto-deploy to Render staging on merge to main

- **Description**: Render auto-deploy hook on push to `main`. Promotion to production is manual via the Render dashboard.
- **Tests**: A merge to `main` deploys all four staging services. The production services do not auto-deploy (verified by inspecting Render deploy logs).
- **Dependencies**: S1-04, S1-33

### S1-35: Sprint 1 acceptance verification

- **Description**: Walk through every PRD section 12.1 acceptance criterion against the staging environment with the Bullet team. Any failure resets the sprint sign-off.
- **Tests**: Acceptance log committed at `docs/sprint-1-acceptance.md` with each criterion ticked, evidence linked, and any deferred items recorded with rationale.
- **Dependencies**: S1-22, S1-23, S1-25, S1-26, S1-29, S1-31, S1-32, S1-34

---

## Sprint 2 (Weeks 3-4): Core Fan-Out + Follow-Up Email + MVP Milestone

Goal: Signed agreement creates every non-financial artefact automatically. AI-drafted post-kickoff email lives behind the PD review -> AM send hand-off. MVP demo to Bullet.

### S2-01: Slack incoming webhook integration

- **Description**: Adapter sending one-way notifications to `#bullet_inbound_clients` (and other channels via env config). New-signing notification includes deep links to dashboard, HubSpot, GHL, Drive, Asana, Calendar.
- **Tests**: Mocked Slack endpoint receives a well-formed message with all expected deep links on a `pandadoc.signed` event. Backoff retries on 429 (1 msg/sec rate limit) verified.
- **Dependencies**: S1-22

### S2-02: Asana integration - project + finance task + onboarding subtasks

- **Description**: Inngest function creates `Bullet Clients Status` project, finance task, onboarding subtasks per service-tier template. Stores `asana_project_id` and `asana_finance_task_id` on the client.
- **Tests**: Mocked Asana API call creates the expected hierarchy. Idempotency key prevents duplicate project on retry. Concurrency cap 5 enforced.
- **Dependencies**: S1-09, S1-22

### S2-03: Asana template checksum monitoring

- **Description**: Daily cron pulls each tracked Asana template, hashes the structure, and alerts Slack on drift.
- **Tests**: Mocked template change triggers an alert; unchanged template does not. Template registry stored in DB.
- **Dependencies**: S2-01, S2-02

### S2-04: Google Sheets - client row creation

- **Description**: Adapter writes a row in the `Client Status Sheet` matching today's schema, including the `SaaS Mode` passthrough column. Stores `sheet_row_id` on the client.
- **Tests**: Mocked Sheets API call creates a row with all current columns. Re-run for the same client updates the existing row, does not create a duplicate.
- **Dependencies**: S1-09, S1-22

### S2-05: Google Drive - folder tree creation + sharing

- **Description**: Creates the actively-used folder tree per client (legacy folders skipped pending plan section 11 Q-2). Applies sharing per service-account configuration. Stores `drive_folder_id` on the client.
- **Tests**: Mocked Drive API creates the expected folder structure. Sharing permissions match the configured spec. Idempotent on retry (returns existing folder id).
- **Dependencies**: S1-09, S1-22

### S2-06: Google Calendar - kick-off booking + change subscription

- **Description**: Books the kick-off call via the existing GHL calendar link. Subscribes to calendar-change events; date changes propagate to Asana finance task in S3-13 (groundwork now).
- **Tests**: Mocked Calendar API creates the booking and stores the event id. A simulated calendar webhook for a date change is received and persisted as an `onboarding_events` row of type `calendar.kickoff_changed`.
- **Dependencies**: S1-09, S1-22

### S2-07: HubSpot integration - contact read + stage mirror

- **Description**: Read contact fields used at PandaDoc template creation time. Mirror onboarding stage transitions back to HubSpot deal (optional but cheap to add now).
- **Tests**: Mocked HubSpot API returns contact data; client row populated with contact fields. Stage transition updates the deal stage; failure does not block the fan-out (writes a `failed` `platform_actions` row but the rest proceeds).
- **Dependencies**: S1-09, S1-22

### S2-08: Inngest fan-out workflow + per-platform concurrency

- **Description**: Single Inngest workflow function fans out to S2-01 through S2-07 in parallel with per-platform concurrency caps from PRD section 6.3.
- **Tests**: A `pandadoc.signed` event triggers all six fan-out actions and they execute concurrently subject to the caps. A burst of 20 simulated signings respects the per-platform caps (verified via Inngest run logs).
- **Dependencies**: S2-01, S2-02, S2-04, S2-05, S2-06, S2-07

### S2-09: Idempotency + retry policy enforcement

- **Description**: Every fan-out function obeys the PRD section 6.1 - 6.4 rules: idempotency_key, exponential backoff (1m, 5m, 15m, 1h, 6h), max 5 attempts, dead-letter on final failure.
- **Tests**: A function that fails 4 times then succeeds records 5 attempts and final `status=success`. A function that fails 5 times records `status=dead_lettered`. Slack escalation alert fires on attempt 3.
- **Dependencies**: S2-08

### S2-10: Dashboard `/actions` cross-client action health

- **Description**: View showing all `platform_actions` filtered by status (failed, retrying, dead-lettered). Per-row Inngest deep-link. 10s polling.
- **Tests**: Playwright: a seeded set of actions in mixed states renders correctly. Filter controls update the list. Inngest deep-link opens the correct run page.
- **Dependencies**: S1-31, S2-09

### S2-11: Manual retry button (dead-letter recovery)

- **Description**: Per-action retry button on `/actions` (founder + PD only). Re-enqueues the action via Inngest with a fresh attempt counter.
- **Tests**: Clicking retry on a dead-lettered action enqueues an Inngest event; on success the row flips to `status=success`. AM role does not see the button.
- **Dependencies**: S2-10

### S2-12: GHL OB V2 portal completion webhook

- **Description**: `POST /webhooks/ghl/portal` verifies signature, dedupes on `(event_type=ghl.portal_completed, external_id=response.id)`, persists `onboarding_events`.
- **Tests**: Valid event creates one row; replay creates none. Tampered signature rejected.
- **Dependencies**: S1-08, S1-19

### S2-13: Portal data ingestion to client_knowledge

- **Description**: Inngest function transforms the GHL portal payload into structured `client_knowledge` rows with `source=portal` and computed embeddings.
- **Tests**: Sample portal payload produces the expected set of knowledge rows. Re-running for the same response id is idempotent.
- **Dependencies**: S1-30, S2-12

### S2-14: `campaign_flow_type` field + PD selection UI

- **Description**: PD picks `low_ticket_checkout` or `high_ticket_consultation` on the client detail page. Default suggestion inferred from portal answers (`group_size=small AND consultation_price>0` -> high-ticket; else low-ticket). Audit-logged.
- **Tests**: PD can select a value; AM cannot. Default suggestion populates from portal data. Selection writes to `clients.campaign_flow_type` and an `audit_log` row.
- **Dependencies**: S1-32, S2-13

### S2-15: Pricing calculator - subscription anchor

- **Description**: Pure function: `anchor_value = monthly_price / 30.4 * offer_days`. Returns the worked breakdown alongside the result.
- **Tests**: `£200 / 30.4 * 21` returns `£138.16` to two decimal places. Edge cases: zero offer_days returns 0; negative inputs raise. Worked-string contains the inputs and result.
- **Dependencies**: n/a

### S2-16: Pricing calculator - class-pack anchor

- **Description**: Pure function: `anchor_value = drop_in_rate * class_count`.
- **Tests**: `£30 * 5 = £150.00`. Worked-string format matches the subscription anchor's contract for consistent dashboard rendering.
- **Dependencies**: n/a

### S2-17: Kick-off transcript capture

- **Description**: Mirror of S1-27 + S1-28 for kick-off calls. Triggers AI follow-up email generation.
- **Tests**: Kick-off recording event captures transcript via native or Whisper. `documents` row created with kind `transcript_text`. Triggers the appropriate variant generator.
- **Dependencies**: S1-27, S1-28

### S2-18: Kick-off email generator - low-ticket variant

- **Description**: Anthropic call with kick-off transcript + portal answers + sales summary + brand assets + historical-offers corpus. Combines S2-15 / S2-16 outputs. Produces JSON per PRD section 7.2 plus calculation transparency string.
- **Tests**: Fixture inputs produce JSON validating against the schema. `total_value`, `savings`, `percent_off` computed correctly. Three offer-name suggestions returned. Calculation transparency string includes every input and the final result.
- **Dependencies**: S2-15, S2-16, S2-17, S1-30

### S2-19: Kick-off email generator - high-ticket variant

- **Description**: Anthropic call producing JSON per PRD section 7.3 (prose-only, no maths block).
- **Tests**: Fixture inputs produce schema-valid JSON. No `total_value` / `savings` / `percent_off` fields present. `email_body` references the agreed offer structure, ad budget, start date, creative requirements, kickoff date.
- **Dependencies**: S2-17, S1-30

### S2-20: Dashboard - `Ready for PD Review` state

- **Description**: Client detail surfaces the AI draft, suggested offer names (low-ticket only), worked pricing or prose, with edit controls. Status badge `Ready for PD Review`.
- **Tests**: Playwright: a client with a generated low-ticket draft renders all three offer-name suggestions and the worked maths. PD edits the email body and the change persists. AM cannot edit, only view.
- **Dependencies**: S1-32, S2-18, S2-19

### S2-21: PD confirm action -> `Ready for AM to Send`

- **Description**: PD clicks Confirm; status transitions; an `audit_log` row is written with the before/after values.
- **Tests**: Confirm transitions the client to `Ready for AM to Send`. AM role does not see the Confirm button. Audit-log row captured.
- **Dependencies**: S2-20

### S2-22: AM Send action -> Resend dispatch

- **Description**: AM clicks Send; Resend API call with the confirmed email body. `documents` row of kind `kickoff_followup_email_body` created. `platform_actions` records the send.
- **Tests**: Mocked Resend send returns success; client transitions to `Sent`; document row created. Send failure surfaces in `/actions`. Idempotency key prevents double-send on a click stutter.
- **Dependencies**: S2-21

### S2-23: Resend domain setup (SPF / DKIM / DMARC)

- **Description**: Configure DNS records on `bulletdigitalmedia.com` for Resend. Verify status in Resend dashboard.
- **Tests**: Resend dashboard reports all three records as verified. A test send to a Gmail address arrives without spam-folder routing and passes SPF/DKIM/DMARC checks (verified in raw headers).
- **Dependencies**: n/a (DNS is owned by Bullet; coordinate via Chris)

### S2-24: React Email templates

- **Description**: React Email components for: auth confirmation (S1-14), kick-off follow-up email (both variants), tech-requirements email (Sprint 3), dashboard alerts.
- **Tests**: Each template renders to HTML and to plain-text fallback. Snapshot tests pin the rendered output. Variables interpolated correctly in fixtures.
- **Dependencies**: S2-23

### S2-25: Resend reply handling + bounce/complaint webhook

- **Description**: `POST /webhooks/resend` records bounces and complaints into `audit_log`. Reply routing rule lands replies in the configured shared inbox.
- **Tests**: Mocked bounce webhook creates an audit_log row. Complaint webhook same. Manual test send + reply lands in the configured inbox.
- **Dependencies**: S2-22, S2-23

### S2-26: End-to-end happy path test

- **Description**: Playwright + API integration test that simulates a PandaDoc signing event and asserts every Sprint 1 + Sprint 2 fan-out completes successfully without human intervention. Asserts the kick-off email path through PD review -> AM send.
- **Tests**: A scripted run from `pandadoc.signed` to `Sent` produces a green run with all expected `platform_actions` rows in `success` and the dashboard reflecting the final state.
- **Dependencies**: S2-08, S2-22

### S2-27: MVP demo to Bullet team

- **Description**: Live walkthrough on staging. Validate against a simulated onboarding flow with the Bullet team observing. Capture feedback into `docs/dashboard-feedback/client-feedback-log.md`.
- **Tests**: Demo checklist signed off by John or Stephen; feedback log updated; any P0 issues converted into Sprint 3 tasks before sprint sign-off.
- **Dependencies**: S2-26

---

## Sprint 3 (Weeks 5-6): Financial Integrations + Comms + Legacy Replacements

Goal: Stripe, Xero, Timely live. Outstanding-elements GHL workflow replaced with the asset checklist + single conditional email. Calendar -> Asana date propagation eliminates the Monday manual sync.

### S3-01: Stripe customer creation + UK / International routing

- **Description**: Inngest function creates a Stripe customer on the appropriate account (UK or International) based on `clients.legal_entity`. Stores `stripe_customer_id`.
- **Tests**: Mocked Stripe API: UK client creates customer on UK account; International on the other. Idempotency key prevents duplicate creation.
- **Dependencies**: S2-08

### S3-02: Stripe `payment_method.attached` webhook

- **Description**: `POST /webhooks/stripe` verifies signature, persists `payment_method.attached`, attaches the method to the customer.
- **Tests**: Valid webhook with signature attaches PM; tampered signature rejected; replay is idempotent.
- **Dependencies**: S3-01

### S3-03: Stripe deferred subscription activation

- **Description**: Subscription created only after the kick-off follow-up email is sent (S2-22). Inngest function listens for `client.kickoff_email_sent` event and creates the subscription with native Stripe idempotency.
- **Tests**: Subscription created exactly once per client. Premature trigger (before send) does not create one. Failure surfaces in `/actions`.
- **Dependencies**: S2-22, S3-02

### S3-04: Stripe Amex flagging in dashboard

- **Description**: Detect Amex payment method via Stripe webhook brand field. Surface a distinct Amex flag on the client detail page; pipeline does not stall on missing PM for Amex clients.
- **Tests**: Amex test PM triggers the flag; Visa does not. Pipeline continues for an Amex client without a captured PM (subscription activation deferred to manual capture, recorded in audit log).
- **Dependencies**: S3-02

### S3-05: Xero OAuth setup + token refresh

- **Description**: OAuth 2.0 flow with offline access. Tokens stored encrypted in env-protected DB rows; refresh handled automatically.
- **Tests**: Initial OAuth handshake succeeds (manual). Refresh token swap after expiry succeeds without manual intervention. Token-revocation case logs cleanly and alerts Slack.
- **Dependencies**: n/a

### S3-06: Xero contact creation with chart-of-accounts + tracking categories

- **Description**: Inngest function creates a Xero contact with the appropriate chart-of-accounts and tracking categories per `legal_entity`. Stores `xero_contact_id`.
- **Tests**: Mocked Xero API: UK and International route to different chart-of-accounts (exact rule pending plan Q-3). Idempotent. Rate limit (60 req/min) backoff verified.
- **Dependencies**: S3-05, S2-08

### S3-07: Timely client + project creation

- **Description**: Inngest function creates a Timely client and project; project budget = `monthly_fee_usd / 100` hours. Stores `timely_client_id` and `timely_project_id`. Team assignment remains a dashboard-driven action.
- **Tests**: Mocked Timely API: client and project created with the correct budget. Idempotent. Concurrency cap 3 enforced.
- **Dependencies**: S2-08

### S3-08: `client_assets` population per client

- **Description**: On `pandadoc.signed`, populate `client_assets` rows from a configurable per-service-tier template (e.g. `ad_account_access`, `regulatory_docs`, `headshot`, `brand_guidelines`, `face_to_camera_video`, `logo_files`, etc.).
- **Tests**: A signed agreement for a known service tier creates the expected set of `client_assets` rows in `status=required`. Idempotent on replay.
- **Dependencies**: S2-08

### S3-09: Dashboard asset checklist UI

- **Description**: Per-client live checklist: asset type, status, requested at, source url. Actions: mark received, mark approved, mark waived, send chase email.
- **Tests**: Playwright: PD or AM can mark received; status flips; audit-log row written. Mark waived requires a note (enforced).
- **Dependencies**: S3-08, S1-32

### S3-10: Single conditional asset-chase email template

- **Description**: One React Email template with conditional blocks for each outstanding asset type. Replaces the 16-branch GHL Outstanding Elements workflow entirely.
- **Tests**: Snapshot tests for representative asset combinations (e.g. only headshot missing; ad account + regulatory docs missing; everything required). Manual send to a test inbox renders correctly.
- **Dependencies**: S2-24

### S3-11: Send chase email action wired to template

- **Description**: "Send chase email" button on the asset checklist composes the conditional email from current `client_assets` state and sends via Resend.
- **Tests**: A client with two outstanding assets receives an email referencing exactly those two. `platform_actions` records the send. Idempotency window of 30 minutes prevents accidental double-chase.
- **Dependencies**: S3-09, S3-10

### S3-12: Preserve existing GHL conditional workflows

- **Description**: Continue triggering existing GHL workflows where they remain (post-signing portal link, survey reminders). No rebuild; just ensure the trigger fires.
- **Tests**: A `pandadoc.signed` event still triggers the post-signing portal link workflow in GHL (verified via GHL workflow logs in staging). Survey-reminder cadence unchanged.
- **Dependencies**: S2-08

### S3-13: Calendar -> Asana date propagation

- **Description**: Calendar-change webhook (S2-06) updates the Asana finance task due date idempotently. Eliminates Steve's Monday manual sync.
- **Tests**: Simulated calendar reschedule for a known client updates the Asana finance task date. Re-running the same change is a no-op. Failure surfaces in `/actions`.
- **Dependencies**: S2-02, S2-06

### S3-14: Sprint 3 acceptance verification

- **Description**: Walk through every PRD section 12.3 acceptance criterion against staging with the Bullet team.
- **Tests**: Acceptance log committed at `docs/sprint-3-acceptance.md` with each criterion ticked and evidence linked.
- **Dependencies**: S3-01, S3-03, S3-04, S3-06, S3-07, S3-09, S3-10, S3-11, S3-12, S3-13

---

## Sprint 4 (Weeks 7-8): Research Agent + Polish + Pilot

Goal: Research agent live, dashboard polish complete, three to five pilot clients onboarded end-to-end through the production system, agreement-to-go-live measured.

### S4-01: Firecrawl integration - client website scrape

- **Description**: Tool function `scrape_website(url)` calls Firecrawl, stores raw HTML in R2 (`clients/{id}/scraped/{hash}.html`), returns markdown + metadata.
- **Tests**: Real Firecrawl scrape against a fixture URL returns markdown; R2 object created. Retry on transient failure. Cost-cap alerting in place.
- **Dependencies**: S1-21 (R2 buckets)

### S4-02: Claude `web_search` competitor discovery

- **Description**: Tool function `search_competitors(location, business_type)` uses Claude's built-in `web_search` to discover competing gyms/studios. Returns up to five candidates with name, url, distance.
- **Tests**: Fixture input returns at least three candidates with required fields. Distances computed against the client's geocoded address.
- **Dependencies**: n/a

### S4-03: Firecrawl - competitor page scrape

- **Description**: Tool function `scrape_competitor_page(url)` fetches a competitor's page and extracts services, pricing signals, USPs.
- **Tests**: Fixture competitor URL produces the expected structured fields. Failure on a 404 surfaces cleanly to the agent loop.
- **Dependencies**: S4-01

### S4-04: Meta Marketing API - audience size

- **Description**: Tool function `get_meta_audience_size(geo, demographics)` returns an estimated audience size. Aggressive caching to respect quota.
- **Tests**: Fixture geo returns a numeric estimate. Cache hit on repeat call within TTL. Rate-limit backoff verified.
- **Dependencies**: n/a

### S4-05: Research agent (Claude Agent SDK) tool loop

- **Description**: Claude Agent SDK orchestration that calls S4-01 to S4-04 per client to produce the four output blocks per PRD section 7.4.
- **Tests**: End-to-end run for a fixture client produces a `research_results` row of each kind (`website_summary`, `competitors`, `audience_size`, `offer_suggestions`). Output schemas validate.
- **Dependencies**: S4-01, S4-02, S4-03, S4-04

### S4-06: Dashboard `/clients/[id]/research` view

- **Description**: Renders all four research output blocks with citations. Surfaced ahead of the kick-off call in the client detail page header.
- **Tests**: Playwright: a client with research results renders all four blocks with citation links. A client without research shows a clear empty state.
- **Dependencies**: S4-05, S1-32

### S4-07: Dashboard `/clients/[id]/knowledge` semantic search

- **Description**: Per-client knowledge view: structured fact list + free-text semantic search using pgvector.
- **Tests**: Playwright: search "pricing" returns rows whose embeddings rank highest. Filter by `source` works. Empty query renders the structured fact list.
- **Dependencies**: S1-30

### S4-08: Dashboard polish - time-in-step and bottleneck view

- **Description**: Per-client time-in-step indicator on `/clients`. Aggregate bottleneck view showing which steps clients sit in longest.
- **Tests**: Time-in-step computed correctly from `step_entered_at`. Bottleneck view aggregates across the active client population.
- **Dependencies**: S1-31, S2-10

### S4-09: Dashboard `/admin/users` and `/admin/integrations`

- **Description**: User management (founder + PD only) and per-platform credential health view (founder only). Integration health: last successful call timestamp, recent failure count.
- **Tests**: PD can manage users; AM gets 403. Integration health polls every 30s. A simulated credential failure flips the indicator within one polling cycle.
- **Dependencies**: S1-32

### S4-10: Reconciliation runbooks

- **Description**: Markdown runbooks under `docs/runbooks/` covering: PandaDoc reconciliation discrepancy, dead-letter recovery, Stripe webhook gap, Xero token revocation, Resend deliverability incident.
- **Tests**: Each runbook walks through a scripted scenario. A new engineer can follow each runbook end-to-end without external clarification (validated by a peer review walkthrough).
- **Dependencies**: S1-23, S2-11, S3-02, S3-05, S2-25

### S4-11: R2 lifecycle rules

- **Description**: 12-month auto-purge for transcript audio in `bullet-prod-artefacts`.
- **Tests**: Lifecycle rule applied; verified via Cloudflare dashboard. A test object with a backdated timestamp is purged on next lifecycle run.
- **Dependencies**: S1-21

### S4-12: Production deployment + cutover prep

- **Description**: Promote all services from staging to production (api, worker, cron, dashboard). Production Neon, R2, Inngest envs in place. Final secrets review. Pre-pilot smoke test.
- **Tests**: Production `/healthz` returns 200 across all four services. A canary `pandadoc.signed` event in production fan-outs to all integrations end-to-end. Staging continues to work as the pre-prod env.
- **Dependencies**: S3-14

### S4-13: Pilot - 3 to 5 real clients onboarded end-to-end

- **Description**: Coordinate with Bullet to route the next three to five new signings through the production system. Bullet team self-serves the dashboard.
- **Tests**: Each pilot client reaches `current_step=live` via the automated flow. Any human interventions are logged in `audit_log` with reason. No silent partial failures.
- **Dependencies**: S4-12

### S4-14: Measure agreement-to-go-live and capture pilot results

- **Description**: For each pilot client, compute the time from `pandadoc.signed` to `current_step=live`. Capture results in `docs/pilot-results.md` against the baseline (~2 weeks) and target (1 day on the happy path).
- **Tests**: `docs/pilot-results.md` committed with per-client metrics, observed bottlenecks, and recommended Phase 2 follow-ups.
- **Dependencies**: S4-13

### S4-15: Stretch - GHL AI builder feasibility evaluation

- **Description**: Evaluate whether GHL's AI builder API exposes the hooks needed to feed our knowledge profile into automated funnel/page generation. Document feasibility for Phase 2 in `docs/ghl-ai-builder-feasibility.md`.
- **Tests**: Feasibility doc committed with API surface mapped, blockers listed, and a recommendation (build / defer / drop).
- **Dependencies**: n/a

### S4-16: Sprint 4 acceptance verification + Phase 1 sign-off

- **Description**: Walk through every PRD section 12.4 acceptance criterion against production. Confirm every Phase 1 success criterion (PRD section 1.4, plan section 10) is met or has an explicit deferral logged.
- **Tests**: Acceptance log committed at `docs/sprint-4-acceptance.md`. Phase 1 sign-off recorded in `docs/CHANGELOG.md` under the appropriate dated entry.
- **Dependencies**: S4-05, S4-06, S4-07, S4-08, S4-09, S4-10, S4-11, S4-13, S4-14

---

## Cross-cutting reminders

- **TDD discipline**: For every task above, the test descriptions are the contract. Write tests first, watch them fail, then implement, then watch them pass.
- **Changelog**: Every task that introduces a decision, discovery, addition, change, removal, or fix must update `docs/CHANGELOG.md` in the same PR.
- **Open questions**: Any new blocking implementation question discovered during a sprint goes into `docs/openquestions.md` immediately, with the next free `Q-NN` id.
- **Q-01 assumption**: Resend with a single system mailbox is assumed throughout. If Bullet later picks per-AM Gmail, tasks `S2-22`, `S2-23`, `S2-24`, `S2-25`, `S3-10`, `S3-11` will be revised; nothing else shifts.

---

*Prepared by IzzyAgents | AI Solutions Consultancy*
