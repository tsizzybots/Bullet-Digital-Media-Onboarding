# Bullet Digital Media - Phase 1 Plan: Onboarding Process Automation

**Prepared by**: IzzyAgents Technical Team
**Date**: 30/04/2026
**Status**: Draft v3.2 - Revised after Stephen's 30/04/2026 reply (kick-off email pricing/discount calculation now scoped to low-ticket / checkout-based campaigns only; high-ticket / consultation-booking variant defined as prose-only confirmation; `campaign_flow_type` captured as a structured client field; Stripe restricted-key one-pager dispatched). v3.1 (28/04/2026) widened the kick-off follow-up workflow with the explicit human review step, two anchor-rate variants, variable body-scan count, and offer-name suggestions.

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

Reproduced from John's 13/04/2026 email, confirmed during the 08/04/2026 meeting walkthrough, and expanded with the platform-level mechanics from Steve's 21/04/2026 Loom walkthroughs (`docs/loom-video-summaries/OB-Phase-1.md` and `docs/loom-video-summaries/OB-Phase-2.md`). Each step has platform touchpoints that are candidates for automation.

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

A central orchestration service that listens for the PandaDoc signing event (Section 3.1.2) and fans out to every downstream platform in parallel. Today this is a 47+ step Zapier chain with a Pabbly bridge for GoHighLevel sub-accounts; Phase 1 replaces it with direct API integrations for reliability, observability, and retry control.

#### 3.1.1 Zapier: Replace vs Retain

Bullet's current automation runs entirely through Zapier, with a Pabbly webhook bolted on to cover the one thing Zapier cannot do (create a GoHighLevel sub-account). Steve's Loom walkthroughs (OB-Phase-1, OB-Phase-2) give us the full inventory. For Phase 1, the default approach is to build direct API integrations - this gives us idempotency, retry logic, per-action status tracking in the dashboard, and eliminates Zapier (and Pabbly) as a single point of failure.

**Current Zap inventory (from Loom walkthroughs)**:

| Zap | Trigger | Actions | Status |
|-----|---------|---------|--------|
| `Document Completed` fan-out | PandaDoc agreement signed | 47+ sequential actions: Pabbly (sub-account), Google Sheet row, Slack notification, Xero contact + draft invoice, ~25 Google Drive folders, GHL contact + custom fields, Asana project + finance task, Timely client | Partly unreliable; folder tree partly legacy |
| `OB Survey Complete` | GHL survey submission | Pabbly (sub-account again - possibly duplicating), Google Doc from template + appended survey answers, Slack, pipeline stage update, sheet update, kick-off email | Pabbly step likely broken |
| `Kickoff Call Booked - Update CSS` | HGL webhook on kickoff booking | Find Asana finance task, update due date to kickoff date | Sync is irregular - Steve manually corrects every Monday |
| `Payment Received - Update CSS` | Stripe new charge | Lookup sheet row by email, update status to "Payment Received" | Working |

**Build directly (recommended)**:
- HubSpot, Stripe, Xero, Google Workspace (Sheets/Docs/Drive/Calendar), Slack, Asana, Timely, GoHighLevel - all core to the orchestration flow and all need retry/status visibility
- Pabbly is retired entirely: we call the GHL API directly for sub-account creation (this also fixes the current duplicate-sub-account behaviour when a returning client signs for a second site)
- Leadsy (used today for one-click Facebook asset access) stays as a link emitted by our system

**Where Zapier might remain**:
- Truly one-off, write-only notifications where building a direct integration adds effort without reliability benefit. No such candidates have emerged yet from the Looms - default position is retire Zapier.

Every fan-out action, whether direct or delegated, writes its outcome (success, failure, retry count, external ID) back to Postgres. The database is the source of truth; the dashboard reads from it.

#### 3.1.2 Agreement Platform: PandaDoc (Confirmed)

