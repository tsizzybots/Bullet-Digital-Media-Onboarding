# Changelog

All notable development updates, decisions, and discoveries for the IzzyAgents x Bullet Digital Media engagement.

Format: newest entries on top. Each entry uses UK dates (DD/MM/YYYY) and 24-hour times. Currency in USD.

Entry types:
- **Added** - new code, features, files, or capabilities
- **Changed** - modifications to existing behaviour, scope, or structure
- **Removed** - deletions, deprecations, or retired components
- **Decision** - confirmed scope, architecture, or product decisions
- **Discovery** - new information learned about Bullet's processes, platforms, or constraints
- **Fixed** - bug fixes or corrections

## [Unreleased]

### 04/05/2026 - Infrastructure setup doc + changelog discipline tightened

- **Added**: `docs/infrastructure.md` - client-facing infrastructure setup guide. Lists every third-party service Bullet must register for (Section A: 9 new services - Neon, Render, Inngest, Cloudflare R2, Sentry, Resend, Anthropic, OpenAI, Firecrawl), every existing service that needs IzzyAgents access added (Section B: 12 existing services), estimated monthly infrastructure cost ($220-$540 USD/month at pilot scale), an action checklist organised by sprint week, and credential-handling rules. Every service specifies how to share access with `team@izzyagents.ai`.
- **Changed**: `CLAUDE.md` "Changelog Discipline" section - added explicit "Never ask for permission to update the changelog" rule. Logging is now unconditional and automatic; do not ask "want me to log this?" - just do it. Only exception is when the user explicitly tells me not to log a specific item.
- **Decision**: Phase 1 hosting topology will live entirely on Bullet's own infrastructure (their accounts, their billing, their data). No IzzyAgents-hosted shim layer. All credentials shared with `team@izzyagents.ai` at the role specified per service in `docs/infrastructure.md` Section A/B. Reasoning: keeps Bullet in control of the data and billing, simplifies the eventual handover at end of Phase 1.

### 04/05/2026 - Development sprint plan landed

- **Added**: `docs/development-sprints.md` - canonical, ordered task list for Phase 1 across all four sprints. Tasks numbered `S{sprint}-{nn}` (S1-01 to S4-16, 92 tasks total). Each task carries a description, TDD-shaped test contract, and explicit upstream task dependencies (or `n/a`).
- **Decision (provisional)**: Q-01 treated as resolved to **Resend with a single system mailbox** for sprint-planning purposes. Affected tasks (S2-22 through S2-25, S3-10, S3-11) flagged in `docs/development-sprints.md` so they can be revised cleanly if Bullet later mandates per-AM Gmail; no other tasks shift.
- **Decision**: Sprint 4 finish line is `S4-12` (production cutover) -> `S4-13` (3-5 real pilot clients onboarded end-to-end) -> `S4-14` (measure agreement-to-go-live in `docs/pilot-results.md`). This is the "live and client-testable" definition of done for Phase 1.

### 03/05/2026 - PRD landed; tech stack locked

