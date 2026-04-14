# Bullet Digital Media - Phase 1 Plan: Onboarding Process Automation

**Prepared by**: IzzyAgents Technical Team
**Date**: 14/04/2026
**Status**: Draft - For Client Review
**Source brief**: `emails/Bullet Onboarding Process.pdf` (John Limber, 13/04/2026)

---

## 1. Overview

Phase 1 delivers an **end-to-end automation of Bullet Digital Media's client onboarding process** - from the first sales call through to campaign go-live. The current process spans around 3 weeks and touches roughly a dozen platforms (HubSpot, PandaDoc, GoHighLevel, Asana, Stripe, Xero, Timely, Slack, Google Workspace, Meta, Canva, Loom). Bullet's stated ambition is to compress this to **a single day** without sacrificing quality.

Previous Phase 1 scope (internal knowledge bank and client-facing Telegram bot) is deferred to a later phase. Those artefacts are preserved in `docs/archive/` for future reference.

### Phase 1 Goals

- Remove manual handoffs between platforms during onboarding
- Trigger every downstream action automatically from a single signing event
- Shrink agreement-to-go-live from two weeks toward one day
- Give every team member live visibility into each client's onboarding status
- Reduce information gaps and rework during kick-off and build

### What Phase 1 Does NOT Include

- Strategy recommendations or autonomous strategic advice
- Internal client knowledge bank (moved to a later phase)
- Client-facing Telegram AI bot (moved to a later phase)
- "Steve AI" digital twin (future phase)
- Productised AI platform (future phase)

---

## 2. Current Onboarding Process (As-Is)

Reproduced from John's email for reference. Each step has platform touchpoints that are candidates for automation.

| # | Step | Platforms | Typical Duration |
|---|------|-----------|------------------|
| 1 | Sales Call | Zoom (moving to Google Meet) | 30 to 60 min call + follow-up |
| 2 | Agreement | HubSpot + PandaDoc (e-signature) | 1 to 3 days to signature |
| 3 | Signing & Onboarding Portal | GoHighLevel + Asana + Stripe + Xero + Timely + Slack + Google Sheets/Docs/Drive + Gmail + Google Calendar | Same-day trigger, ~3 days client fill-in |
| 4 | Kick-Off Call | Same as Step 3, plus call tooling | 45 to 60 min call, 3 to 4 days after signing |
| 5 | The Build | GoHighLevel + Meta Business Manager + Canva | ~14 days |
| 6 | Campaign Guide & Go Live | GoHighLevel + Meta + Canva + Loom | 1 to 2 days |

**Best-case total**: ~3 weeks sales call to go live (~2 weeks agreement to go live).
**Target**: agreement to go live in 1 day.

---

## 3. Phase 1 Scope - What We Are Automating

The focus is the **signing trigger** in Step 3, which fans out into every other system. Getting this single event right unlocks most of the time savings.

### 3.1 Trigger Orchestration Layer (Primary Deliverable)

A central orchestration service that listens for the signing event (PandaDoc webhook, or HubSpot event once agreements move in-house) and fans out to every downstream platform in parallel. Today this is a mixture of manual and semi-manual work; Phase 1 makes it a single reliable pipeline.

On a successful signing event, the orchestrator performs:

| Action | System | Notes |
|--------|--------|-------|
| Post new-client notification | Slack | To the correct team channel, with links to all created records |
| Create onboarding task list | Asana | Template per service tier, assigned to the correct team members |
| Create client row | Google Sheet (`Client Status Sheet`) | Matches current schema, with live status field |
| Create client folders | Google Drive | Shared structure the client can drop assets into |
| Create client record | Timely | For time tracking |
| Create client record | Xero | Financial profile |
| Store payment method | Stripe | Captured from portal, ready for Step 4 charge |
| Create onboarding info doc | Google Docs | Populated from sales notes + portal answers |
| Send technical requirements email | Gmail (via GoHighLevel) | Conditional branching based on what is required per client |
| Book kick-off call | Google Calendar | Uses existing GHL calendar link, pre-fills attendees |

