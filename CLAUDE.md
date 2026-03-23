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

## Phase 1: Client Onboarding + Internal Knowledge Bank (Months 1-2)

This is the sole active phase. The goal is to build an internal client knowledge bank the team can query for:
- Faster responses to client queries
- Smoother client handovers between team members
- Holiday cover support
- Reducing time spent searching across platforms

Key deliverables:
- AI-powered questionnaire processing and document generation
- Central client knowledge hub per client
- CRM setup guidance
- Client offboarding/retention automation (quick win)

Key constraints:
- **Telegram** confirmed as the client communication channel (one supergroup per client with AI bot + client + account manager)
- Meta Marketing API access needed for ~100 ad accounts; current API tier and system user token status unconfirmed
- Campaign strategy is fluid and multi-layered — no static "strategy docs"; strategy communicated via Zoom calls, email summaries, Loom videos
- Document formats: Google Docs, Google Sheets, Slides, Canva, email, Loom videos
- Client agreed to host documents in-platform rather than external storage
- AI should flag uncertain answers to team members before responding (human-in-the-loop)
- Internal-facing first, then client-facing once validated

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
- **Phase 2**: Client comms AI ("Steve AI") — digital twin for team query support, library of standard responses
- **Phase 3**: Productised AI tools, AI-as-a-service to gym clients, staff training AI