- **Added**: `docs/PRD.md` - operationalises Phase 1 plan v3.2 into concrete product requirements (data model, integration surfaces, AI prompt schemas, dashboard IA, observability, deployment topology, sprint-mapped acceptance criteria).
- **Added**: `docs/openquestions.md` - canonical log for blocking implementation questions Bullet must answer. Sister doc to phase-1-plan.md Section 11. Seeded with **Q-01** (outbound email provider).
- **Decision**: Backend language - Python (FastAPI). Best SDK ecosystem for Stripe/Xero/GHL/Asana/HubSpot/Anthropic/Whisper.
- **Decision**: Job queue / orchestration - Inngest. Durable execution, automatic retries, idempotency keys, step-level observability, built-in UI.
- **Decision**: Database - Neon Postgres with pgvector for semantic search on `client_knowledge`.
- **Decision**: ORM + migrations - SQLAlchemy 2.x async + Alembic.
- **Decision**: Frontend - Next.js App Router + TypeScript strict + Tailwind + shadcn/ui, dark mode default. shadcn replaces the plan's "Polaris-style" placeholder (Polaris is Shopify-only).
- **Decision**: Auth - username/password + Resend confirmation email + 7-day session cookie. argon2id password hashing.
- **Decision**: AI/LLM SDK split - Anthropic Python SDK direct (with prompt caching) for one-shot prompts (sales summary, kick-off email); Claude Agent SDK for the Sprint 4 research agent's multi-step tool-using loop.
- **Decision**: Transcription - native Zoom / Google Meet transcripts first, OpenAI Whisper API as fallback.
- **Decision**: Object storage - Cloudflare R2 for transcript audio, scraped HTML, system-generated docs. Google Drive remains the client-asset store.
- **Decision**: Observability - Sentry + Inngest UI + Postgres `platform_actions` audit table.
- **Decision**: Hosting - Render.com (web service + worker + cron + dashboard); Neon for Postgres.
- **Decision**: Repo structure - monorepo: `apps/api` (Python via uv) + `apps/dashboard` (TS via pnpm workspaces) + `packages/shared`.
- **Decision**: API contract - FastAPI auto-generates OpenAPI spec; codegen TS client into `packages/shared`. Build breaks if dashboard goes out of sync with API.
- **Decision**: Real-time updates - TanStack Query polling every 5-10s on active dashboard views. Skip WebSocket/SSE complexity.
- **Decision**: Testing - pytest (backend) + Playwright (E2E) + Vitest (dashboard unit/component). TDD discipline.
- **Decision**: Staging environment - yes from day one. Separate Render services + separate Neon DB.
- **Decision**: Secrets - Render env groups only. Defer 1Password / Doppler integration until rotation pain emerges.
- **Decision**: Web scraping (Sprint 4) - Firecrawl for client-website + competitor-page deep scrapes; Claude `web_search` (built-in Agent SDK tool) for competitor discovery.
- **Decision**: Slack - incoming webhooks only (one-way notifications). Bullet has not requested interactive features; all human-confirmation flows happen in the dashboard.
- **Decision**: Local development - Docker Compose for Postgres + Inngest dev server.
- **Decision (provisional)**: Outbound email - Resend for all system-sent email (kick-off follow-up, tech-requirements, auth confirmation, dashboard alerts). Existing GHL post-signing / survey-reminder workflows stay in GHL. **Pending Q-01** to Bullet: confirm a single system mailbox is acceptable, or whether per-AM Gmail mailboxes are required.
- **Discovery**: User reasoning on email provider (logistics over current-state) - per-AM Gmail delegation requires a setup ritual every time a new Account Manager joins Bullet. A single Resend mailbox scales with team growth. Final answer pending Bullet's response to Q-01.

### 03/05/2026 - Transition from planning to development

- **Changed**: Project moved from planning/discovery phase into active development phase. Phase 1 plan (v3.2) is locked as the build spec.
- **Added**: `docs/CHANGELOG.md` introduced as the canonical log for development updates and ongoing discoveries.

---

## Planning phase summary (pre-development)

The following entries summarise key milestones from the planning phase. Future entries should be appended above this section under dated headings.

### 30/04/2026
- **Changed**: Phase 1 plan refined to v3.2 after Stephen's reply (commit `d01a660`).

### 24/04/2026
- **Changed**: Phase 1 plan refined to v3.1 after Stephen's reply (commit `c3de9f2`).

### 22/04/2026
- **Changed**: Phase 1 plan revised to v3 (commit `85182c9`); client-facing version added.

### 21/04/2026
- **Decision**: Agreement platform - PandaDoc stays (already natively integrated with HubSpot; no abstraction layer).
- **Decision**: Client onboarding portal - GHL portal retained for Phase 1; custom-branded portal deferred to Phase 2.
- **Decision**: Pabbly middleman retired - direct GoHighLevel API used for sub-account creation, with returning-client existence check.
- **Decision**: Loom videos adopted as documentation standard for team processes (feeds future AI agents).
- **Decision**: Database + dashboard confirmed as the central source of truth from Sprint 1 (not a Sprint 4 polish item). Google Sheets, Google Docs, and GHL custom fields become optional mirrors only.
- **Discovery**: Long-term vision confirmed - "AI agent conveyor belt" with one orchestrator agent and an agnostic IzzyAgents front-end ("Perplexity for gyms and fitness").
- **Added**: Onboarding meeting notes (commit `8cf17cf`).

### Pre-21/04/2026
- **Added**: OB-Phase-1 and OB-Phase-2 Loom walkthrough summaries from Steve (commit `a5353b2`) - authoritative source for current Zapier chains, GHL workflows, and manual workarounds.
- **Changed**: Original Phase 1 scope (internal knowledge bank + client-facing Telegram bot) deferred to a later phase; archived under `docs/archive/`.

---

## How to use this changelog

- Append new entries at the top of `[Unreleased]` under a `### DD/MM/YYYY` heading.
- Use the entry types above to tag each bullet.
- Capture decisions and discoveries in the moment - don't rely on memory or git history alone.
- When code lands, link to the relevant commit hash where helpful.
- When a release is cut, rename `[Unreleased]` to a version + date heading and start a fresh `[Unreleased]` block above it.
