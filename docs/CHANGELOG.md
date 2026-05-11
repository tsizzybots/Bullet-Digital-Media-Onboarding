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

### 11/05/2026 - S1-05: SQLAlchemy + Alembic setup

- **Added**: `apps/api` async database layer. `bullet_api.config.Settings` (pydantic-settings) loads `DATABASE_URL` from env; `bullet_api.config.get_async_database_url()` rewrites the canonical `postgresql://` scheme to `postgresql+asyncpg://` so the same env var drives `psql`, `scripts/verify_pgvector.py`, the SQLAlchemy async engine, and Alembic. `bullet_api.db` exposes `Base` (DeclarativeBase, ready for S1-06+ models), `engine` (`AsyncEngine` with `pool_pre_ping=True`), `AsyncSessionLocal` (async sessionmaker, `expire_on_commit=False`), and `get_session` (FastAPI dependency that rolls back on exception).
- **Added**: dependencies in `apps/api/pyproject.toml`: `sqlalchemy[asyncio]>=2.0.36,<3.0`, `asyncpg>=0.30,<1.0`, `alembic>=1.14,<2.0`, `pydantic-settings>=2.6,<3.0`, `greenlet>=3.1,<4.0` (pinned explicitly so the SQLAlchemy async dependency is visible).
- **Added**: Alembic scaffold under `apps/api/` - `alembic.ini` (script_location=`alembic`, `prepend_sys_path=src`, blank `sqlalchemy.url` so env.py owns it, UTC timestamps + numeric prefix in `file_template`), `alembic/env.py` (async migrations via `AsyncEngine` + `connection.run_sync(do_run_migrations)`, `target_metadata=Base.metadata` so future autogenerate works), `alembic/script.py.mako` (matches repo Python style, `from __future__ import annotations` baked in).
- **Added**: first migration `apps/api/alembic/versions/0001_create_extensions.py` - `CREATE EXTENSION IF NOT EXISTS vector` then `citext` on upgrade; reverse order on downgrade. No tables (those start at S1-06). `IF NOT EXISTS`/`IF EXISTS` keeps the migration safe against Neon branches that may have the extension pre-installed by the control plane.
- **Added**: pytest `db` marker plus `apps/api/tests/conftest.py` and `apps/api/tests/test_db_smoke.py`. Conftest probes the engine once at collection time via a throwaway `NullPool` engine; tests marked `@pytest.mark.db` skip cleanly when `DATABASE_URL` is unreachable. The `async_session` fixture also uses a `NullPool` engine so the per-function event loops that `pytest-asyncio` creates in auto mode cannot poison a pooled connection. Two tests added: `SELECT 1` against the async session, and a query asserting both extensions are present in `pg_extension`.
- **Added**: Makefile targets `db-upgrade`, `db-downgrade`, `db-revision m="..."`, `db-reset` (all delegate to `cd apps/api && uv run alembic ...`).
- **Changed**: `.github/workflows/ci.yml` - the `python` job now spins up a `pgvector/pgvector:pg16` service container (user/password/db match `.env.example`, `pg_isready` healthcheck), exports `DATABASE_URL=postgresql://bullet:bullet@localhost:5432/bullet_dev`, and runs `alembic upgrade head` -> `alembic downgrade base` -> `alembic upgrade head` -> `pytest` so every PR gates on real migration + reversibility + DB smoke tests.
- **Changed**: `.env.example` comment for `DATABASE_URL` rewritten to explain that the canonical `postgresql://` scheme is intentional and is rewritten internally to `+asyncpg` by SQLAlchemy and Alembic.
- **Changed**: `README.md` - added a "Database migrations" subsection under "Local development" documenting `make db-upgrade`/`db-downgrade`/`db-reset`/`db-revision` and the URL-rewriting contract.
- **Decision**: Alembic config lives at `apps/api/alembic.ini` + `apps/api/alembic/`, co-located with the models that S1-06+ will add. Reason: keeps the entire DB story (config, models, migrations, session factory) in one workspace package; future `alembic revision --autogenerate` picks up `Base.metadata` from the same import root.
- **Decision**: `DATABASE_URL` in `.env` stays in canonical `postgresql://` form. SQLAlchemy session and Alembic `env.py` rewrite to `postgresql+asyncpg://` internally. Reason: keeps `psql`, `scripts/verify_pgvector.py` (psycopg), and the Neon CLI working from the same env var; no special-casing per tool.
- **Decision**: per-PR Neon branching support means `env.py` reads `DATABASE_URL` from environment - nothing else. The CI workflow in S1-33 will inject the per-PR branch URL into that env var. `env.py` does not call the Neon API; this keeps the migration code path identical whether it's pointed at local Postgres, a Neon branch, staging, or production.
- **Discovery**: `pytest-asyncio` in `auto` mode (`asyncio_mode = "auto"` from S1-01) creates a fresh event loop per test function. Connections held in a SQLAlchemy async engine's pool become bound to a closed loop between tests, surfacing as `RuntimeError: Event loop is closed` on the second DB-touching test. Fix used in `conftest.py`: build the test engine with `NullPool` and dispose it per-fixture. The production `engine` in `bullet_api.db.session` remains pooled and is unaffected.

