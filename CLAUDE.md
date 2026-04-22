# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **project planning and documentation workspace** for the IzzyAgents & Bullet Digital Media AI engagement. Bullet Digital Media is a gym/fitness marketing agency (~91-100 active clients, ~8-12 team members) that IzzyAgents is building AI solutions for.

**Current status**: Documentation and discovery phase. No application code exists yet — this workspace contains project scope, meeting notes, and questionnaire responses that provide context for future development. Code will be added as the application is built out.

**Current focus**: Phase 1 only. Future phases will not begin until Phase 1 is fully complete.

## Directory Structure

- `scope/` - Project scope documents and development priorities
- `meeting_notes/` - Onboarding and meeting transcripts/summaries (subdirs per topic, e.g. `meeting_notes/onboarding/`)
- `questionnaire_responses/` - Client discovery questionnaire data (CSV)
- `emails/` - Source email briefs from Bullet (e.g. `Bullet Onboarding Process.pdf`)
- `docs/` - Plan documents, Loom summaries, and other project documentation
  - `docs/phase-1-plan.md` and `docs/phase-1-plan-client.md` - current Phase 1 plans (internal + client-facing)
  - `docs/loom-video-summaries/` - Steve's walkthroughs of the current onboarding mechanics (OB-Phase-1, OB-Phase-2)
  - `docs/archive/` - Deferred earlier-scope plans (knowledge bank, Telegram bot)

## Client Context

- **Bullet Digital Media**: Performance marketing agency specializing in Meta ads for gyms/fitness studios
- **Key stakeholders**: John Limber (Founder), Stephen Taylor (Founder), Max & Luchiano (Performance Directors)
- **Current capacity**: 22-23 clients per team member, 18-month ramp to full capacity for new hires
- **Primary communication**: Trello (backlog/ideas), email (formal), WhatsApp (quick check-ins)

## Phase 1: Onboarding Process Automation (Months 1-2)

This is the sole active phase. The goal is to automate Bullet's end-to-end client onboarding process (sales call through campaign go-live) to compress agreement-to-go-live from ~2 weeks toward a single day.

Primary sources of truth for the plan:
- `docs/phase-1-plan.md` (internal plan, current version v3 - 22/04/2026)
- `docs/phase-1-plan-client.md` (client-facing version)
- `emails/Bullet Onboarding Process.pdf` (John Limber, 13/04/2026 - original brief)
- `meeting_notes/onboarding/` (most recent: IzzyAgents & Bullet Digital Media 21/04/2026)
- `docs/loom-video-summaries/` (Steve's OB-Phase-1 and OB-Phase-2 walkthroughs of the current onboarding mechanics - authoritative source for today's Zapier chains, GHL workflows, and manual workarounds)

Previous Phase 1 scope (internal knowledge bank + client-facing Telegram bot) is deferred to a later phase; archived under `docs/archive/`.

Key deliverables:
- **Database + dashboard as the central source of truth** - Postgres holds all client data; dashboard is live from Sprint 1 (not a Sprint 4 polish item). Google Sheets, Google Docs, and GoHighLevel custom fields become optional mirrors, never the primary store
- **Trigger orchestration layer** - a PandaDoc signing event fans out to Slack, Asana, Google Sheets/Drive/Calendar, Stripe, Xero, Timely, Gmail/GHL reliably and idempotently; retires the current Zapier + Pabbly chain
- **Onboarding status dashboard** - single view of every client's step, platform links, knowledge profile, and per-action health; live asset checklist per client replacing the 16-branch GHL Outstanding Elements workflow
- **Sales call intelligence** - transcript to structured summary stored in the client's knowledge profile in the database and surfaced in the dashboard
- **Kick-off follow-up email generator** - AI-drafted post-call email with deterministic offer pricing (75% membership anchor + consultation + body scan + bring-a-friend/MBG framing); Stripe subscription activation after sign-off
- **Client research agent** (Sprint 4) - website scraping, competitor identification, Meta audience sizing, offer suggestions stored in the knowledge profile

Key decisions (confirmed 21/04/2026):
- **Agreement platform: PandaDoc stays** (HubSpot does not offer the document handling Bullet needs; PandaDoc is already natively integrated with HubSpot). No abstraction layer.
- **Client onboarding portal: GHL portal retained for Phase 1**. Custom-branded portal is a Phase 2 engagement deliverable.
- **Pabbly middleman: retired**. Direct GoHighLevel API for sub-account creation, with returning-client existence check.
- **Loom videos as documentation standard**: Steve continues to record Loom walkthroughs of team processes to feed future AI agents.

Long-term vision (from 21/04/2026 call):
- **AI agent conveyor belt**: individual agents per step (sales, onboarding, research, post-onboarding) reporting to one orchestrator agent
- **Agnostic interface**: clients interact only with the IzzyAgents front-end; underlying tools (Meta, GHL, Canva, etc.) can be swapped without clients feeling the churn ("Perplexity for gyms and fitness")
- Phase 1's database, orchestration layer, and knowledge profile are the concrete foundation for this vision

Key constraints:
- Platforms involved: HubSpot, PandaDoc, GoHighLevel, Asana, Stripe, Xero, Timely, Slack, Google Workspace (Sheets/Docs/Drive/Calendar/Gmail), Meta Business Manager, Canva, Loom, Leadsy (for one-click Facebook asset access)
- GoHighLevel conditional email workflows are triggered where they still make sense; the 16-branch Outstanding Elements tech follow-up workflow is replaced with a DB-driven dashboard checklist and single conditional email template
- Zoom to Google Meet migration in progress; transcript capture must work against whichever is live
- Internal-facing tool; single-tenant (Bullet team only)
- Every fan-out action is idempotent, retryable, and individually auditable - partial failures must be visible in the dashboard, never silent
- Every job writes its outcome (success/failure, external ID, retry count) back to Postgres - nothing is inferred from live platform state at view-time

## Document Formatting Rules

- Never use em dashes (-). Always use standard hyphens (-).
- All dates must use UK format: DD/MM/YYYY (e.g., 23/03/2026)
- All times must use 24-hour format (e.g., 14:30, not 2:30 PM)
- Currency in USD ($) unless otherwise specified

## GitHub
- Account: `tsizzybots` - always use this account for this project
- Repo: `tsizzybots/bullet_digital_media`
- Before pushing, ensure active account: `gh auth switch --user tsizzybots`

## StrikeFlow Integration
- Board Name: "Bullet Digital Media"
- Board ID: c01081f2-c27c-4a8c-b7c5-0b2857254cd9

## Future Phases (Not Yet Active)

These phases are scoped but will only begin after Phase 1 is fully complete:
- **Phase 2 (originally planned Phase 1)**: Internal client knowledge bank + client-facing Telegram AI bot. Archived plan in `docs/archive/phase-1-plan-knowledge-bank.md` and `docs/archive/sprint-plan-knowledge-bank.md`
- **Phase 3**: Client comms AI ("Steve AI") - digital twin for team query support, library of standard responses
- **Phase 4**: Productised AI tools, AI-as-a-service to gym clients, staff training AI
