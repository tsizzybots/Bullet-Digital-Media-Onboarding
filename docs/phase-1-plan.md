# Bullet Digital Media - Phase 1 Plan: Onboarding Process Automation

**Prepared by**: IzzyAgents Technical Team
**Date**: 16/04/2026
**Status**: Draft v2 - Revised after 08/04/2026 meeting

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
- Build a per-client knowledge profile that accumulates from every touchpoint, laying the foundation for future AI agents

### What Phase 1 Does NOT Include

- Strategy recommendations or autonomous strategic advice
- Internal client knowledge bank as a standalone product (moved to a later phase, though the per-client knowledge model in this phase is the foundation)
- Client-facing Telegram AI bot (moved to a later phase)
- "Steve AI" digital twin (future phase)
- Productised AI platform (future phase)

---

## 2. Current Onboarding Process (As-Is)

Reproduced from John's email and confirmed during the 08/04/2026 meeting walkthrough. Each step has platform touchpoints that are candidates for automation.

| # | Step | Platforms | Typical Duration |
|---|------|-----------|------------------|
| 1 | Sales Call | Zoom (moving to Google Meet) | 30 to 60 min call + follow-up |
| 2 | Agreement | HubSpot + PandaDoc (e-signature) | 1 to 3 days to signature |
| 3 | Signing & Onboarding Portal | GoHighLevel + Asana + Stripe + Xero + Timely + Slack + Google Sheets/Docs/Drive + Gmail + Google Calendar | Same-day trigger, ~3 days client fill-in |
| 4 | Kick-Off Call | Same as Step 3, plus call tooling | 45 to 60 min call, 3 to 4 days after signing |
| 5 | The Build | GoHighLevel + Meta Business Manager + Canva | ~14 days (see note on GHL AI builder below) |
| 6 | Campaign Guide & Go Live | GoHighLevel + Meta + Canva + Loom | 1 to 2 days |

**Best-case total**: ~3 weeks sales call to go live (~2 weeks agreement to go live).
**Target**: agreement to go live in 1 day.

**Note on Step 5**: John confirmed in the 08/04/2026 meeting that GoHighLevel's recently released AI builder can now generate funnels, landing pages, and campaign assets in approximately 30 seconds - work that previously took the team up to 3 weeks. This dramatically reduces the Build phase duration and shifts the bottleneck further toward Steps 1-4 (the onboarding intake), reinforcing the decision to prioritise onboarding automation.

---

## 3. Phase 1 Scope - What We Are Automating

The focus is the **signing trigger** in Step 3, which fans out into every other system. Getting this single event right unlocks most of the time savings.

Phase 1 is structured around an **MVP milestone at the end of Sprint 2** (see Section 8) that delivers demonstrable value before the full system is complete. The MVP includes: sales call transcript to structured summary, post-kickoff follow-up email auto-generation, core platform fan-out, and live onboarding status in the dashboard.

### 3.1 Trigger Orchestration Layer (Primary Deliverable)

A central orchestration service that listens for the signing event (PandaDoc webhook or HubSpot event - see Section 3.1.2 on the agreement platform decision) and fans out to every downstream platform in parallel. Today this is a mixture of manual and semi-manual Zapier automations; Phase 1 replaces most of these with direct API integrations for reliability, observability, and retry control.

#### 3.1.1 Zapier: Replace vs Retain

Bullet's current automation runs entirely through Zapier. For Phase 1, the default approach is to build direct API integrations - this gives us idempotency, retry logic, per-action status tracking in the dashboard, and eliminates Zapier as a single point of failure. However, some integrations may be better left in Zapier where the effort of a direct integration outweighs the benefit:

**Build directly (recommended for most)**:
- HubSpot, Stripe, Xero, Google Workspace (Sheets/Docs/Drive/Calendar), Slack, Asana - these are core to the orchestration flow and need retry/status visibility
- Any integration where we need to read data back (bidirectional)