Each step is **idempotent, retryable, and individually auditable**, so a partial failure never leaves a client half-onboarded.

### 3.2 Sales Call Intelligence (Supporting Deliverable)

Bullet already runs sales call transcripts through AI ad-hoc. Phase 1 formalises this:

- Zoom/Google Meet transcript captured automatically
- AI generates a structured sales summary (business type, goals, budget, red flags, next steps)
- Summary posted into the client's Google Doc automatically at Step 3, so the digital specialist starts from a filled template rather than a blank page

### 3.3 Onboarding Status Dashboard (Supporting Deliverable)

A lightweight internal dashboard (web) that surfaces a single row per client with:

- Which step they are on (1 to 6)
- Which automated actions succeeded, failed, or are pending
- Links into every connected platform (HubSpot, PandaDoc, GHL, Asana, Drive folder, Google Doc, Stripe customer, Xero contact, Timely client, Slack thread, Meta ad account, Calendar event)
- Time-in-step tracking so bottlenecks are visible

This becomes the single source of truth for "where is this client?" replacing scattered lookups.

### 3.4 Kick-Off Summary Generator (Supporting Deliverable)

After the Step 4 kick-off call, AI generates the detailed written summary the digital specialist currently writes manually. The specialist reviews and edits rather than drafts from scratch. The same flow triggers Stripe's recurring payment activation.

### 3.5 Campaign Guide Assembly (Stretch / Evaluate in Phase 1)

Depending on complexity, the system can auto-assemble the Campaign Guide template in Canva/GHL from the data captured in Steps 3-5, leaving only the creative review for the digital specialist. Scoped as a stretch goal - confirm feasibility in Sprint 2.

---

## 4. Technology Stack (Proposed)

| Layer | Technology | Why |
|-------|-----------|-----|
| **Orchestration** | Python (FastAPI) + Celery + Redis | Async webhook ingestion, durable job queue for cross-platform fan-out, retries with backoff |
| **Database** | Neon PostgreSQL | Single source of truth for client state, step status, platform IDs, audit log |
| **Dashboard** | Next.js + Tailwind + Polaris-style components | Internal tool, dark mode default |
| **AI (transcripts & summaries)** | Claude (Anthropic) | Sales summaries, kick-off summaries |
| **Transcription** | OpenAI Whisper | Zoom/Google Meet recordings |
| **Hosting** | Render.com | Web service + worker + cron |

### Platform Integrations (Read / Write)

| Platform | Direction | Mechanism |
|----------|-----------|-----------|
| HubSpot | Read/Write | Official API + webhooks |
| PandaDoc | Read (webhook) | Signing webhook |
| GoHighLevel | Read/Write | API + webhooks for portal completion |
| Asana | Write | API (task list templates) |
| Stripe | Write | API (customers, payment methods, subscriptions) |
| Xero | Write | API (contacts) |
| Timely | Write | API (clients) |
| Slack | Write | Incoming webhooks |
| Google Sheets | Write | Google Sheets API |
| Google Docs | Write | Google Docs API (template + merge) |
| Google Drive | Write | Drive API (folder creation + sharing) |
| Gmail | Send | Gmail API or GoHighLevel email automation (to preserve existing conditional logic) |
| Google Calendar | Write | Calendar API |
| Meta Business Manager | Read (later step) | Marketing API (ad account confirmation only in Phase 1) |

---

## 5. How It Works - End-to-End Flow

```
                                  SIGNING EVENT
                                        |
                                        v
                   +--------------------+--------------------+
                   |     Orchestration Service (FastAPI)     |
                   |  - Verifies webhook signature           |
                   |  - Writes client record + step state    |
                   |  - Enqueues fan-out jobs                |
                   +--------------------+--------------------+
                                        |
             +----------+-------+-------+-------+----------+----------+
             v          v       v       v       v          v          v
          Slack      Asana   Sheet   Drive   Timely      Xero      Stripe
                                        |
                                        v
                                   Google Docs
                                        |
                                        v
                              Gmail / GHL email
                                        |
                                        v
                              Google Calendar (kick-off)

          +-----------------------------------------------------+
          |            Onboarding Status Dashboard              |
          |  - Per-client step tracker                          |
          |  - Per-action success / failure / retry             |
          |  - Deep links into every connected platform        |
          |  - Time-in-step + bottleneck surfacing              |
          +-----------------------------------------------------+
```

