# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **project planning and documentation workspace** for the IzzyAgents & Bullet Digital Media AI engagement. Bullet Digital Media is a gym/fitness marketing agency (~91-100 active clients, ~8-12 team members) that IzzyAgents is building AI solutions for.

**Current status**: Documentation and discovery phase. No application code exists yet — this workspace contains project scope, meeting notes, and questionnaire responses that provide context for future development. Code will be added as the application is built out.

**Current focus**: Phase 1 only. Future phases will not begin until Phase 1 is fully complete.

## Directory Structure

- `scope/` — Project scope documents and development priorities
- `meeting_notes/` — Onboarding and meeting transcripts/summaries
- `questionnaire_responses/` — Client discovery questionnaire data (CSV)
- `docs/` — Additional documentation

## Client Context

- **Bullet Digital Media**: Performance marketing agency specializing in Meta ads for gyms/fitness studios
- **Key stakeholders**: John Limber (Founder), Stephen Taylor (Founder), Max & Luchiano (Performance Directors)
- **Current capacity**: 22-23 clients per team member, 18-month ramp to full capacity for new hires
- **Primary communication**: Trello (backlog/ideas), email (formal), WhatsApp (quick check-ins)

## Phase 1: Onboarding Process Automation (Months 1-2)

This is the sole active phase. The goal is to automate Bullet's end-to-end client onboarding process (sales call through campaign go-live) to compress agreement-to-go-live from ~2 weeks toward a single day. Source brief: `emails/Bullet Onboarding Process.pdf` (John Limber, 13/04/2026).

Scope is defined in `docs/phase-1-plan.md`. Previous Phase 1 scope (internal knowledge bank + client-facing Telegram bot) is deferred to a later phase; archived under `docs/archive/`.

Key deliverables:
- **Trigger orchestration layer** - a signed agreement fans out to Slack, Asana, Google Sheets/Drive/Docs/Calendar, Stripe, Xero, Timely, Gmail/GHL reliably and idempotently
- **Onboarding status dashboard** - single view of every client's step, platform links, and action health
- **Sales call intelligence** - transcript to structured summary written straight into the onboarding Google Doc
- **Kick-off summary generator** - AI-drafted post-call summary + Stripe subscription activation

Key constraints:
- Platforms involved: HubSpot, PandaDoc, GoHighLevel, Asana, Stripe, Xero, Timely, Slack, Google Workspace (Sheets/Docs/Drive/Calendar/Gmail), Meta Business Manager, Canva, Loom
- Agreement location (PandaDoc vs HubSpot) is under client review; orchestrator abstracts the signing-webhook source
- GoHighLevel conditional email workflows are triggered, not replaced
- Zoom to Google Meet migration in progress; transcript capture must work against whichever is live
- Internal-facing tool; single-tenant (Bullet team only)
- Every fan-out action is idempotent, retryable, and individually auditable - partial failures must be visible, never silent

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