**Evaluate for Zapier delegation**:
- Timely - if their API is limited or poorly documented, a webhook to Zapier may be simpler
- Any one-off notification or simple write-only action where Zapier already works reliably

The decision per integration should be made during Sprint 2 when we have hands on each API. For any Zapier-delegated action, the orchestrator still sends the webhook and tracks success/failure - Zapier becomes a transparent execution layer rather than the orchestration layer.

**Question for Steve (added to Section 11)**: Which Zapier automations exist today, what do they connect, and which ones have been unreliable?

#### 3.1.2 Agreement Platform: PandaDoc vs HubSpot

Bullet currently uses PandaDoc for agreements, integrated with HubSpot via Zapier. During the 08/04/2026 meeting, John mentioned they are considering moving agreements into HubSpot natively. This decision affects the signing-event trigger at the heart of the orchestration layer.

**Current plan**: Build for PandaDoc as the signing trigger (webhook on agreement completion). PandaDoc webhooks are best-effort, so the orchestrator pairs them with polling/reconciliation to ensure no signing events are missed.

**If Bullet confirms the move to HubSpot**: We would build for HubSpot agreement events instead, removing the PandaDoc integration entirely. This is the preferred outcome - it eliminates a platform dependency, simplifies the webhook architecture (HubSpot webhooks are more reliable), and means one fewer API to maintain. Building around two systems when one is being retired adds unnecessary complexity.

**Recommendation**: Confirm the timeline for moving agreements to HubSpot before Sprint 1 begins. If the move will happen during or before the Phase 1 build window, we should build for HubSpot only and skip PandaDoc. If PandaDoc will remain in use beyond Phase 1, we build for PandaDoc now with an abstraction layer that makes the future switch straightforward.

On a successful signing event, the orchestrator performs:

| Action | System | Notes |
|--------|--------|-------|
| Write client record + knowledge profile | Postgres (internal DB) | Primary store for all onboarding data; dashboard and AI read from here |
| Post new-client notification | Slack | To the correct team channel, with links to all created records |
| Create onboarding task list | Asana | Template per service tier, assigned to the correct team members |
| Create client row | Google Sheet (`Client Status Sheet`) | Matches current schema, with live status field |
| Create client folders | Google Drive | Shared structure the client can drop assets into |
| Create client record | Timely | For time tracking |
| Create client record | Xero | Financial profile |
| Store payment method | Stripe | Captured from portal, ready for Step 4 charge |
| Sync onboarding info doc | Google Docs (optional export) | Generated from database; convenience artifact for team members who prefer Docs |
| Send technical requirements email | Gmail (via GoHighLevel) | Conditional branching based on what is required per client |
| Book kick-off call | Google Calendar | Uses existing GHL calendar link, pre-fills attendees |

Each step is **idempotent, retryable, and individually auditable**, so a partial failure never leaves a client half-onboarded.

**Database-first approach**: During the 08/04/2026 meeting, the team agreed that client onboarding data should live in a structured database rather than Google Docs as the primary store. Google Docs remain as an optional sync/export for team members who prefer that format, but the database and dashboard are the source of truth. This enables AI agents to query client data directly, supports the per-client knowledge model (Section 3.9), and positions the system for future phases where agents need programmatic access to client information.

### 3.2 Sales Call Intelligence (Supporting Deliverable)

Bullet already runs sales call transcripts through AI ad-hoc. Phase 1 formalises this:

- Zoom/Google Meet transcript captured automatically
- AI generates a structured sales summary (business type, goals, budget, red flags, next steps)
- Summary stored in the client's knowledge profile in the database, with structured fields extractable by other agents
- Optionally synced to a Google Doc for team members who prefer that format
- Positioned in Sprint 1 to deliver visible value early - the team can see AI-generated sales summaries in the dashboard from day one

### 3.3 Onboarding Status Dashboard (Supporting Deliverable)

A lightweight internal dashboard (web) that surfaces a single row per client with:

- Which step they are on (1 to 6)
- Which automated actions succeeded, failed, or are pending
- Links into every connected platform (HubSpot, GHL, Asana, Drive folder, Stripe customer, Xero contact, Timely client, Slack thread, Meta ad account, Calendar event)
- Time-in-step tracking so bottlenecks are visible
- Per-client knowledge profile view - all accumulated data from sales calls, portal answers, research, and kickoff notes in one place

This becomes the single source of truth for "where is this client?" replacing scattered lookups across Google Docs and platform-specific views.

### 3.4 Client Onboarding Portal (Evaluate / Supporting Deliverable)

Bullet's current onboarding portal is built in GoHighLevel and presents a basic form-style experience for new clients to submit business information after signing. There is an opportunity to replace this with a custom-built portal that:

- Provides a polished, branded onboarding experience with better UX than the current GHL form
- Captures all the same information the GHL portal does today, writing it directly into our database
- Syncs the captured data to GoHighLevel via webhook so GHL automations continue to work as before
- Gives the internal team visibility on portal progress - whether the client has started, how far through they are, and which sections are incomplete
- Feeds the per-client knowledge profile (Section 3.9) immediately on submission rather than requiring a separate data extraction step

**Advantages over keeping the GHL portal**:
- Data lands in our database first, not in GHL's system where it needs to be pulled out
- We control the design and can match Bullet's brand standards
- Progress tracking is native to the dashboard rather than requiring GHL API polling
- Future AI features (e.g. smart field suggestions, progressive disclosure based on business type) are straightforward to add

**Trade-off**: This is additional build effort in Sprint 2. If Bullet is satisfied with the current GHL portal, we can keep it and ingest the data via GHL webhooks/API instead. The data-first benefits still apply either way - the question is whether the improved client experience justifies the build.

**Pending confirmation from Bullet** (see Section 11, question 14): Is refreshing the onboarding portal a priority? If yes, we scope it into Sprint 2 alongside the core fan-out. If no, we keep the GHL portal and pull data via their API.

### 3.5 Kick-Off Follow-Up Email Generator (Supporting Deliverable - MVP)

After the Step 4 kick-off call, AI generates the detailed follow-up email that the digital specialist currently writes manually. This email confirms everything discussed: the agreed offer, campaign structure, ad budget, creative requirements, setup timeline, and any outstanding items. The specialist reviews and edits rather than drafts from scratch.

John specifically identified this as a key MVP feature during the 08/04/2026 meeting. Positioned in Sprint 2 to ship with the MVP milestone.

### 3.6 Kick-Off Stripe Activation (Supporting Deliverable)

After the kick-off call and follow-up email sign-off, the system triggers Stripe's recurring payment activation. Previously bundled with the follow-up email generator; separated here because it depends on client sign-off of the follow-up email and belongs in the financial integrations sprint.

### 3.7 Campaign Guide Assembly via GHL AI Builder (Stretch / Evaluate in Phase 1)

GoHighLevel's recently released AI builder can generate funnels, landing pages, and campaign assets in approximately 30 seconds from structured input. Rather than building a parallel assembly system, Phase 1 scopes this as feeding the structured client data (from the knowledge profile) into GHL's AI builder to trigger campaign generation. The digital specialist reviews and refines the output.

This stretch goal evaluates feasibility in Sprint 2 and, if viable, executes in Sprint 4. The custom build effort is significantly reduced by leveraging GHL's native capability rather than auto-assembling from Canva/GHL data independently.

### 3.8 Client Research Agent (Supporting Deliverable)

Campaign managers currently perform manual research ahead of the kick-off call: scanning the client's website, identifying competitors in the area, checking population/audience size in Meta, and suggesting potential offer angles. Phase 1 automates the structured portion of this research:

- Scrape and summarise the client's website (services, pricing, USPs, current offers)
- Identify competing gyms/fitness studios in the client's area
- Pull Meta audience size data for the client's geographic region
- Generate initial offer angle suggestions based on competitive landscape

The research agent augments rather than replaces the campaign manager's judgement in Phase 1. Output is stored in the client's knowledge profile and surfaced in the dashboard ahead of the kick-off call. Positioned in Sprint 4 alongside the other AI-powered deliverables, sharing the same AI infrastructure.