Every job writes its outcome (success, failure, retry count, external ID) back to Postgres. The dashboard reads from Postgres. Nothing is inferred from live platform state at view-time - the database is the record.

---

## 6. Data Model (Core Tables)

| Table | Purpose |
|-------|---------|
| `clients` | One row per client. Includes current step, stage timestamps, canonical IDs in each platform |
| `onboarding_events` | Append-only log of every trigger received (sales call booked, signed, portal complete, kick-off done, build complete, gone live) |
| `platform_actions` | One row per fan-out job. Stores target platform, payload, status (pending/success/failed/retrying), external ID, retry count, last error |
| `documents` | Google Docs / Drive / Canva artefacts linked to each client |
| `users` | Internal team members with role-based access to the dashboard |
| `audit_log` | Who did what, when, from which system |

Row-level security is scoped per team role rather than per tenant (single-tenant Bullet-internal tool), but audit logging remains comprehensive.

---

## 7. Risks, Limitations & Dependencies

### Critical Risks

**Partial fan-out failure**
- *What could happen*: Signing triggers 10 actions; 2 fail silently. Team assumes the client is onboarded.
- *Mitigation*: Every action returns success/failure, dashboard surfaces partial states loudly, failed actions auto-retry with backoff then escalate to Slack.

**Webhook duplication / loss**
- *What could happen*: PandaDoc or HubSpot resend a webhook, causing duplicate clients; or deliver once and fail, losing the signing event.
- *Mitigation*: Idempotency keys on every action, webhook replay endpoint for manual recovery, daily reconciliation job cross-checks signed agreements against created clients.

**API rate limits across many platforms**
- *What could happen*: A burst of new signings hits Asana/Google/GHL rate limits, causing cascading failures.
- *Mitigation*: Queue-based architecture with per-platform concurrency caps, exponential backoff, and visible dashboard status.

### High Risks

| Risk | Mitigation |
|------|-----------|
| **Credential sprawl** - 12+ platforms each need stored tokens | Centralised secret management, documented rotation schedule, least-privilege scopes per integration |
| **Platform change of shape** - HubSpot / GHL / Asana change APIs | Version-pinned SDKs, contract tests against each provider, alerts on schema drift |
| **Template drift** - Google Doc / Asana templates evolve manually | Templates stored by ID with checksum monitoring; any drift flagged in dashboard |
| **Data currency** - signing before payment info captured | Orchestrator waits for Stripe payment method before triggering financial provisioning |
| **Agreement move from PandaDoc to HubSpot mid-project** | Abstraction layer at the signing-webhook boundary means only the adapter changes |

### Known Platform Limitations

- **PandaDoc webhooks** are best-effort; must be paired with polling / reconciliation.
- **GoHighLevel conditional email logic** is worth preserving in GHL rather than rebuilding in code; the orchestrator triggers the correct GHL workflow rather than sending directly.
- **Google Docs template merge** works well for structured fields; free-form strategy sections still need human authoring in Phase 1.

---

## 8. Build Sequence (Indicative)

Detailed TDD task breakdown to follow in `docs/sprint-plan.md` once this architecture is approved.

### Sprint 1 (Weeks 1-2): Foundation & Instrumentation
- Project scaffold, Postgres schema, auth, dashboard shell
- HubSpot + PandaDoc webhook ingestion (verified, idempotent, logged)
- Read-only view: every signed agreement appearing in the dashboard with status "captured"

### Sprint 2 (Weeks 3-4): Core Fan-Out
- Slack, Asana, Google Sheet, Google Drive, Google Doc (from template), Google Calendar integrations
- Orchestration engine with retries and the per-action status UI
- End-to-end happy path: signed agreement creates all non-financial artefacts automatically