**Decision confirmed at the 21/04/2026 meeting**: Bullet stays on PandaDoc. HubSpot does not offer the level of document handling they need, and PandaDoc is already natively integrated with HubSpot. Phase 1 builds directly against the PandaDoc signing webhook with no abstraction layer for a future HubSpot switch.

PandaDoc webhooks are best-effort, so the orchestrator pairs them with polling/reconciliation against the PandaDoc API to ensure no signing events are missed. Client details continue to flow from HubSpot into the PandaDoc template at document creation time; the signing event then becomes our orchestrator trigger.

PandaDoc currently holds two templates (UK and International); the orchestrator treats both as the same trigger and uses the legal entity captured in the document to route Xero and Stripe accordingly.

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
- Step 4 hand-off states: each client at Step 4 surfaces explicitly as `Ready for Performance Director Review` (AI draft + suggested offer names + worked pricing visible for confirmation) then `Ready for Account Manager to Send` (PD has confirmed, AM picks up and sends). The deliberate human pause that exists today between the kick-off call and the follow-up email going out is preserved as a visible workflow step rather than an out-of-band Google Doc thread (see Section 3.5).

This becomes the single source of truth for "where is this client?" replacing scattered lookups across Google Docs and platform-specific views.

### 3.4 Client Onboarding Portal (Decision: GHL Portal Retained for Phase 1)

**Decision confirmed at the 21/04/2026 meeting**: A custom-branded client portal is planned as a Phase 2 engagement deliverable (not Sprint 2 of Phase 1). John immediately supported the custom portal concept for the customer-facing product experience, but framed it as phase two work to avoid bloating Phase 1 scope.

**Phase 1 approach**:
- Keep the current GoHighLevel onboarding portal (OB V2 survey) in place
- Ingest submitted data into our database via the GHL webhook already fired on survey completion
- Surface the submitted data in the dashboard and in the per-client knowledge profile (Section 3.9) immediately on submission
- Show portal progress in the dashboard based on the GHL contact's tag/stage state (started, in progress, sections incomplete)