During the 08/04/2026 meeting, Josh described the vision of three pillars: an onboarding agent, a kickoff agent, and a research agent - all feeding into a single per-client knowledge profile. This deliverable is the research pillar.

### 3.9 Per-Client Knowledge Model (Architectural Foundation)

Each client accumulates a knowledge profile from every touchpoint throughout the onboarding process:

- **Sales call**: Structured summary (business type, goals, budget, pain points, red flags)
- **Agreement**: Service tier, pricing, contract terms, special conditions
- **Portal answers**: Business details, branding, target audience, existing marketing
- **Research**: Website analysis, competitor landscape, audience sizing, offer suggestions
- **Kick-off call**: Agreed offer, campaign structure, budget, creative direction, outstanding items

This knowledge profile is:
- **Queryable by team members** via the dashboard (any team member can ask "what do we know about this client?")
- **Queryable by other agents** programmatically (future phases: client-facing bot, Steve AI, fulfilment agents)
- **Append-only** - new information adds to the profile; nothing is overwritten without an audit trail

Josh's vision from the 08/04/2026 meeting: "one agent per customer that knows everything about that customer - sales calls, agreements, kickoff notes, research - and becomes a queryable resource." This section is the data foundation that makes that vision possible. Future phases layer conversational AI on top of it.

---

## 4. Technology Stack (Proposed)

| Layer | Technology | Why |
|-------|-----------|-----|
| **Orchestration** | Python (FastAPI) + Celery + Redis | Async webhook ingestion, durable job queue for cross-platform fan-out, retries with backoff |
| **Database** | Neon PostgreSQL | Primary source of truth for client state, knowledge profiles, step status, platform IDs, audit log |
| **Dashboard** | Next.js + Tailwind + Polaris-style components | Internal tool, dark mode default |
| **AI (transcripts & summaries)** | Claude (Anthropic) | Sales summaries, kick-off summaries, research synthesis, follow-up email drafting |
| **Transcription** | OpenAI Whisper | Zoom/Google Meet recordings |
| **Web scraping (research agent)** | Firecrawl or equivalent | Client website and competitor analysis |
| **Hosting** | Render.com | Web service + worker + cron |

### Platform Integrations (Read / Write)

| Platform | Direction | Mechanism |
|----------|-----------|-----------|
| HubSpot | Read/Write | Official API + webhooks |
| PandaDoc | Read (webhook) | Signing webhook; may be replaced by HubSpot agreements - see Section 3.1.2 |
| GoHighLevel | Read/Write | API + webhooks for portal completion |
| Asana | Write | API (task list templates) |
| Stripe | Write | API (customers, payment methods, subscriptions) |
| Xero | Write | API (contacts) |
| Timely | Write | API (clients) |
| Slack | Write | Incoming webhooks |
| Google Sheets | Write | Google Sheets API |
| Google Docs | Write (optional sync) | Google Docs API (generated from database, not primary store) |
| Google Drive | Write | Drive API (folder creation + sharing) |
| Gmail | Send | Gmail API or GoHighLevel email automation (to preserve existing conditional logic) |
| Google Calendar | Write | Calendar API |
| Meta Business Manager | Read | Marketing API (ad account confirmation + audience sizing for research agent) |

---

## 5. How It Works - End-to-End Flow

