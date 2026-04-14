# Bullet Digital Media - Phase 1 Sprint Plan: Onboarding Process Automation

**Prepared by**: IzzyAgents Technical Team
**Date**: 14/04/2026
**Status**: Skeleton - detailed TDD task breakdown to follow once `docs/phase-1-plan.md` is approved
**Approach**: Test-Driven Development. Each task (once expanded) will carry explicit test criteria that must pass before the task is considered complete.

> The previous sprint plan (53 TDD tasks for the internal knowledge bank + Telegram bot) has been archived to `docs/archive/sprint-plan-knowledge-bank.md`. Phase 1 has been pivoted to onboarding process automation per John's email of 13/04/2026. See `docs/phase-1-plan.md` for the full architecture.

---

## Overview

Phase 1 is sequenced into **4 sprints across 8 weeks**. The indicative task count will be confirmed during expansion; expect ~40 to 60 trackable tasks.

| Sprint | Weeks | Focus |
|--------|-------|-------|
| Sprint 1 | 1-2 | Foundation, signing-webhook ingestion, dashboard shell |
| Sprint 2 | 3-4 | Core fan-out: Slack, Asana, Google Sheet/Drive/Docs, Calendar |
| Sprint 3 | 5-6 | Financial + comms fan-out: Stripe, Xero, Timely, Gmail/GHL emails; kick-off summary |
| Sprint 4 | 7-8 | Sales call intelligence, dashboard polish, reconciliation, pilot |

### Pre-Development Blockers

These must be resolved before the sprint that requires them. Owners and statuses are tracked in `docs/phase-1-plan.md` Section 9.

| Requirement | Needed By |
|-------------|-----------|
| HubSpot API access + scope list | Sprint 1 |
| PandaDoc webhook + API token | Sprint 1 |
| Decision: agreements stay in PandaDoc vs move to HubSpot | Sprint 1 |
| GoHighLevel API access + workflow IDs to trigger | Sprint 2 |
| Google Workspace service account + domain-wide delegation | Sprint 2 |
| Slack incoming webhooks per channel | Sprint 2 |
| Asana token + onboarding task templates | Sprint 2 |
| Stripe restricted API key | Sprint 3 |
| Xero OAuth connection | Sprint 3 |
| Timely API token | Sprint 3 |
| Claude API key | Sprint 4 |
| OpenAI API key (Whisper) | Sprint 4 |
| Sample sales-call recording | Sprint 4 (helpful) |

---

## Sprint 1 (Weeks 1-2): Foundation & Signing Capture

**Goal**: Stand up the orchestration service skeleton and reliably capture every signed agreement into our database, visible in a read-only dashboard.

**Headline outcomes**:
- Monorepo scaffold (FastAPI backend, Next.js dashboard, Postgres on Neon, Redis, Celery worker on Render)
- Auth + role-based access for the internal dashboard
- Signing-webhook ingestion (PandaDoc, HubSpot) behind an abstraction so the agreement-source decision is not blocking
- Idempotent persistence of signing events into Postgres with audit log
- Dashboard read-only view: every signed agreement appearing as a client row with status "captured"
- Reconciliation command: query PandaDoc / HubSpot for signed agreements in the last 24 hours and backfill any missed webhooks

## Sprint 2 (Weeks 3-4): Core Fan-Out

**Goal**: A signing event automatically creates every non-financial artefact, with per-action status visible in the dashboard.

**Headline outcomes**:
- Orchestration engine: durable queue, per-platform concurrency caps, retries with exponential backoff, idempotency keys on every action
- Slack integration (team notification per new client with deep links)
- Asana integration (task list from template, assignments)
- Google Drive integration (client folder structure, sharing)
- Google Sheets integration (row append into `Client Status Sheet`)
- Google Docs integration (template merge with sales-call inputs placeholder)
- Google Calendar integration (kick-off booking)
- Dashboard: per-action status (pending / success / failed / retrying), deep links into each created artefact
- End-to-end happy path test: sign a PandaDoc sample, see every artefact created within minutes

## Sprint 3 (Weeks 5-6): Financial, Comms & Kick-Off

**Goal**: Close the financial and communication loop, and automate the kick-off summary.

**Headline outcomes**:
- Stripe integration (customer, payment method capture, deferred subscription activation)
- Xero integration (contact + tracking categories)
- Timely integration (client record)
- Gmail / GoHighLevel email triggering with the existing conditional logic preserved in GHL
- Kick-off Summary Generator: transcript ingest -> AI summary -> Google Doc update -> Stripe subscription activation on confirmation
- Dashboard: financial health panel per client (Stripe status, Xero status, billing start date)

## Sprint 4 (Weeks 7-8): Sales Intelligence, Polish & Pilot

**Goal**: Close out the sales-call-to-Google-Doc loop, harden the system, and run a real pilot.

**Headline outcomes**:
- Sales Call Intelligence: Zoom / Google Meet recording intake -> Whisper transcript -> Claude structured summary -> Google Doc section populated
- Dashboard: time-in-step, bottleneck view, per-platform health, operator-facing retry / replay controls
- Reconciliation cron (hourly): cross-check agreements against created clients and flag drift
- Operational runbooks: webhook replay, partial-failure recovery, credential rotation
- Pilot with 3 to 5 real new clients; measured agreement-to-go-live time vs the 2-week baseline
- Stretch: Campaign Guide Assembly prototype (Section 3.5 of the plan)

---

## Next Step

Once `docs/phase-1-plan.md` is reviewed and approved, expand each sprint above into individual TDD tasks with explicit deliverables and test criteria. Target granularity: every task completable in under a day, each with a pass/fail test gate.