### Sprint 3 (Weeks 5-6): Financial & Communication Fan-Out
- Stripe (customer + payment method + deferred subscription activation)
- Xero (contact)
- Timely (client)
- Gmail / GHL conditional technical requirements emails
- Kick-off Summary Generator (post-call AI summary + Stripe subscription activation)

### Sprint 4 (Weeks 7-8): Sales Intelligence, Polish & Pilot
- Sales Call Intelligence (transcript -> structured summary -> Google Doc)
- Onboarding status dashboard (time-in-step, bottleneck view, per-platform health)
- Reconciliation job + operational runbooks
- Pilot with 3 to 5 real new clients, measure agreement-to-go-live time

Campaign Guide Assembly (3.5) scoped as stretch inside Sprint 4 if time permits.

---

## 9. Pre-Development Requirements

| Requirement | Owner | Status | Blocker? |
|-------------|-------|--------|----------|
| HubSpot API access + scope list | Bullet Digital Media | Not started | Sprint 1 |
| PandaDoc webhook + API token | Bullet Digital Media | Not started | Sprint 1 |
| GoHighLevel API access + workflow IDs to trigger | Bullet Digital Media | Not started | Sprint 2 |
| Google Workspace service account + domain-wide delegation | Bullet Digital Media | Not started | Sprint 2 |
| Slack incoming webhook per channel | Bullet Digital Media | Not started | Sprint 2 |
| Asana token + onboarding task templates | Bullet Digital Media | Not started | Sprint 2 |
| Stripe restricted API key | Bullet Digital Media | Not started | Sprint 3 |
| Xero OAuth connection | Bullet Digital Media | Not started | Sprint 3 |
| Timely API token | Bullet Digital Media | Not started | Sprint 3 |
| Claude API key | Bullet Digital Media | John committed | Sprint 4 |
| OpenAI API key (Whisper) | IzzyAgents | Not started | Sprint 4 |
| Sample sales-call recording | Bullet Digital Media | Not started | Sprint 4 (helpful) |
| Decision: agreements stay in PandaDoc vs move to HubSpot | Bullet Digital Media | Under review | Affects Sprint 1 adapter |

---

## 10. Success Criteria (Phase 1)

Phase 1 is complete when:

- [ ] A signed agreement reliably triggers all 10 downstream actions with no manual intervention
- [ ] The onboarding status dashboard shows every active client's step, status, and platform links
- [ ] Partial failures are surfaced, retried, and recoverable without data loss
- [ ] Sales call transcripts auto-populate the onboarding Google Doc
- [ ] Kick-off call summaries are AI-drafted and Stripe subscription activation is automated
- [ ] 3 to 5 pilot clients onboarded end-to-end through the automated flow
- [ ] Measured agreement-to-go-live time on pilot clients vs baseline (2 weeks)

---

## 11. Open Questions For Steve

From John's email, Step 3 was flagged as needing a dedicated call with Steve to clarify the behind-the-scenes triggers. Specific items to close out:

1. Which Asana workspace and template IDs are used today?
2. Which GoHighLevel workflows own the conditional technical requirements emails? We want to trigger these rather than replace them.
3. Is there a standard Google Drive folder structure per client, or does it vary?
4. Which Google Sheet is the `Client Status Sheet`, and what is its exact schema?
5. Who receives the Slack notifications today, and on which channels?
6. Timely - is a client created per engagement, or per billing account?
7. Xero - are all clients on the same chart of accounts / tracking categories?
8. Does Stripe capture happen at signing (Step 3) or at kick-off (Step 4)? The email suggests both.
9. Confirm the Zoom -> Google Meet migration timeline so the transcript capture is built against the correct provider.
10. Any compliance requirements around storing sales call transcripts?

---

## 12. Next Steps

1. Walk Bullet through this plan and confirm scope alignment
2. Book the follow-up call with Steve to resolve Section 11 open questions
3. Decide PandaDoc-vs-HubSpot agreement location before Sprint 1 starts
4. Begin credential and template gathering per Section 9
5. Confirm pilot clients for Sprint 4

---

*Prepared by IzzyAgents | AI Solutions Consultancy*