**Known limitations of the current GHL portal that we accept for Phase 1** (from Steve's OB-Phase-1 walkthrough):
- Redundant questions (name and business name asked in both PandaDoc and the portal; small-group/large-group asked in up to three separate sections due to form layout)
- Brand asset questions (fonts, slogans, colours) that could be derived from the website
- Convoluted offer-pricing flow

These are real UX issues but replacing the portal is not the lever for a single-day onboarding in Phase 1. The orchestration and knowledge-profile work deliver the time saving; Phase 2's custom portal then delivers the customer-facing product experience on top of a stable backend.

**What Phase 1 still delivers on the portal side**:
- Live visibility in the dashboard of which clients have and have not completed the survey
- No manual "did they do it?" lookups in GHL
- Automated chase emails continue to run in GHL (we do not rebuild them)

### 3.5 Kick-Off Follow-Up Email Generator (Supporting Deliverable - MVP)

After the Step 4 kick-off call, AI generates the detailed follow-up email that the digital specialist currently writes manually. This email confirms everything discussed: the agreed offer, campaign structure, ad budget, creative requirements, setup timeline, and any outstanding items.

John specifically identified this as a key MVP feature during the 08/04/2026 meeting. Positioned in Sprint 2 to ship with the MVP milestone.

**Two variants, gated by campaign type (confirmed with Stephen, 30/04/2026)**: the generator branches at the top on a structured `campaign_flow_type` flag set on the kick-off call by the Performance Director. Bullet sells low-ticket and high-ticket campaigns differently, so the email shapes are different:

- `low_ticket_checkout` - large group class facilities on the checkout-page funnel. **Full pricing-maths block applies** (anchor rate + body scans + optional consultation -> total value -> savings -> % off).
- `high_ticket_consultation` - smaller, more expensive clients on the application + consultation-booking funnel. **Prose-only confirmation; no anchor / total-value / savings calculation.** The pricing/discount framing is not how those campaigns are sold.

**The hand-off (confirmed with Stephen, 24/04/2026)**: regardless of variant, there is a deliberate human review point between the kick-off call and the email going out, because not everything is 100% locked in on the call. The system preserves that pause as a productised workflow rather than removing it:

1. Kick-off call ends. AI generates the draft email; for the low-ticket variant it also suggests **2-3 offer-name options** (derived from the client, brand, portal answers, and historically successful offers for similar gyms) and works the pricing maths.
2. The dashboard moves the client into the **`Ready for Performance Director Review`** state (Section 3.3). The PD sees the suggested names and the worked pricing with the calculation visible (low-ticket variant), or the prose draft only (high-ticket variant), plus the full email body.
3. PD confirms the offer name, sanity-checks the maths or the prose, and adjusts anything that was not 100% locked in on the call.
4. The dashboard moves the client into **`Ready for Account Manager to Send`**. AM sends.

The pause stays. The grunt work goes away.

**Low-ticket / checkout variant: pricing calculator (from Steve's OB-Phase-2 walkthrough, refined from Stephen's 24/04/2026 detail)**:

The follow-up email today encodes a deterministic pricing structure that the Performance Director calculates manually on the call. We build this as a structured generator inside the email tool so the email comes out with numbers already worked out, and the dashboard renders the full calculation alongside the result so the PD can sanity-check rather than trust a black box.

The calculator has **two anchor-rate variants** (selected per client based on the gym's pricing model captured in the OB survey):

- **Subscription anchor** (e.g. unlimited classes for £200/month, 21-day offer): `anchor_value = monthly_price / 30.4 × offer_days`. Worked example: `£200 / 30.4 × 21 = £138.16`.
- **Class-pack anchor** (e.g. pilates studio with 5-class pack at £30 drop-in rate): `anchor_value = drop_in_rate × class_count`. Worked example: `£30 × 5 = £150.00`.

On top of the anchor:

- **+ Consultation value** (if a consultation is part of the offer, agreed on the kick-off call) - duration and stand-alone price per the portal answers.
- **+ Body scan value × N**, where N is **1, 2 or 3**, sourced from the OB survey (not fixed at 2).

Then:

- `total_value = anchor_value + consultation_value + (body_scan_price × N)`
- `savings = total_value - offer_price`
- `percent_off = savings / total_value × 100`

Additional outputs:

- **Offer-name suggestions** (2-3 candidates) for the PD to pick from or override.
- **Bring-a-friend** and **money-back-guarantee** framing appended as optional blocks based on portal answers.
- **Calculation transparency**: the dashboard shows the full working alongside the result (for the worked example above: `£200 / 30.4 × 21 = £138.16, +£X consult, +£Y × 2 body scans = £Z total value, £W savings, X% off`) so the PD can confirm the maths matches what was agreed on the call.

The AI generates the prose; the calculator supplies the numbers; the dashboard surfaces both for review. The specialist confirms, then sends.

**High-ticket / consultation variant: prose-only confirmation (added per Stephen's 30/04/2026 reply)**:

For higher-ticket consultation-booking clients, the follow-up email is a prose-only confirmation of what was agreed on the kick-off call - no anchor maths, no total-value table, no savings or % off block. The AI pulls the specifics straight from the call transcript and the knowledge profile and generates the email body.

The variant covers:

- The agreed offer structure (application funnel + consultation booking) and headline price point.
- Ad budget and start date.
- Creative requirements (face-to-camera footage, brand assets, headshots, etc.) and any outstanding items the client owes.
- Setup timeline and the kickoff date for going live.
- The consultation booking link / mechanic the client team will use to book leads.

The hand-off (PD review -> AM send) is identical to the low-ticket variant. The PD reviews and signs off the prose; only the offer-name and pricing-maths concerns drop out.

**Campaign-type capture**:

The variant is selected by a structured `campaign_flow_type ENUM('low_ticket_checkout', 'high_ticket_consultation')` field on the client record, set by the Performance Director on the kick-off call (one click in the dashboard) when the offer structure is finalised. The field is added to the `clients` table in Section 6.

A fallback inference runs against the OB-survey data captured before the call so the dashboard can show a default suggestion ahead of PD confirmation: if `group_size = small` AND `consultation_price > 0` then the default is `high_ticket_consultation`; otherwise the default is `low_ticket_checkout`. The PD always has the final say.

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

At the 21/04/2026 meeting this vision was extended: John described an "AI agent conveyor belt" where individual agents own each step (sales, onboarding, research, post-onboarding) and report to one overall orchestrator agent, with an "agnostic interface" that lets Bullet swap best-in-class underlying tools (Meta, GHL, etc.) without clients seeing the churn. Josh framed this as "the perplexity for gyms and fitness". Phase 1's database, orchestration layer, and knowledge profile are the concrete foundation for that long-term architecture.

### 3.10 Legacy State Replacements (Supporting Deliverable)

Steve's OB-Phase-1 and OB-Phase-2 Looms exposed specific legacy workflows that Phase 1 replaces. Each is a concrete pain point resolved by the database-first orchestration model:

| Legacy workflow | Problem today | Phase 1 replacement |
|-----------------|---------------|---------------------|
| **Pabbly middleman for GHL sub-accounts** | Zapier cannot create GHL sub-accounts; Pabbly bridges it but has duplicate triggers across two Zaps and is "probably broken" in places | Direct GHL API call from the orchestrator; Pabbly retired entirely |
| **Returning-client sub-account duplication** | Same client signing for a second site creates a duplicate GHL sub-account; manual cleanup required | Orchestrator checks for existing GHL contact/sub-account by email before creating |
| **16-branch Outstanding Elements tech follow-up** *(approach confirmed by Stephen, 24/04/2026)* | GHL workflow with a 16-path decision tree (ad account × reg docs × headshot × brand guidelines) sending one of 16 email variants; Sam manually chases from there | Single client-assets table in Postgres with boolean status per required asset; dashboard shows a live checklist per client; one email template with conditional blocks, driven by DB state |
| **Monday manual Asana finance-date sync** | Kickoff date changes break the Zapier Asana sync; Steve manually corrects every Monday | Orchestrator listens for kickoff-calendar changes and writes directly to Asana with idempotency; date mutations propagate automatically |
| **Timely project creation (manual)** | Only the Timely *client* auto-creates; the *project* is manual so Sam can set `time_budget_hours = monthly_fee / 100` and assign team members | Orchestrator auto-creates the project with the calculated budget; team assignment remains manual (or moves to a dashboard action) |
| **Google Doc as de-facto source of truth** | Sales notes pasted manually into a Doc; survey answers auto-appended; pre-call research pasted in red; call notes added in red; team reads this Doc as the "single pane" | Database is the source of truth; dashboard shows the equivalent view (structured, not pasted); Google Doc becomes an optional export for team members who prefer that format |
| **Amex not accepted in PandaDoc payment capture** | Rare cases require extracting Amex details manually outside the PandaDoc flow | Flagged; accepted as-is for Phase 1 unless client asks otherwise |

Each of these has a line of status in the dashboard so the team can see, at a glance, that the legacy pain is gone.

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
| PandaDoc | Read (webhook + API) | Signing webhook with polling/reconciliation against the API; PandaDoc confirmed as agreement platform (Section 3.1.2) |
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
| `clients` | One row per client. Includes current step, stage timestamps, canonical IDs in each platform, and `campaign_flow_type ENUM('low_ticket_checkout', 'high_ticket_consultation')` (Section 3.5) gating the kick-off email variant |
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
| **Research agent accuracy** - website scraping or competitor identification returns incorrect data | Research output is presented as suggestions for human review, not used for autonomous decisions in Phase 1 |
| **Returning-client sub-account duplication** - same email signing for a second site creates duplicate GHL sub-accounts | Orchestrator checks for existing GHL contact/sub-account by email before creating; dashboard surfaces the second-site relationship to the team |
| **Kickoff date mutations breaking finance sync** - client reschedules kick-off and Asana finance date falls out of step (today requires Steve's Monday manual fix) | Orchestrator subscribes to calendar-change events and updates Asana finance task date idempotently; dashboard shows last-known scheduled date and drift from payment date |
| **Amex edge case in PandaDoc** - payment capture fails for Amex, occasionally breaking the signing flow | Accepted as-is for Phase 1; orchestrator flags unpaid agreements distinctly so finance picks up manual capture without the onboarding pipeline stalling |

### Known Platform Limitations

- **PandaDoc webhooks** are best-effort; must be paired with polling/reconciliation against the PandaDoc API to ensure no signing events are missed. Mitigated by a daily reconciliation job and a manual replay endpoint.
- **GoHighLevel conditional email logic** is worth preserving in GHL rather than rebuilding in code; the orchestrator triggers the correct GHL workflow rather than sending directly.
- **GHL AI builder** is a recent release (noted by John, 08/04/2026); stability and API access should be validated before building the Campaign Guide Assembly integration.
- **Meta Marketing API** audience sizing queries are subject to rate limits and may return approximate data; sufficient for research agent suggestions but not for precise targeting.

---

## 8. Build Sequence (Indicative)

Detailed TDD task breakdown to follow in `docs/sprint-plan.md` once this architecture is approved.

**MVP milestone at end of Sprint 2**: a shippable checkpoint where the team can see AI-generated sales summaries, the signing trigger creates all non-financial artefacts, post-kickoff follow-up emails are AI-drafted, and the dashboard shows live onboarding status. Testable with real workflows before Sprints 3-4 add financial integrations and the research agent.

### Sprint 1 (Weeks 1-2): Foundation + Sales Call Intelligence

- Project scaffold, Postgres schema (including `client_knowledge` table), auth, dashboard shell - the dashboard is live from Sprint 1, not a Sprint 4 polish task
- HubSpot + PandaDoc webhook ingestion for agreement signing events (verified, idempotent, logged)
- Direct GoHighLevel API client for sub-account creation (retires Pabbly) with returning-client existence check
- **Sales Call Intelligence**: transcript capture (Zoom/Google Meet) -> AI-generated structured summary -> stored in client knowledge profile -> visible in dashboard
- Read-only dashboard view: every signed agreement appearing with status "captured", plus AI sales summaries for any clients with transcripts; dashboard is the single source of truth for "where is this client?" from day one
- **Sprint 1 value**: team can see AI-generated sales summaries in the dashboard immediately, replacing the manual "paste transcript into Claude" workflow

### Sprint 2 (Weeks 3-4): Core Fan-Out + Follow-Up Email + MVP Milestone

- Slack, Asana, Google Sheet, Google Drive, Google Calendar integrations
- Google Docs optional sync (generated from database, not primary store)
- Orchestration engine with retries and the per-action status UI
- **Kick-off follow-up email generator (both variants)**: AI drafts the post-kickoff confirmation email from call transcript + knowledge profile. Low-ticket / checkout variant ships with the pricing calculator (anchors, body scans, total value, savings, % off); high-ticket / consultation variant ships as prose-only confirmation (no maths). PD selects `campaign_flow_type` in the dashboard before AI generation; specialist reviews and sends
- End-to-end happy path: signed agreement creates all non-financial artefacts automatically
- **MVP MILESTONE**: demo to Bullet team, validate with real (or simulated) onboarding flow

### Sprint 3 (Weeks 5-6): Financial Integrations, Communication & Legacy Replacements

- Stripe (customer + payment method + deferred subscription activation post-kickoff sign-off)
- Xero (contact, with UK vs International routing)
- Timely (client auto-create; project auto-create with calculated `monthly_fee / 100` time budget)
- **Outstanding Elements replacement**: client-assets table + live dashboard checklist per client + single conditional email template (replaces the 16-branch GHL workflow; see Section 3.10)
- Gmail / GHL conditional technical requirements emails (trigger existing GHL workflows where they still make sense; otherwise send from our system)
- Stripe subscription activation triggered after kick-off follow-up email sign-off
- Kickoff-date change propagation: calendar change auto-updates Asana finance task (eliminates Steve's Monday manual sync)
- **Stripe restricted-key brief** dispatched to Stephen 30/04/2026 so the key is provisioned ahead of Sprint 3 (separate doc: `emails/Stripe Restricted Key Setup.pdf`)

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
| HubSpot API access + scope list | Bullet Digital Media | Chris chasing (21/04/2026) | Sprint 1 |
| PandaDoc webhook + API token | Bullet Digital Media | Chris chasing (21/04/2026) | Sprint 1 (PandaDoc confirmed as agreement platform) |
| Claude API key | Bullet Digital Media | John committed | Sprint 1 (for Sales Call Intelligence) |
| OpenAI API key (Whisper) | Bullet Digital Media | Not started | Sprint 1 (for Sales Call Intelligence) |
| Sample sales-call recording | Bullet Digital Media | Not started | Sprint 1 (needed to validate transcript pipeline) |
| GoHighLevel API access + sub-account + workflow IDs to trigger | Bullet Digital Media | Not started | Sprint 1 (Pabbly retirement + portal webhook) |
| Google Workspace service account + domain-wide delegation | Bullet Digital Media | Not started | Sprint 2 |
| Slack incoming webhook for `bullet_inbound_clients` (and any other channels) | Bullet Digital Media | Not started | Sprint 2 |
| Asana token + `Bullet Clients Status` project and finance task template IDs | Bullet Digital Media | Not started | Sprint 2 |
| Stripe restricted API key | Bullet Digital Media | Not started | Sprint 3 |
| Xero OAuth connection + chart-of-accounts + UK/International routing rules | Bullet Digital Media | Not started | Sprint 3 |
| Timely API token + time-budget calculation confirmation (`monthly_fee / 100`) | Bullet Digital Media | Not started | Sprint 3 |
| Leadsy credentials / link-generation access | Bullet Digital Media | Not started | Sprint 3 (tech-follow-up replacement) |
| Meta Marketing API access (audience sizing) | Bullet Digital Media | Not started | Sprint 4 |

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

This section captures **plan-scope** open questions (scope, decisions, deferrals). Blocking **implementation** questions raised during the build are tracked separately in `docs/openquestions.md` (one source of truth, with verbatim wording, owner, blocking impact, and our provisional default for each).

Several prior questions were resolved by Steve's Loom walkthroughs and the 21/04/2026 meeting (agreement platform, portal refresh, Zapier inventory, Sheet schema, Slack channel, Asana task shape, Timely model). The following remain:

### Resolved since the previous draft
- ~~Agreement platform: PandaDoc confirmed to stay (21/04/2026 meeting).~~
- ~~Portal refresh: deferred to engagement Phase 2 (21/04/2026 meeting).~~
- ~~Zapier inventory: captured in OB-Phase-1 and OB-Phase-2 Looms.~~
- ~~16-branch Outstanding Elements replacement: Stephen confirmed on 24/04/2026 ("1 Email with conditional blocks sounds great"). Single client-assets table + live dashboard checklist + one conditional email template, per Section 3.10.~~
- ~~Offer pricing calculator scope: Stephen confirmed on 24/04/2026 that AI computes the full priced offer (two anchor variants, consultation, body scans 1/2/3) AND a deliberate human review point sits between the kick-off call and the email send, surfaced in the dashboard as `Ready for PD Review` -> `Ready for AM to Send`. See Section 3.5.~~
- ~~Pricing/discount calculation applicability: Stephen confirmed on 30/04/2026 that the pricing/discount calculation applies to low-ticket / checkout-based campaigns only (large group class facilities). Higher-ticket / consultation-booking clients (smaller, more expensive) receive a prose-only confirmation email - no anchor maths, no savings/% off block. The generator branches at the top on a structured `campaign_flow_type` field set by the PD on the kick-off call. See Section 3.5.~~

### Still open (carried forward)

1. Which Asana workspace + project template IDs are used today for the onboarding fan-out, and which are for the finance task?
2. The current Google Drive folder tree has ~25 sub-folders (Face-to-Camera variants, Ad Creative, Logo Files, Images, Brand Docs, Font Files, Headshots, Invoices, Campaign Guide). Steve called parts "probably legacy". Mirror exactly, simplify to actively used folders, or generate on demand?
3. Xero - are all clients on the same chart of accounts / tracking categories, or does UK vs International routing change this?
4. Stripe capture timing - card details are collected inside PandaDoc at signing; recurring subscription activates only after kickoff follow-up sign-off. Confirm this is unchanged.
5. Confirm the Zoom to Google Meet migration timeline so the transcript capture is built against the correct provider.
6. Any compliance requirements around storing sales call transcripts (retention, access, consent wording)?
7. What is the current research process in detail? Which sources does the campaign manager check, and in what order? (Needed for the research agent scope in Sprint 4.)

### New (from Loom walkthroughs + 21/04/2026 meeting)

8. **Returning-client handling**: When a client who already has a GHL sub-account signs for a second site, should the orchestrator reuse the existing sub-account, create a new one, or prompt the team? Today this is manual cleanup.
9. **Timely project automation**: Auto-create the Timely project with `time_budget_hours = monthly_fee / 100`, or leave project creation as a dashboard-driven action so Sam can still assign team members?
10. **Kickoff call trajectory**: John's 21/04/2026 framing was a "self-service module" - humans talk at sales, AI manages everything after. Does the kickoff call stay human-led through Phase 1 and into Phase 2, or is AI-led kickoff a Phase 2+ goal we should architect for now?
11. **Pipeline stage parity**: Should the dashboard mirror GHL's pipeline stages (`Lead Gen Live / OB Form Submitted / Kickoff Call Booked / Kick Off Call Complete / Payment Received`), or use our own state model with a mapping layer to GHL?
12. **`SaaS Mode` column** in the Client Status Sheet was visible in the Loom but not explained - what does it represent, and how should it flow through the new system?
13. **Sales handover notes**: Today the salesperson pastes these manually into the Google Doc before kickoff. In the new model, does the salesperson enter these via the dashboard, or do we capture them from the sales-call transcript automatically?
14. **Amex fallback**: Accept the current manual-capture workaround for Phase 1, or scope a fix (e.g. separate Stripe-hosted payment link when Amex is the card)?

### New (from Stephen's 24/04/2026 reply)

15. **30.4-day month divisor for subscription anchor**: Stephen's worked example uses `£200 / 30.4 × 21`. Confirm 30.4 is Bullet's standard (vs 28 or 30) so the calculator matches today's manual workings.
16. **Historical offers corpus for the offer-name suggester**: Where are previously-used offer names stored today (Asana, Drive, PD's head)? Needed to seed the AI suggestions with what has actually worked.
17. **Class-pack offer shapes**: Are class-pack offers always `N classes for £X` (so total value = drop-in × N), or are there variants (e.g. "10 classes / 30 days")?

---

## 12. Next Steps

1. Walk Bullet through this revised plan (v3.2) and confirm scope alignment
2. Close out remaining Section 11 open questions with Steve (new items 9-16 in particular)
3. **Sprint 1 blocker**: Complete API credential and access gathering per Section 9 - Chris is already chasing John (21/04/2026 meeting action)
4. Obtain a sample sales-call recording for Sprint 1 transcript pipeline validation
5. Confirm pilot clients for Sprint 4

---

*Prepared by IzzyAgents | AI Solutions Consultancy*