```
  SALES CALL                         SIGNING EVENT                    KICK-OFF CALL
      |                                    |                                |
      v                                    v                                v
  Transcript                  +------------+------------+            Transcript
      |                       |   Orchestration Service |                |
      v                       |   (FastAPI)             |                v
  AI Summary ----+            |  - Verify webhook       |         AI Follow-Up
      |          |            |  - Write client record  |           Email Draft
      v          |            |  - Enqueue fan-out jobs |                |
  +--------+    |            +------------+------------+                v
  |Postgres|<---+                         |                     +--------+
  | (DB)   |<-----------------------------+-------------------->|Postgres|
  +--------+              |               |              |      +--------+
      ^          +--------+-------+-------+-------+------+          ^
      |          v        v       v       v       v      v          |
      |       Slack    Asana   Sheet   Drive   Timely  Xero        |
      |          |        |       |       |       |      |          |
      |          +--------+-------+-------+-------+------+          |
      |                          |                                  |
      |                          v                                  |
      |               Stripe (payment method)                       |
      |                          |                                  |
      |                          v                                  |
      |               Gmail / GHL email                             |
      |                          |                                  |
      |                          v                                  |
      |               Google Calendar (kick-off)                    |
      |                                                             |
      +---------------------+  +------------------------------------+
                             |  |
                             v  v
              +-------------------------------+
              |  Per-Client Knowledge Profile  |
              |  (Postgres - accumulated data) |
              +-------------------------------+
                             |
                             v
              +-------------------------------+
              | Onboarding Status Dashboard   |
              | - Per-client step tracker     |
              | - Knowledge profile view      |
              | - Per-action success/failure  |
              | - Platform deep links         |
              | - Time-in-step tracking       |
              +-------------------------------+
                             |
                             v (optional)
                        Google Docs
                      (sync/export)
```

The database is the central store. Every job writes its outcome (success, failure, retry count, external ID) back to Postgres. The dashboard reads from Postgres. Google Docs are generated as optional exports, not used as the primary record. Nothing is inferred from live platform state at view-time - the database is the record.

---

## 6. Data Model (Core Tables)

| Table | Purpose |
|-------|---------|
| `clients` | One row per client. Includes current step, stage timestamps, canonical IDs in each platform |
| `client_knowledge` | Per-client knowledge profile. Structured facts accumulated from every touchpoint (sales call, agreement, portal, research, kickoff). Each entry tagged with source and timestamp. Queryable by team and by future AI agents |
| `onboarding_events` | Append-only log of every trigger received (sales call booked, signed, portal complete, kick-off done, build complete, gone live) |
| `platform_actions` | One row per fan-out job. Stores target platform, payload, status (pending/success/failed/retrying), external ID, retry count, last error |
| `documents` | Google Docs / Drive / Canva artefacts linked to each client (now optional exports rather than primary store) |
| `research_results` | Output from the research agent: website analysis, competitor data, audience sizing, offer suggestions. Linked to `client_knowledge` |
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
- *What could happen*: PandaDoc or HubSpot resends a webhook, causing duplicate clients; or delivers once and fails, losing the signing event.
- *Mitigation*: Idempotency keys on every action, webhook replay endpoint for manual recovery, daily reconciliation job cross-checks signed agreements against created clients.

**API rate limits across many platforms**
- *What could happen*: A burst of new signings hits Asana/Google/GHL rate limits, causing cascading failures.
- *Mitigation*: Queue-based architecture with per-platform concurrency caps, exponential backoff, and visible dashboard status.

### High Risks

| Risk | Mitigation |
|------|-----------|
| **Credential sprawl** - 12+ platforms each need stored tokens | Centralised secret management, documented rotation schedule, least-privilege scopes per integration |
| **Platform change of shape** - HubSpot / GHL / Asana change APIs | Version-pinned SDKs, contract tests against each provider, alerts on schema drift |
| **Template drift** - Asana templates evolve manually | Templates stored by ID with checksum monitoring; any drift flagged in dashboard |
| **Data currency** - signing before payment info captured | Orchestrator waits for Stripe payment method before triggering financial provisioning |
| **Agreement platform change mid-project** - PandaDoc to HubSpot during the build | Abstraction layer at the signing-webhook boundary means only the adapter changes. Confirm timeline before Sprint 1 to avoid building for a system that is being retired (see Section 3.1.2) |
| **Research agent accuracy** - website scraping or competitor identification returns incorrect data | Research output is presented as suggestions for human review, not used for autonomous decisions in Phase 1 |

### Known Platform Limitations

