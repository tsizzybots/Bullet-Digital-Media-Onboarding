# Bullet Digital Media - AI Department

AI solutions engagement between **IzzyAgents** and **Bullet Digital Media**, a performance marketing agency specialising in Meta ads for gyms and fitness studios (~100 active clients, ~8-12 team members).

> **Current Focus: Phase 1** - All other phases are on hold until Phase 1 is fully delivered and validated.

---

## The Problem

Bullet Digital Media faces capacity constraints that limit growth:

- **Staff capacity ceiling** - each team member maxes out at 22-23 clients
- **18-month ramp time** for new hires to reach full capacity
- **Manual onboarding** spanning ~3 weeks across ~12 platforms (HubSpot, PandaDoc, GoHighLevel, Asana, Stripe, Xero, Timely, Slack, Google Workspace, Meta, Canva, Loom) with handoffs that leak time and information
- **Repetitive client comms** consuming time that should go to strategy and campaign management
- **Scattered client knowledge** across Google Docs, Sheets, email, Loom, and Canva

## Phased Development Plan

### Phase 1: Onboarding Process Automation (Months 1-2) `ACTIVE`

The sole active phase. Automating Bullet's end-to-end client onboarding - from sales call through campaign go-live - to compress agreement-to-go-live from ~2 weeks toward a single day. Full plan: [`docs/phase-1-plan.md`](docs/phase-1-plan.md). Source brief: [`emails/Bullet Onboarding Process.pdf`](emails/Bullet%20Onboarding%20Process.pdf).

**Deliverables:**
- Trigger orchestration layer - a signed agreement fans out to Slack, Asana, Google Sheets/Drive/Docs/Calendar, Stripe, Xero, Timely, Gmail/GHL reliably and idempotently
- Onboarding status dashboard - single view of every client's step, platform links, and action health
- Sales call intelligence - transcript to structured summary written straight into the onboarding Google Doc
- Kick-off summary generator - AI-drafted post-call summary + Stripe subscription activation

**Key constraints:**
- Integrates HubSpot, PandaDoc, GoHighLevel, Asana, Stripe, Xero, Timely, Slack, Google Workspace, Meta Business Manager, Canva, Loom
- Agreement location (PandaDoc vs HubSpot) under client review
- GoHighLevel conditional email workflows are triggered, not replaced
- Every fan-out action idempotent, retryable, and individually auditable
- Internal-facing tool; single-tenant for Bullet's team

### Phase 2: Internal Knowledge Bank + Telegram Client Bot `NOT STARTED`

Previously planned as Phase 1, now deferred. Internal knowledge bank the team can query for faster client responses, smoother handovers, and holiday cover, followed by client-facing Telegram bot per client group. Archived plan: [`docs/archive/phase-1-plan-knowledge-bank.md`](docs/archive/phase-1-plan-knowledge-bank.md).

### Phase 3: Client Comms AI - "Steve AI" `NOT STARTED`

A digital twin trained on company philosophy, past responses, and decision patterns. Team queries the AI before escalating. Handles simple, repeatable email responses immediately; advises on complex cases by citing prior decisions like legal precedent.

**Sub-phases:**
1. **MVP** - AI handles simple, repeatable email responses
2. **Library Build** - Catalogue complex/unique requests; AI references prior cases
3. **Cultural Shift** - Define and embed "black and white" standards into the AI

**Data requirements:** 5+ years of email history (export and categorise), call transcriptions, response templates, documented decision-making philosophy from Steve and John.

### Phase 4: Productised AI Tools & AI as a Service `NOT STARTED`

The strategic scale play, building on Phases 1-3:

- **Internal AI Product** - Account managers use AI to manage 30-50 clients each (up from 22-23)
- **External AI Product** - Productised gym marketing AI for 100,000+ gyms globally, white-label for other agencies
- **AI as a Service** - Targeted AI automations sold to gym clients (voice AI for payment chasing, chatbots for member enquiries) at a margin through existing relationships

## Project Structure

```
scope/                        # Project scope documents and development priorities
meeting_notes/                # Onboarding and meeting transcripts/summaries
questionnaire_responses/      # Client discovery questionnaire data (CSV)
docs/                         # Additional documentation
```

## Key Stakeholders

| Name | Role |
|------|------|
| John Limber | Founder, Bullet Digital Media |
| Stephen Taylor | Founder, Bullet Digital Media |
| Max | Performance Director |
| Luchiano | Performance Director |

## Key Metrics

| Metric | Current |
|--------|---------|
| Max clients per team member | 22-23 |
| Time to full staff capacity | 18 months |
| Client churn rate | ~10% |
| New clients needed annually | ~100 |
| Historical data available | 5-6 years |

---

Prepared by **IzzyAgents** | AI Solutions Consultancy