**Verification (all passing, 11/05/2026, locally against docker-compose Postgres on port 5433)**

| Step | Command | Result |
|---|---|---|
| 1 | `uv sync --all-packages` | OK, 8 new packages installed (sqlalchemy 2.0.49, asyncpg 0.31.0, alembic 1.18.4, pydantic-settings 2.14.1, greenlet 3.5.0, mako/markupsafe) |
| 2 | `alembic upgrade head` | OK, runs `0001_create_extensions`; `vector` + `citext` rows present in `pg_extension`; `alembic_version=0001_create_extensions` |
| 3 | `alembic downgrade base` | OK, both extensions dropped; `alembic_version` empty |
| 4 | `alembic upgrade head` (re-apply) | OK, both extensions reinstalled, idempotent against pre-existing state |
| 5 | `pytest apps/api -v` against live DB | 3 passed (version smoke + 2 DB smoke) |
| 6 | `pytest apps/api -v` with unreachable DB | 1 passed, 2 skipped (skip path verified) |
| 7 | `ruff check apps/api` | All checks passed |

### 06/05/2026 - S1-02: Docker Compose local dev environment

- **Added**: `docker-compose.yml` at repo root brings up the local dev stack with two services - `pgvector/pgvector:pg16` (Postgres 16 with pgvector compiled in, named volume `bullet-postgres-data`, `pg_isready` healthcheck) and `inngest/inngest:latest` (dev server, UI on `localhost:8288`, started with `--no-discovery` and `-u http://host.docker.internal:8000/api/inngest` until S1-19 wires the worker). Linux compatibility via `extra_hosts: host.docker.internal:host-gateway`. Ports configurable through `.env`.
- **Added**: `scripts/verify_pgvector.py` - standalone verification script using PEP 723 inline metadata (declares `psycopg[binary]>=3.2,<4.0`) so `uv run scripts/verify_pgvector.py` works without touching `apps/api`'s pyproject. Connects via `DATABASE_URL`, runs `CREATE EXTENSION IF NOT EXISTS vector;`, asserts `pg_extension` row, prints `OK: pgvector extension available on <host>:<port>/<db>`. Idempotent.
- **Changed**: `.env.example` - appended local-stack defaults (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT`, `DATABASE_URL`, `INNGEST_DEV_PORT`, `INNGEST_DEV_SERVER_URL`). Defaults match `docker-compose.yml` so a fresh `cp .env.example .env` works end-to-end with no edits. S1-01's comment header preserved.
- **Changed**: `README.md` - added a new "Local development" section (setup, start, service URLs, verify, stop / reset, ops notes). Quickstart Docker bullet de-qualified (no longer references "added in Sprint 1 task S1-02"); now points at the new section.
- **Decision**: pgvector extension creation is owned by Alembic (S1-05's first migration), not by docker-compose `/docker-entrypoint-initdb.d/`. Reasoning: keeps the dev DB and the migrated DB shape-identical so "works locally, breaks on Neon" cannot happen at the schema level. The S1-02 test ("a script can `CREATE EXTENSION vector;`") still passes - the verify script runs the create itself and the image has the extension available.
- **Decision**: image choices - `pgvector/pgvector:pg16` (official combined image) over a `postgres:16 + custom Dockerfile`, and `inngest/inngest:latest` (un-pinned) for the dev server because it is local-only and Inngest publishes frequent fixes. Documented in the README.

### 06/05/2026 - S1-01: Monorepo scaffold

- **Added**: monorepo skeleton at the repo root. New top-level files: `package.json` (pnpm workspace root, `packageManager: pnpm@10.17.0`, Node `>=20`), `pnpm-workspace.yaml` (`apps/*` + `packages/*`), `.npmrc`, `pyproject.toml` (uv workspace, `requires-python >=3.12,<3.13`, members `apps/api`), `.python-version` (3.12), `.nvmrc` (20), `.editorconfig`, `.gitattributes`, `.env.example`, `Makefile` (single entrypoint covering both stacks). Workspace members: `apps/api/` (FastAPI scaffold via uv: `pyproject.toml`, `ruff.toml`, `src/bullet_api/__init__.py`, `tests/test_smoke.py`, README), `apps/dashboard/` (TS scaffold: `package.json`, strict `tsconfig.json`, `src/index.ts`, README), `packages/shared/` (TS scaffold: `package.json`, `tsconfig.json`, `src/index.ts`, README - empty placeholder until S1-17 codegen).
- **Added**: `.pre-commit-config.yaml` with `gitleaks` (secret scanning) plus the standard `pre-commit-hooks` hygiene set (trailing-whitespace, end-of-file-fixer, check-yaml/json/toml, check-merge-conflict, check-added-large-files=1024kb, mixed-line-ending=lf). Hygiene hooks exclude `docs/`, `emails/`, `meeting_notes/`, `questionnaire_responses/`, `scope/`, `progress-site/dist/` to avoid churning narrative content.
- **Added**: `.gitleaks.toml` extending the upstream default ruleset with an allowlist for the same narrative-content paths plus `.env.example`.
- **Added**: `.github/workflows/ci.yml` (three parallel jobs: pre-commit incl. gitleaks; pnpm typecheck/build with Node 20 + pnpm 10.17.0; uv ruff/pytest with Python 3.12 + uv 0.11.7) and `.github/workflows/lint-actions.yml` (actionlint).
- **Changed**: `.gitignore` - extended to cover `.env*` (with `!.env.example` exception), `.venv/`, `.pnpm-store/`, `.next/`, `out/`, `coverage/`, `*.tsbuildinfo`, `*.egg-info/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.idea/`, `.vscode/`, `*.log`, `logs/`. Original entries (macOS, output/, node_modules/, dist/, `__pycache__/`, `*.pyc`) preserved.
- **Changed**: `README.md` - rewritten to lead with monorepo quickstart (`pnpm install`, `uv sync --all-packages`, `uvx pre-commit install`) and repo layout, with the existing project-context narrative preserved below. All `docs/` links retained.
- **Decision**: GitHub repo to be renamed `tsizzybots/Bullet-Digital-Media-Onboarding` -> `tsizzybots/bullet_digital_media` to align with the PRD spec. GitHub auto-redirects HTTPS git URLs after rename, so the existing Render `progress-site` deploy continues to work; Render's repo connection will be updated post-rename so future webhooks use the new name.
- **Decision**: `progress-site/` stays at the repo root and is **not** a pnpm workspace member. The pnpm workspace globs are `apps/*` + `packages/*` only. Reason: progress-site is the client-facing static site (separate concern from the internal `apps/dashboard` ops surface), already deployed via `render.yaml`, and moving it would force a Render path change for no functional gain. `render.yaml` and `progress-site/` are untouched by this scaffold.
- **Decision**: secret-scanning tool is `gitleaks` via the `pre-commit` framework (vs detect-secrets or trufflehog). Reason: industry standard, single binary auto-installed by pre-commit, fast staged-files mode, catches the `sk_test_`-style keys named in S1-21's acceptance test.
- **Decision**: runtimes pinned to **Python 3.12 + Node 20 LTS**. Reason: both are current LTS lines, broadly supported by FastAPI / SQLAlchemy / Next.js 15, stable on Render. uv handles the local 3.12 download even on machines with 3.13 preinstalled.
- **Discovery**: `uv sync` from a workspace root only installs the root project, **not** workspace members. Required flag is `uv sync --all-packages`. Captured in the Makefile `install` target, the CI `python` job, and the README quickstart so future contributors don't hit it.
- **Discovery**: the `pre-commit` framework's `--all-files` mode operates on git-tracked files only. New (unstaged) files appear as "no files to check" in hook output until `git add`-ed. Worth knowing when interpreting first-run logs on a fresh scaffold.

**Verification (all passing, 06/05/2026)**

| Step | Command | Result |
|---|---|---|
| 1 | `pnpm install` | OK, 3 workspace projects |
| 2 | `uv sync --all-packages` | OK, 27 packages installed (fastapi, uvicorn, pytest, ruff, etc.) |
| 3 | `uvx pre-commit run --all-files` | OK, all hooks pass clean |
| 4 | Pre-commit blocks fake API key | OK, exit 1; gitleaks found 3 leaks (github-pat, stripe-access-token, generic-api-key) |
| 5 | `actionlint .github/workflows/*.yml` | OK, exit 0 |
| 6 | `pnpm -r typecheck` + `uv run pytest apps/api -q` | OK, both clean (1 pytest passed) |

### 06/05/2026 - Q-01 resolved: Resend single mailbox confirmed

- **Decision**: Q-01 (outbound email provider) confirmed by Bullet on 06/05/2026 - **single system mailbox via Resend**. All system-originated client emails (kick-off follow-up email both variants, technical-requirements email replacement, auth confirmation, dashboard alerts) send from one Bullet-owned mailbox (e.g. `onboarding@bulletdigitalmedia.com`) over Resend. Replies route via a catch-all rule into a shared Bullet inbox; per-message reply-to header preserves thread context where useful. No per-AM Gmail-API delegated-send work. GoHighLevel-native workflow emails (post-signing portal link, survey reminders) continue to fire from GHL where they still make sense.
- **Changed**: `docs/openquestions.md` - Q-01 moved to **Resolved** with the verbatim answer recorded.
- **Changed**: `docs/phase-1-plan.md` - Section 4 integrations table updated (Resend added as the system outbound channel; Gmail/GHL row demoted to legacy passthrough only). Section 11 "Resolved since the previous draft" gains the Q-01 entry.
- **Changed**: `docs/PRD.md` - Section 3 provisional table promoted to a confirmed-decisions table (Q-01 marked confirmed). Section 5.9 Gmail line rewritten to "not used for any system-originated outbound". Section 5.11 and Section 11.5 headings flipped from `(provisional, pending Q-01)` to `(confirmed - Q-01 resolved 06/05/2026)`. Section 8.1 `Sent` state and Section 12.2 acceptance criterion no longer hedged on Q-01. Section 13.1 cleared (no open implementation questions).
- **Changed**: `docs/development-sprints.md` - opening Q-01 assumption note and cross-cutting reminders updated to reflect Q-01 as resolved (was previously "treated as resolved for the purpose of this plan"). Tasks `S2-22`, `S2-23`, `S2-24`, `S2-25`, `S3-10`, `S3-11` are now locked to Resend.
- **Changed**: StrikeFlow card S2-22 (`AM Send action -> Resend dispatch`) - confirmation note added recording the Q-01 resolution.

### 04/05/2026 - Client progress page + /update-progress automation

- **Added**: `progress-site/` - standalone Vite + React 19 + Tailwind 4 + Framer Motion static site that renders all 92 StrikeFlow cards as a client-facing progress dashboard. Dark mode by default, single-brand IzzyAgents header, "By Sprint / By Status" grouping toggle, "All / Done / Active / Upcoming" filters, slide-in detail panel with description and notes timeline. Mirrors the WZY Revenue Dashboard pattern. Initial build verified: 391 modules, 711ms, no errors.
- **Added**: `scripts/transform_snapshot.py` - transforms a raw `mcp__strikeflow__boards_get_snapshot` response into the `BoardSnapshot` shape consumed by `progress-site/src/main.tsx`. Includes the 10 in-scope lists in fixed display order, normalises tags and notes defensively, warns on cards whose title doesn't begin with `S{N}-`.
- **Added**: `progress-site/src/data/board-snapshot.json` - initial snapshot generated 04/05/2026: Sprint 1 (35), Sprint 2 (27), Sprint 3 (14), Sprint 4 (16); other lists empty.
- **Added**: `.claude/skills/update-progress/SKILL.md` - `/update-progress` slash command. Fetches the live board, runs the transform, runs `vite build` to verify, commits the snapshot with a UK-format `chore: update progress dashboard snapshot - DD/MM/YYYY` message, then asks before pushing (push triggers Render auto-deploy).
- **Added**: `render.yaml` - Render Blueprint with one static-site service `bullet-progress` (publishes `progress-site/dist`, 1h cache, SPA rewrite). First deploy still requires creating the service manually in the Render dashboard pointed at `tsizzybots/bullet_digital_media`; every push thereafter auto-deploys.
- **Added**: `public/izzyagents-white.png` and `progress-site/public/izzyagents-white.png` (copied from WZY repo) - single-brand header logo.
- **Changed**: `.gitignore` - excludes `node_modules/`, `dist/`, and `__pycache__/`.
- **Decision**: client progress page mirrors the WZY pattern verbatim. All 92 internal cards are visible to the client (no curation, no description filtering). Reasoning: single source of truth, no editorial overhead, and Bullet sees the same TDD-shaped task content the build team works against - matches the "agnostic interface" long-term vision where everything goes through one front-end. Routine snapshot refreshes do **not** require a changelog entry; only structural changes (new sprint, new list) do.

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