- **PandaDoc webhooks** are best-effort; must be paired with polling/reconciliation to ensure no signing events are missed. This limitation is one reason to favour the HubSpot move if the timeline allows (see Section 3.1.2).
- **GoHighLevel conditional email logic** is worth preserving in GHL rather than rebuilding in code; the orchestrator triggers the correct GHL workflow rather than sending directly.
- **GHL AI builder** is a recent release (noted by John, 08/04/2026); stability and API access should be validated before building the Campaign Guide Assembly integration.
- **Meta Marketing API** audience sizing queries are subject to rate limits and may return approximate data; sufficient for research agent suggestions but not for precise targeting.

---

## 8. Build Sequence (Indicative)

Detailed TDD task breakdown to follow in `docs/sprint-plan.md` once this architecture is approved.

**MVP milestone at end of Sprint 2**: a shippable checkpoint where the team can see AI-generated sales summaries, the signing trigger creates all non-financial artefacts, post-kickoff follow-up emails are AI-drafted, and the dashboard shows live onboarding status. Testable with real workflows before Sprints 3-4 add financial integrations and the research agent.

### Sprint 1 (Weeks 1-2): Foundation + Sales Call Intelligence

- Project scaffold, Postgres schema (including `client_knowledge` table), auth, dashboard shell
- HubSpot + PandaDoc webhook ingestion for agreement signing events (verified, idempotent, logged; platform depends on Section 3.1.2 decision)
- **Sales Call Intelligence**: transcript capture (Zoom/Google Meet) -> AI-generated structured summary -> stored in client knowledge profile -> visible in dashboard
- Read-only dashboard view: every signed agreement appearing with status "captured", plus AI sales summaries for any clients with transcripts
- **Sprint 1 value**: team can see AI-generated sales summaries in the dashboard immediately, replacing the manual "paste transcript into Claude" workflow

### Sprint 2 (Weeks 3-4): Core Fan-Out + Follow-Up Email + MVP Milestone

- Slack, Asana, Google Sheet, Google Drive, Google Calendar integrations
- Google Docs optional sync (generated from database, not primary store)
- Orchestration engine with retries and the per-action status UI
- **Kick-off follow-up email generator**: AI drafts the post-kickoff confirmation email from call transcript + knowledge profile; specialist reviews and sends
- End-to-end happy path: signed agreement creates all non-financial artefacts automatically
- **MVP MILESTONE**: demo to Bullet team, validate with real (or simulated) onboarding flow

### Sprint 3 (Weeks 5-6): Financial Integrations & Communication

- Stripe (customer + payment method + deferred subscription activation post-kickoff sign-off)
- Xero (contact)
- Timely (client)
- Gmail / GHL conditional technical requirements emails
- Stripe subscription activation triggered after kick-off follow-up email sign-off

### Sprint 4 (Weeks 7-8): Research Agent, Polish & Pilot

- **Client Research Agent**: website scraping, competitor identification, Meta audience sizing, offer suggestions -> stored in knowledge profile -> surfaced in dashboard pre-kickoff
- Onboarding status dashboard polish (time-in-step, bottleneck view, per-platform health)
- Reconciliation job + operational runbooks
- Pilot with 3 to 5 real new clients, measure agreement-to-go-live time
- Campaign Guide Assembly via GHL AI builder scoped as stretch inside Sprint 4 if time permits

---

## 9. Pre-Development Requirements

| Requirement | Owner | Status | Blocker? |
|-------------|-------|--------|----------|
| HubSpot API access + scope list | Bullet Digital Media | Not started | Sprint 1 |
| PandaDoc webhook + API token | Bullet Digital Media | Not started | Sprint 1 (may be replaced by HubSpot - see Section 3.1.2) |
| Claude API key | Bullet Digital Media | John committed | Sprint 1 (moved up for Sales Call Intelligence) |
| OpenAI API key (Whisper) | Bullet Digital Media | Not started | Sprint 1 (moved up for Sales Call Intelligence) |
| Sample sales-call recording | Bullet Digital Media | Not started | Sprint 1 (needed to validate transcript pipeline) |
| GoHighLevel API access + workflow IDs to trigger | Bullet Digital Media | Not started | Sprint 2 |
| Google Workspace service account + domain-wide delegation | Bullet Digital Media | Not started | Sprint 2 |
| Slack incoming webhook per channel | Bullet Digital Media | Not started | Sprint 2 |
| Asana token + onboarding task templates | Bullet Digital Media | Not started | Sprint 2 |
| Stripe restricted API key | Bullet Digital Media | Not started | Sprint 3 |
| Xero OAuth connection | Bullet Digital Media | Not started | Sprint 3 |
| Timely API token | Bullet Digital Media | Not started | Sprint 3 |
| Meta Marketing API access (audience sizing) | Bullet Digital Media | Not started | Sprint 4 |
| Decision: confirm agreement platform timeline (PandaDoc vs HubSpot) | Bullet Digital Media | Under review | **Sprint 1 blocker** - determines which signing trigger to build (see Section 3.1.2) |

---

## 10. Success Criteria

### MVP Milestone (End of Sprint 2)

- [ ] Sales call transcripts are automatically processed into structured summaries visible in the dashboard
- [ ] A signed agreement reliably triggers all non-financial downstream actions (Slack, Asana, Sheets, Drive, Calendar) with no manual intervention
- [ ] Post-kickoff follow-up emails are AI-drafted from call transcripts and the knowledge profile
- [ ] The onboarding status dashboard shows every active client's step, status, and platform links
- [ ] Partial failures are surfaced, retried, and recoverable without data loss

### Phase 1 Complete (End of Sprint 4)

- [ ] All MVP criteria above remain passing
- [ ] Financial integrations live: Stripe payment capture, Xero contact, Timely client creation all automated
- [ ] Stripe subscription activation triggered automatically post-kickoff sign-off
- [ ] Gmail / GHL conditional technical requirements emails fire correctly per client
- [ ] Research agent produces website analysis, competitor data, and offer suggestions before kick-off calls
- [ ] Per-client knowledge profile accumulates data from all sources and is viewable in the dashboard
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
11. What is the current research process in detail? Which sources does the campaign manager check, and in what order? (Needed for the research agent scope in Sprint 4.)
12. Which Zapier automations exist today? What do they connect, and which ones have been unreliable? (Needed to decide which integrations we build directly vs delegate to Zapier - see Section 3.1.1.)
13. What is the timeline for moving agreements from PandaDoc to HubSpot? If the move will happen during or before the Phase 1 build window (next 8 weeks), we should build for HubSpot agreements from the start rather than building around a system that is being retired. If PandaDoc will remain beyond Phase 1, we build for PandaDoc now with an abstraction layer for the future switch. (See Section 3.1.2.)
14. Is refreshing the client onboarding portal a priority? The current GHL portal is functional but basic. We could build a custom-branded portal with a better client experience that writes directly into our system and syncs to GHL via webhook - giving the team progress visibility (has the client started, how far through, which sections incomplete) while keeping GHL automations intact. If you are happy with the current portal, we keep it and pull data via the GHL API instead. (See Section 3.4.)

---

## 12. Next Steps

1. Walk Bullet through this revised plan and confirm scope alignment
2. Book the follow-up call with Steve to resolve Section 11 open questions
3. **Sprint 1 blocker**: Confirm agreement platform timeline - if PandaDoc is being retired during the build window, we build for HubSpot only; otherwise we build for PandaDoc with an abstraction layer (see Section 3.1.2)
4. **Sprint 1 blocker**: Get inventory of current Zapier automations from Steve to decide build-vs-delegate per integration (see Section 3.1.1)
5. Begin credential and template gathering per Section 9 - note that Claude API key and OpenAI key are now Sprint 1 blockers (moved up for Sales Call Intelligence)
6. Obtain a sample sales-call recording for Sprint 1 transcript pipeline validation
7. Confirm pilot clients for Sprint 4

---

*Prepared by IzzyAgents | AI Solutions Consultancy*
