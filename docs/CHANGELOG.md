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

### 22/05/2026 - S1-19: Inngest setup (prod + staging + local)

- **Added**: `inngest==0.5.18` (+ `jcs==0.2.1`) to `apps/api/pyproject.toml` dependencies via `uv add "inngest>=0.4,<1.0"`.
- **Added**: `apps/api/src/bullet_api/worker/__init__.py` - new `worker` sub-package.
- **Added**: `apps/api/src/bullet_api/worker/client.py` - `_make_client()` builds an `inngest.Inngest` client with `app_id="bullet-api"`. Signing key and event key are injected from settings only when non-empty (local dev uses the Inngest dev server which requires neither). `noop_function` is a `@inngest_client.create_function` registered on the `bullet/noop` event - serves as connectivity smoke test. `FUNCTIONS` list is the single import point for `main.py`.
- **Added**: `apps/api/src/bullet_api/config.py` - three new settings: `inngest_signing_key`, `inngest_event_key`, `inngest_serve_path` (default `/api/inngest`). All default to empty/safe values for local dev.
- **Changed**: `apps/api/src/bullet_api/main.py` - imports `inngest.fast_api` and calls `inngest.fast_api.serve(app, inngest_client, FUNCTIONS)` to mount the serve endpoint at `/api/inngest`. Inngest Cloud calls this path to invoke registered functions. Local dev routes to the same endpoint via the Inngest dev server (docker-compose port 8288, `-u http://host.docker.internal:8000/api/inngest`).
- **Changed**: `render.yaml` `bullet-worker-staging` - upgraded from placeholder sleep to a logging health process. Comment explains Inngest functions are served via the FastAPI HTTP endpoint, not this worker dyno. Real job logic lands in S1-25+.
- **Verified**: `uv run python -c "from bullet_api.worker.client import FUNCTIONS; print('OK:', len(FUNCTIONS), 'functions')"` prints `OK: 1 functions`. `ruff check src/` clean.
- **Decision**: Inngest functions are served via the existing FastAPI web service (`/api/inngest`), not a dedicated worker process. Inngest Cloud calls the HTTP endpoint; the worker dyno in render.yaml is reserved for future out-of-band jobs that cannot run inside a request/response cycle.

### 22/05/2026 - S1-21: Render env groups + secret hygiene

- **Changed**: `render.yaml` `bullet-staging-env` and `bullet-prod-env` - added all Phase 1 secrets: `EMAIL_TOKEN_SECRET`, `DATABASE_SSL_MODE` (hardcoded `require` in both envs), `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_CONFIRMATION_BASE_URL`, `INNGEST_SIGNING_KEY`, `INNGEST_EVENT_KEY`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`. All secrets are `sync: false` (pasted into Render dashboard, never committed). `DATABASE_SSL_MODE` forces `require` for both staging and prod, overriding the `prefer` default in `config.py`.
- **Changed**: `render.yaml` `bullet-dashboard-staging` - upgraded from `type: static` HTML placeholder to a full `type: web` Next.js service (`runtime: node`, `rootDir: apps/dashboard`, `buildCommand: pnpm install && pnpm build`, `startCommand: pnpm start`, `healthCheckPath: /api/healthz`). Consumes `bullet-staging-env` group plus `NEXT_PUBLIC_API_URL` pointing at the staging API. Wires with S1-16 (Next.js scaffold).
- **Decision**: `DATABASE_SSL_MODE` is explicitly declared as `require` in the Render env groups rather than relying on the `prefer` default. This means a dev override cannot silently downgrade TLS in staging or prod.
- **Decision**: R2 env vars (transcript storage) declared now in S1-21 so they are in place before S1-27 lands, avoiding a second env-group edit pass at that point.

### 22/05/2026 - S1-33: GitHub Actions CI workflow

- **Added**: `.github/workflows/ci.yml` - Full PR/push CI workflow with four parallel jobs: `pre-commit` (gitleaks + hooks), `python-checks` (ruff lint + format check), `typescript-checks` (tsc + eslint best-effort), and `python-tests` (pytest with per-PR Neon branch).
- **Added**: `.github/CODEOWNERS` - Requires review from `@tsizzybots` for all files.
- **Added**: `.github/pull_request_template.md` - PR template with Summary, StrikeFlow card link, test plan checklist, and local lint/typecheck checklist.
- **Changed**: `ci.yml` trigger updated from `pull_request: branches: [main]` to `pull_request: types: [opened, synchronize, reopened]` for correct PR event coverage.
- **Changed**: `ci.yml` Python job split into `python-checks` (lint only, runs in parallel with TS checks) and `python-tests` (runs after `python-checks` passes) to give faster feedback on lint failures before running the full test suite.
- **Changed**: Local Postgres service container replaced with per-PR Neon ephemeral branches using `neondatabase/create-branch-action@v5` / `neondatabase/delete-branch-action@v5`. Branch is always torn down (`if: always()`) even on test failure.
- **Decision**: `continue-on-error: true` on Neon branch creation means tests still run against `NEON_STAGING_DATABASE_URL` fallback if the Neon API is temporarily unavailable, keeping CI unblocked.
- **Decision**: mypy disabled for now (TODO comment in workflow); will be enabled once mypy config is added to `apps/api/pyproject.toml`.
- **Decision**: eslint runs with `|| true` best-effort flag during dashboard conversion; will be tightened once `eslint-config-next` is fully configured.
- **Added**: Three GitHub secrets documented in workflow header comments: `NEON_API_KEY`, `NEON_PROJECT_ID`, `NEON_STAGING_DATABASE_URL` - must be set in repo settings before CI can run Python tests.

### 22/05/2026 - S1-16: Next.js 15 dashboard scaffold

- **Added**: `apps/dashboard/next.config.ts` - Next.js 15 config with `output: 'standalone'` for Render containerised deployments.
- **Added**: `apps/dashboard/tailwind.config.ts` - Tailwind CSS v3 config with `darkMode: 'class'`, custom CSS-variable-based colour tokens (background, foreground, primary, muted, border, card) wired to shadcn/ui conventions.
- **Added**: `apps/dashboard/postcss.config.mjs` - PostCSS config for Tailwind + Autoprefixer.
- **Added**: `apps/dashboard/components.json` - shadcn/ui base config (style: default, rsc: true, baseColor: slate, cssVariables: true). No components installed yet; config establishes the alias and CSS-variable contract for S1-31+.
- **Added**: `apps/dashboard/src/app/globals.css` - Tailwind directives + dark-palette CSS variable defaults in `:root`. Dark palette is set as the only palette (no light toggle); `html.dark` is always present.
- **Added**: `apps/dashboard/src/lib/utils.ts` - `cn()` helper combining clsx + tailwind-merge, per shadcn/ui convention.
- **Added**: `apps/dashboard/src/app/layout.tsx` - Root layout. Sets `<html lang="en" className="dark">` so dark mode is always active. Imports globals.css.
- **Added**: `apps/dashboard/src/app/page.tsx` - Root route; immediately redirects to `/clients`.
- **Added**: `apps/dashboard/src/app/login/page.tsx` - Login page placeholder (full implementation deferred to S1-18).
- **Added**: `apps/dashboard/src/app/clients/page.tsx` - Clients list placeholder (full implementation deferred to S1-31).
- **Added**: `apps/dashboard/src/app/api/healthz/route.ts` - Health check endpoint returning `{"status":"ok"}`. Public (excluded from middleware auth guard).
- **Added**: `apps/dashboard/src/middleware.ts` - Next.js middleware auth guard. Passes `/login` and `/api/healthz` through without auth check. All other routes require a `session` cookie; absent cookie redirects to `/login?next=<pathname>`.
- **Added**: `apps/dashboard/src/app/(founder)/layout.tsx`, `(pd)/layout.tsx`, `(am)/layout.tsx` - Route group layouts establishing role-boundary segments for future per-role pages.
- **Removed**: `apps/dashboard/src/index.ts` (TS stub), `apps/dashboard/dist/` (compiled stub output) - replaced by Next.js App Router structure.
- **Changed**: `apps/dashboard/package.json` - replaced TS-only devDep setup with full Next.js 15 + React 19 + Tailwind v3 + shadcn/ui utility dependency set.
- **Changed**: `apps/dashboard/tsconfig.json` - replaced outDir/rootDir-based config with Next.js-idiomatic tsconfig (noEmit, bundler moduleResolution, next plugin, @/* path alias).
- **Decision**: dark palette set as `:root` defaults (not a `.dark {}` override block). Since dark mode is always-on for this project, a single palette avoids the overhead of a class-toggled override chain.
- **Decision**: `output: 'standalone'` in next.config.ts from day one. Render deploys the dashboard as a Docker container; standalone output ensures the build artifact is self-contained without a full node_modules copy.

### 22/05/2026 - S1-15: POST /auth/logout + session lifecycle tests

- **Added**: `apps/api/src/bullet_api/auth/sessions.py` - `POST /auth/logout` endpoint. Reads the raw session token from the `session` cookie, computes its sha256 hash, deletes the matching row from `sessions`, clears the browser cookie via `response.delete_cookie`, and returns `{"status": "ok"}`. The `get_current_user` dependency is declared alongside the raw Cookie parameter so unauthenticated calls (no cookie, expired session) receive 401 before any DB work is attempted.
- **Added**: `apps/api/tests/test_sessions.py` - 4 tests: (a) logout deletes the session row, (b) same cookie is rejected by GET /me after logout, (c) an expired session is rejected at GET /me with 401, (d) logout with no cookie returns 401. All seed real user and session rows; all pass against Neon staging (22/05/2026, 38s).
- **Changed**: `apps/api/src/bullet_api/auth/__init__.py` - imports `logout_router` from `sessions.py` and adds it to `__all__`.
- **Changed**: `apps/api/src/bullet_api/main.py` - includes `logout_router` so the route is registered on the ASGI app.

### 18/05/2026 - S1-14: Resend email confirmation flow

- **Added**: `itsdangerous>=2.2,<3.0` and `httpx>=0.27,<1.0` API dependencies. itsdangerous signs the time-limited confirmation token (no users-table migration needed - the token is stateless); httpx is the HTTP client used by `ResendEmailClient`.
- **Added**: `apps/api/src/bullet_api/config.py` settings: `email_token_secret` (signing key, must be high-entropy in env), `resend_api_key`, `email_from` (defaults to `onboarding@bulletdigitalmedia.com` per Q-01 06/05/2026), `email_confirmation_base_url` (the dashboard URL the user clicks). Defaults are local-dev safe; staging/prod Render env groups override.
- **Added**: `apps/api/src/bullet_api/email/__init__.py` + `client.py` - `EmailClient` Protocol, `EmailMessage` dataclass, `ResendEmailClient` (httpx against Resend's `/emails` REST endpoint - fails loudly when `RESEND_API_KEY` is empty so a Render env-group misconfiguration cannot silently drop mail), `FakeEmailClient` for tests, `get_email_client()` FastAPI dependency.
- **Added**: `apps/api/src/bullet_api/auth/confirmation.py`:
  - `generate_confirmation_token(user_id)` and `_decode_confirmation_token(token)` using `itsdangerous.URLSafeTimedSerializer` with the `bullet-email-confirmation-v1` salt and a 24h `max_age` enforced at verification time.
  - `send_confirmation_email(user_id, email, email_client)` builds the link (`<base>/{token}`) and hands an `EmailMessage` to the injected client. Caller injects the client so tests can use `FakeEmailClient`.
  - `POST /auth/confirm/{token}` flips `email_confirmed=true` and stamps `email_confirmed_at`. Idempotent for already-confirmed users (200 with `status=already_confirmed`). Expired token → 410 with "Confirmation link expired" detail. Bad signature / unknown user → 400 with a generic "Invalid confirmation token" message so the endpoint doesn't leak user existence.
  - `POST /auth/resend-confirmation` with body `{email}`. Always returns 200 with `status=sent_if_unconfirmed` regardless of whether the email matched a real row or whether that row was already confirmed - prevents the endpoint from being a free user-enumeration oracle. Only sends when the email matched an unconfirmed user.
- **Changed**: `apps/api/src/bullet_api/main.py` - includes the new `confirmation_router`.
- **Added**: `apps/api/tests/test_confirmation.py` - 8 tests covering the full flow plus the S1-13 contract that an unconfirmed user can't log in is already covered there. Tests inject `FakeEmailClient` via `app.dependency_overrides[get_email_client]` and assert on `client.sent`.
- **Verified (Neon staging, 18/05/2026)**: `pytest tests/test_confirmation.py -v` - 8 passed in 54.92s.
- **Decision**: stateless signed tokens (itsdangerous) over a DB-stored token. Avoids a users-table migration; itsdangerous handles the timestamp + signature verification atomically; rotating `EMAIL_TOKEN_SECRET` (or the `_TOKEN_SALT` constant) invalidates every outstanding token without touching DB rows.
- **Decision**: `POST /auth/resend-confirmation` returns the same response regardless of whether the email is known. Existence-oracle paths are a recurring attack surface; closing this one now is cheap.
- **Decision**: production Resend client refuses to send when `RESEND_API_KEY` is empty rather than silently no-op'ing. Failure-to-send should be loud so it's caught at deploy time, not after a customer didn't get an email.

### 18/05/2026 - S1-13: argon2id login + brute-force lockout

- **Added**: `apps/api/src/bullet_api/auth/brute_force.py` - `BruteForceTracker` sliding-window per-IP failure counter. Defaults: 5 attempts in 15 minutes triggers a 15-minute lockout. Clock is injectable so tests can advance the lockout window without `time.sleep(900)`. Module-level singleton accessed via `get_tracker` dep so tests can swap in a fresh, clock-driven instance per test.
- **Added**: `apps/api/src/bullet_api/auth/login.py` - `POST /auth/login` route. argon2id verifies the supplied password against `users.password_hash`; on success mints `secrets.token_urlsafe(32)`, stores `sha256(token)` in `sessions` (with caller IP and User-Agent), updates `users.last_login_at`, and sets `Set-Cookie: session=<raw>; HttpOnly; Secure; SameSite=Lax; Max-Age=604800`. Brute-force lockout is checked before the DB lookup so a locked-out attacker can't time-probe valid emails. `email_confirmed=false` blocks login with 403 (the contract S1-14 will fulfil). `X-Forwarded-For` is honoured for caller-IP detection so Render's front-proxy reports the original client.
- **Added**: `email-validator>=2.2,<3.0` dependency - required by pydantic's `EmailStr` field on the login body.
- **Changed**: `apps/api/src/bullet_api/main.py` - includes the new `login_router`.
- **Changed**: `apps/api/tests/conftest.py` - `async_session` now binds to a per-test outer transaction with `join_transaction_mode="create_savepoint"`. Any `commit()` inside the test or handler under test only releases a savepoint; the outer transaction rolls back at teardown so the live-Neon test pattern stays clean.
- **Added**: `apps/api/tests/test_login.py` - 7 tests: valid creds issue HttpOnly+Secure+SameSite=Lax cookie + persist a sessions row; invalid password → 401; unknown email → 401; unconfirmed email → 403; 5 failures from an IP → 6th attempt returns 429 even with valid creds; lockout expires after the 15-minute window (clock advanced via injection, no sleep); lockout is per-IP (locked IP must not lock a different IP).
- **Verified (Neon staging, 18/05/2026)**: `pytest tests/test_login.py -v` - 7 passed in 61.30s. `ruff check src tests alembic` clean.
- **Decision**: in-memory brute-force tracker for now. Render starter plan is single-replica and restarts only on deploy, so the simpler implementation is safe for Sprint 1. The file is the swap point when the API scales out (S2+) - a Redis- or Postgres-backed equivalent ships in the same module.
- **Decision**: lockout check runs before the user lookup. Order matters - if the user lookup ran first, a locked-out attacker could time-probe valid emails (faster failure when row not found vs. row found + lockout check). Constant-time-ish behaviour from the attacker's perspective.

### 18/05/2026 - S1-12: FastAPI app shell + role dependencies

- **Added**: `apps/api/src/bullet_api/logging_config.py` - stdlib-only structured JSON logging. Each LogRecord renders as one-line JSON with `timestamp` (UTC ISO-8601), `level`, `logger`, `message`, plus any `extra={...}` fields passed by the caller. Idempotent installation so test re-entry and worker restarts do not stack handlers. uvicorn.error / uvicorn.access flow through the same handler for unified output on Render.
- **Added**: `apps/api/src/bullet_api/auth/dependencies.py` - `CurrentUser` dataclass, `get_current_user` FastAPI dep (reads `session` cookie, sha256-hashes, joins sessions→users with `expires_at > now()`), `require_role()` factory, and the four named role gates `require_founder` / `require_pd` / `require_am` / `require_engineer`. Hash matches PRD §4.9 / S1-15 plan: only sha256(token) is ever stored; raw tokens live only in the cookie. 401 for missing/expired/unknown cookie, 403 for wrong role.
- **Added**: `apps/api/src/bullet_api/auth/__init__.py` re-exporting the public surface.
- **Changed**: `apps/api/src/bullet_api/main.py` - now installs JSON logging on import, declares `/version` (returns `__version__`), `/me` (returns the current user, behind `get_current_user`), and `/admin/ping` (smoke endpoint for the `require_founder` gate). FastAPI's default OpenAPI 3.1 emission is preserved at `/openapi.json`. App version surfaced from `bullet_api.__version__`.
- **Added**: `apps/api/tests/test_auth_dependencies.py` - 9 tests using `httpx.AsyncClient` + `ASGITransport` (not fastapi.testclient TestClient, which spins its own anyio thread and triggers "Future attached to a different loop" against the test's asyncpg session). Covers `/healthz`, `/version`, 401 unauthenticated, 401 unknown cookie, 401 expired session, 200 founder, 403 AM, `/me` profile, OpenAPI 3.1 invariants.
- **Verified (Neon staging, 18/05/2026)**: `pytest tests/test_auth_dependencies.py -v` - 9 passed in 35.23s. `ruff check src tests alembic` - all clean.
- **Discovery**: fastapi.testclient.TestClient runs the ASGI app on a separate anyio thread with its own event loop. Mixing it with an `async_session` fixture that holds an asyncpg connection bound to the pytest loop raises `RuntimeError: Future attached to a different loop`. Workaround / convention: use `httpx.AsyncClient(transport=ASGITransport(app=app))` for tests that share a DB session with the request handler.
- **Discovery**: asyncpg cannot bind a Python `str` to a `(:exp)::interval` cast; it expects a `datetime.timedelta` for native interval values. Adopted convention: compute the absolute `expires_at` as `datetime.now(UTC) + timedelta(...)` and bind that timestamp directly, rather than the interval.

### 18/05/2026 - S1-11: idempotent team seeder

- **Added**: `argon2-cffi>=23.1,<26.0` dependency (`apps/api/pyproject.toml`). argon2id is the password hash used for every user row in `users.password_hash`; the seeder lands today, the login verifier in S1-13.
- **Added**: `apps/api/src/bullet_api/scripts/__init__.py` and `apps/api/src/bullet_api/scripts/seed_team.py` - idempotent seeder for the four named users. John Limber + Stephen Taylor (founder), Max + Luchiano (performance_director). Uses `INSERT ... ON CONFLICT (email) DO NOTHING RETURNING id` so re-runs produce the same state. Generates ~96-bit url-safe temp passwords per user, hashes via argon2id, sets `email_confirmed=false` so seeded users must complete the S1-14 confirmation flow before login.
- **Added**: `make db-seed-team` Makefile target.
- **Added**: `apps/api/tests/test_seed_team.py` - 4 `@pytest.mark.db` tests using a `test_` email prefix so they never collide with real team rows in staging. Covers idempotency (4 rows after two runs, not 8), argon2id verification of generated passwords, `email_confirmed=false` invariant, and role assignments matching the brief.
- **Verified (Neon staging, 18/05/2026)**: `pytest tests/test_seed_team.py -v` - 4 passed in 34.92s. `ruff check src tests alembic` clean.
- **Decision**: `seed_team()` accepts an optional `session=` parameter so tests can run it inside the standard rollback fixture; production CLI uses the no-arg form which manages its own session and commits. Also accepts `users=` so tests can use a `test_`-prefixed roster without touching the canonical `TEAM_USERS` constant.

### 18/05/2026 - S1-10: remaining tables migration (documents, research_results, client_assets, users, sessions, audit_log)

- **Added**: Alembic migration `0006_create_remaining_tables` covering PRD §§4.5-4.10 in one transaction. Five new ENUMs (`document_kind`, `research_result_kind`, `client_asset_type`, `client_asset_status`, `user_role`). `users.email` is `citext` with the `UNIQUE` constraint - case-insensitive collisions are enforced at the DB level. `sessions.token_hash` is `TEXT UNIQUE` (sha256 of the cookie value, never the raw token). `sessions.ip` and `audit_log.ip` use Postgres' native `INET` type rather than `TEXT` so IPv4/IPv6 lookups are first-class.
- **Added**: deferred FK from `client_knowledge.captured_by → users(id)` with `ON DELETE SET NULL`, added at the end of `0006`'s upgrade(). Declared as a plain UUID in 0003 because users didn't yet exist; the FK is now in place. (Downgrade drops the constraint before dropping users so the chain remains reversible.)
- **Added**: `apps/api/tests/test_remaining_tables.py` - 9 live-DB tests: users email UNIQUE, case-insensitive collision, user_role enum rejection, sessions token_hash UNIQUE, four enum rejections (document_kind, client_asset_status, research_result_kind, client_asset_type), and a positive + negative test of the deferred client_knowledge.captured_by FK.
- **Verified (Neon staging, 18/05/2026)**: `alembic upgrade head` clean (0005 → 0006). `alembic downgrade 0005_create_platform_actions` clean (all 6 tables and 5 enums dropped; deferred FK released first). Re-upgrade clean. `pytest tests/test_remaining_tables.py -v` - 9 passed in 50.73s.

### 18/05/2026 - S1-09: platform_actions table migration

- **Added**: Alembic migration `0005_create_platform_actions` per PRD §4.4. Two enums: `platform_action_platform` (16 values across hubspot, pandadoc, ghl, asana, stripe, xero, timely, slack, gsheets, gdocs, gdrive, gmail, gcal, meta, resend, firecrawl) and `platform_action_status` (pending, in_progress, success, failed, dead_lettered). `idempotency_key text NOT NULL UNIQUE` is the workhorse that keeps duplicate Inngest fan-outs from firing - the writer code will `INSERT ... ON CONFLICT DO NOTHING` and then read back the existing row. FK to clients (`ON DELETE CASCADE`) and a nullable FK to onboarding_events (`ON DELETE SET NULL`) so synthetic/reconciliation actions can exist without an event. Indexes: `client_id`, `(client_id, platform, action)`, `status`, `started_at DESC`, plus the auto-index on `idempotency_key` from the UNIQUE constraint.
- **Added**: `apps/api/tests/test_platform_actions_table.py` - 4 live-DB tests covering insert (with `retry_count` defaulting to 0), duplicate idempotency_key rejection, pending → in_progress → success status transitions, invalid platform enum rejection.
- **Verified (Neon staging, 18/05/2026)**: `alembic upgrade head` clean. `pytest tests/test_platform_actions_table.py -v` - 4 passed in 25.87s.

### 18/05/2026 - S1-07 + S1-08: client_knowledge and onboarding_events migrations

- **Added**: `0003_create_client_knowledge` per PRD §4.2. Source enum (`sales_call`, `agreement`, `portal`, `research`, `kickoff`, `manual`), `vector(1536)` embedding column (declared via an inline `UserDefinedType` so no pgvector Python dep is required for migrations), `jsonb` value with a GIN `jsonb_path_ops` index, ivfflat ANN index over embedding using `vector_cosine_ops` with `lists=100`. `client_id` FK has `ON DELETE CASCADE` so wiping a client also wipes its knowledge profile. `captured_by` is declared as a plain nullable UUID without an FK constraint - the FK to `users(id)` is added in 0006 (S1-10) once users exists.
- **Added**: `0004_create_onboarding_events` per PRD §4.3. `client_id` is nullable FK to clients (NULL until the PandaDoc signing event creates the client; orchestrator backfills the column). `(event_type, external_id) UNIQUE` is the idempotency key for webhook replays; NULL external_id rows do not collide (Postgres treats NULLs as distinct under UNIQUE, which is the behaviour we need for synthetic internal events).
- **Added**: `apps/api/tests/test_client_knowledge_table.py` - 4 live-DB tests covering GIN+ivfflat index existence, valid vector(1536) insert, source-enum rejection.
- **Added**: `apps/api/tests/test_onboarding_events_table.py` - 3 live-DB tests covering insert, duplicate (event_type, external_id) rejection, NULL external_id non-collision.
- **Verified (Neon staging, 18/05/2026)**: `alembic upgrade head` clean (`0002` → `0003` → `0004`). `alembic downgrade 0002_create_clients` clean (both tables and `client_knowledge_source` enum dropped). Re-upgrade clean. `pytest tests/test_client_knowledge_table.py tests/test_onboarding_events_table.py -v` - 7 passed in 36.31s against `STAGING_DATABASE_URL_POOLED`.
- **Discovery**: SQLAlchemy's `text()` bind-parameter parser does not handle `:name::pg_type(N)` cleanly when the parameter is followed by a Postgres double-colon cast (`::vector(1536)`, `::jsonb`, etc.). The bind marker `:vec` was left unresolved in the rendered SQL. Workaround: use the SQL-standard `cast(:vec AS vector(1536))` / `cast(:val AS jsonb)` form. Adopted as the convention for all later DB-touching tests that need an explicit Postgres type cast.

### 18/05/2026 - S1-06: clients table migration

- **Added**: Alembic migration `0002_create_clients` (`apps/api/alembic/versions/0002_create_clients.py`). Creates the `clients` table per PRD §4.1 with the two enum types `campaign_flow_type` (`low_ticket_checkout`, `high_ticket_consultation`) and `current_step` (`sales_call`, `agreement`, `signed`, `portal`, `kickoff`, `build`, `live`), 15 platform-ID text columns (`hubspot_contact_id` ... `slack_thread_ts`), self-referencing FK on `parent_client_id` with `ON DELETE SET NULL`, and the four indexes listed in the PRD (`email`, `current_step`, `created_at DESC`, `parent_client_id`). `id` defaults to `gen_random_uuid()` (Postgres 13+ built-in, available on Neon PG 16.12). `email` is `citext` so case-insensitive lookups work without per-query lower().
- **Added**: `apps/api/tests/test_clients_table.py` - 4 live-DB tests against the PRD contract. Each runs inside the per-test rollback fixture so test rows never persist.
- **Verified (against Neon staging, 18/05/2026)**:
  - `alembic upgrade head` (head → 0002) clean.
  - `alembic downgrade 0001_create_extensions` clean - both enum types and the table fully gone (`\dt` + `pg_type` confirm).
  - `alembic upgrade head` re-applies clean (round-trip safe).
  - `pytest tests/test_clients_table.py -v` → 4 passed against `STAGING_DATABASE_URL_POOLED`.
  - `ruff check src tests alembic` clean.
- **Discovery**: SQLAlchemy emits a duplicate `CREATE TYPE` when an explicit `postgresql.ENUM(...).create(bind)` is paired with a column whose type is a *different instance* of `sa.Enum(name=..., create_type=False)` for the same name; passing `create_type=False` on the generic `sa.Enum` is not enough to suppress the emit. Fix: build the `postgresql.ENUM(..., create_type=False)` instance once, call `.create()` on it, then reuse the same object as the column type. This pattern is now the convention for every future migration that needs an enum.

### 18/05/2026 - S1-05a: Neon URL compat fix for asyncpg

- **Fixed**: `bullet_api.config.get_async_database_url()` now strips libpq-only query params (`sslmode`, `channel_binding`, `sslrootcert`, `sslcert`, `sslkey`, `sslpassword`, `sslcrl`, `sslcrldir`, `sslcompression`, `gssencmode`, `krbsrvname`, `gsslib`) before returning the URL. Previously the query string passed through unchanged, so Neon's canonical URL (`?sslmode=require&channel_binding=require`) crashed asyncpg at connect time with `TypeError: connect() got an unexpected keyword argument 'sslmode'`. Verified `alembic upgrade head` against `STAGING_DATABASE_URL_DIRECT` now completes cleanly. (`apps/api/src/bullet_api/config.py`)
- **Added**: `Settings.database_ssl_mode` (env var `DATABASE_SSL_MODE`) - typed `Literal["disable","allow","prefer","require","verify-ca","verify-full"]`, default `"prefer"`. Applied through `connect_args={"ssl": ...}` on the SQLAlchemy async engine in `bullet_api/db/session.py` and on the test-pool engine in `tests/conftest.py`. `"prefer"` works against both local docker Postgres (no TLS, falls back to plain) and Neon (TLS mandatory, auto-upgraded); Render env groups for staging / prod should force `"require"` or stricter so a mis-configured local override cannot disable TLS in those environments. (`apps/api/src/bullet_api/config.py`, `apps/api/src/bullet_api/db/session.py`, `apps/api/tests/conftest.py`, `.env.example`)
- **Added**: `apps/api/tests/test_config.py` - 8 unit tests covering the URL rewrite: Neon canonical URL stripped, both `postgresql://` and `postgres://` short schemes rewritten, already-async URLs preserved with query cleaned, unknown schemes returned unchanged, no-query idempotency, case-insensitive key matching, and the rest of the libpq-only deny-list (`sslrootcert`, `gssencmode`, etc.). All pass; non-libpq params like `application_name` survive the round-trip.
- **Verified**: `pytest -v` runs 12 tests, all pass (the 8 new config tests + 2 DB smoke tests against `STAGING_DATABASE_URL_POOLED` + 2 baseline). `ruff check src tests` clean. `alembic current` against Neon staging direct URL returns `0001_create_extensions (head)`; `psql` confirms both `vector` and `citext` extensions present.
- **Discovery**: asyncpg as of 0.30 accepts ssl modes as strings (`"disable"|"allow"|"prefer"|"require"|"verify-ca"|"verify-full"`) on the `ssl=` kwarg, matching libpq vocabulary. Documented for future env-group config.

### 18/05/2026 - Render workspace remediation executed

- **Added**: Render Blueprint instance `bullet-digital-media` (`exs-d85d158js32c73aefn70`) connected to `tsizzybots/Bullet-Digital-Media-Onboarding` `main` in the client-owned `Agents's workspace` (`tea-d80mkougvqtc73dmah20`). First sync committed `88c0930`. Created in the workspace: env groups `bullet-staging-env` (`evg-d85d1brtqb8s73fu56fg`) and `bullet-prod-env` (`evg-d85d1brtqb8s73fu56h0`); web service `bullet-api-staging` (`srv-d85d1brtqb8s73fu56n0`); background worker `bullet-worker-staging` (`srv-d85d1brtqb8s73fu56mg`); cron job `bullet-cron-staging` (`crn-d85d1brtqb8s73fu56m0`); static site `bullet-dashboard-staging`. All four services are Blueprint-managed; staging env group linked to API, worker, and cron.
- **Added**: `DATABASE_URL` secret populated in `bullet-staging-env` (Neon staging pooled URL) and `bullet-prod-env` (Neon prod pooled URL) via the Render dashboard. Values match `.env.neon` (gitignored).
- **Verified**: `curl https://bullet-api-staging.onrender.com/healthz` returns HTTP 200 `{"status":"ok"}` in ~325ms - confirms the S1-04 `/healthz` test contract is now satisfied end-to-end against the client workspace.
- **Removed**: orphaned env groups in IzzyAgents workspace (`tea-cunci5popnds73d4n8g0`) - `bullet-staging-env` (`evg-d8591rh9rddc73a5c7gg`) and `bullet-prod-env` (`evg-d85926t7vvec73fr2drg`, the actual prod env group ID; earlier CHANGELOG referenced a placeholder). Both had zero linked services at time of deletion. IzzyAgents workspace now contains zero env groups for this project.
- **Discovery**: Render's env-group delete confirmation requires typing `sudo delete <group-name>` into a Sudo Command field - a hard interlock against accidental destruction. Useful to know for future cleanup or scripting.

### 18/05/2026 - Repo name and Render service name doc corrections

- **Discovery**: the GitHub repo rename from `tsizzybots/Bullet-Digital-Media-Onboarding` to `tsizzybots/bullet_digital_media` recorded as a Decision under 11/05/2026 (`docs/CHANGELOG.md` line 120) and as Added under the same date never actually executed on GitHub. Confirmed today via `gh repo view`: only `tsizzybots/Bullet-Digital-Media-Onboarding` exists; the new name returns "Could not resolve to a Repository". A push against the new URL today failed with exit 128 "Repository not found"; rolling the remote URL back to the original name made the push succeed. The historical CHANGELOG line is left in place as a record of what was decided at the time; this entry supersedes it.
- **Discovery**: similarly, the Render service for the client progress dashboard is named `Bullet-Digital-Media-Progress` (slug `bullet-digital-media-progress`, `srv-d7s18ajbc2fs738mgei0`, URL `https://bullet-digital-media-progress.onrender.com`) - not `bullet-progress` as documented in several places. The package name in `progress-site/package.json` is `bullet-progress`, which is the source of the doc drift, but the Render service was named differently when it was created via "New Static Site" in the dashboard.
- **Decision**: do not execute the GitHub repo rename. Keep `tsizzybots/Bullet-Digital-Media-Onboarding` as the canonical repo name and update all active docs to match. Rationale: rename is reversible later if wanted, but every doc currently referencing the wrong name is actively misleading future contributors and Claude sessions. Faster to align docs to reality than to chase a rename that did not happen.
- **Changed**: `CLAUDE.md` `## GitHub` section - corrected repo name and added a parenthetical noting the planned rename was never executed.
- **Changed**: `package.json` `repository.url` - corrected to `Bullet-Digital-Media-Onboarding.git`.
- **Changed**: `docs/PRD.md` §11.6 CI/CD - corrected repo reference.
- **Changed**: `docs/infrastructure.md` §B12 GitHub - corrected repo reference.
- **Changed**: `docs/development-sprints.md` S1-01 description - corrected repo reference.
- **Changed**: `.claude/skills/update-progress/SKILL.md` - corrected repo name in two places and corrected the Render service name from `bullet-progress` to `bullet-digital-media-progress` in three places.

### 18/05/2026 - Render workspace remediation (S1-03 / S1-04 follow-up)

- **Decision**: the canonical Render workspace for this project is the **client-owned** `Agents's workspace` (`tea-d80mkougvqtc73dmah20`, owner `agents@bulletdigitalmedia.com`), not the IzzyAgents workspace (`tea-cunci5popnds73d4n8g0`) where S1-03 originally created the env groups and S1-04 wired the staging services. Reason: billing, ownership, and access control sit with the client. All Phase 1 services (`bullet-api-*`, `bullet-worker-*`, `bullet-cron-*`, `bullet-dashboard-*`) and env groups (`bullet-staging-env`, `bullet-prod-env`) live in the client workspace going forward.
- **Discovery**: Neon org "Bullet Launch" was confirmed already client-owned via the Neon People screen: `agents@bulletdigitalmedia.com` is Admin, `team@izzyagents.ai` is Member. No Neon org migration required - only the Render env groups + services need to be recreated in the client workspace. Follow-up: bump `team@izzyagents.ai` from Member to Admin so per-PR Neon branching (S1-33) can be managed without involving the client.
- **Discovery**: Render's MCP surface exposes no `create_env_group` action. Env groups can only be created via (a) Blueprint sync auto-creating them from `envVarGroups:` in `render.yaml`, (b) the Render dashboard manually, or (c) the Render REST API directly. The Blueprint-sync path was chosen for this remediation.
- **Discovery**: the IzzyAgents Render workspace has **no Blueprint** connection to this repo. The `render.yaml` `bullet-progress` block declared since 04/05/2026 was aspirational; the actually-deployed static site in IzzyAgents is named `Bullet-Digital-Media-Progress` (slug `bullet-digital-media-progress`, `srv-d7s18ajbc2fs738mgei0`, URL `https://bullet-digital-media-progress.onrender.com`) and was created via the "New Static Site" flow with its own per-service GitHub connection. It auto-deploys on every push to `main` independent of `render.yaml`. Implication: removing the `bullet-progress` block from `render.yaml` has zero operational impact on the existing service; no Blueprint disconnect step is required before pushing.
- **Discovery**: despite the S1-04 commit (`3f32ff5 feat: wire bullet-api-staging + Render staging blueprint (S1-04)`) landing in the repo, no `bullet-api-staging`, `bullet-worker-staging`, `bullet-dashboard-staging`, or `bullet-cron-staging` services have been created in any Render workspace. The Blueprint connection that would have instantiated them was never made. Implication: S1-04 service provisioning is still pending - the upcoming Blueprint sync in the client workspace will create those four services fresh, not migrate them.
- **Changed**: `render.yaml` - removed the never-instantiated `bullet-progress` static-site block. Top-of-file comment now documents the canonical workspace target (`tea-d80mkougvqtc73dmah20`) and clarifies that the IzzyAgents-side `Bullet-Digital-Media-Progress` deploys via its own per-service GitHub connection rather than this Blueprint.
- **Removed**: `bullet-progress` block from `render.yaml` services list.

### 18/05/2026 - S1-03: Neon Postgres provisioning (prod + staging)

- **Added**: two Neon projects in the "Bullet Launch" org, both in AWS Europe West 2 (London), Postgres 16.12, default branch `main`, default DB `neondb`, default role `neondb_owner`:
  - `bullet-prod` (project id `mute-moon-52846962`, primary compute `ep-summer-hill-ab4ediqn`).
  - `bullet-staging` (project id `old-credit-26332098`, primary compute `ep-mute-mode-ab75uu4u`).
- **Added**: `vector` (already present from Neon control plane) and `citext` extensions enabled on both `bullet-prod` and `bullet-staging`. Verified via `SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','citext')` against both pooled endpoints: `vector 0.8.0` + `citext 1.6` on each.
- **Added**: two Render environment groups in the IzzyAgents workspace (`tea-cunci5popnds73d4n8g0`) - `bullet-staging-env` (`evg-d8591rh9rddc73a5c7gg`) and `bullet-prod-env`. Each holds a single `DATABASE_URL` secret pointing at the corresponding Neon **pooled** endpoint (`-pooler` hostname suffix). `PYTHON_VERSION` + `NODE_VERSION` will be merged in on the first blueprint sync (S1-04 onwards) - no need to set them manually.
- **Added**: `render.yaml` now declares a second `envVarGroups` entry `bullet-prod-env` mirroring `bullet-staging-env` (`DATABASE_URL` with `sync: false`, plus `PYTHON_VERSION=3.12` and `NODE_VERSION=20`). The group exists ahead of any prod services so the secret is in place before those services boot in a later Sprint 1 task.
- **Added**: top-of-file comment in `render.yaml` updated to reflect that the prod env group is declared up-front in S1-03 (was: "production services are added later... with a separate env group" without distinguishing the group from the services).
- **Added**: `.env.neon` (gitignored via `.env.*` rule) at the repo root captures all four connection strings - `PROD_DATABASE_URL_POOLED`, `PROD_DATABASE_URL_DIRECT`, `STAGING_DATABASE_URL_POOLED`, `STAGING_DATABASE_URL_DIRECT`. Values are quoted so the `&` in `sslmode=require&channel_binding=require` is safe to `source` from a shell. Used locally for psql verification; never read at app runtime (app reads `DATABASE_URL` from `.env` or Render env group).
- **Changed**: `.gitignore` now also ignores `.playwright-mcp/`. The Playwright MCP tool writes page snapshots and console logs into that directory while driving the browser, and those artefacts can contain secrets that should never be committed.
- **Decision**: Phase 1 Render services live in the IzzyAgents workspace alongside the existing `Bullet-Digital-Media-Progress` static site, not in Bullet's own Render workspace. Reason: keeps all Bullet Phase 1 infrastructure in one billing/admin surface for now; can be migrated to Bullet's workspace later when the engagement transitions to client-paid infra. Implication: the workspace selection is implicit in whichever account first syncs the blueprint - no `render.yaml` change is needed when the migration happens, but env group values and service identities will have to be recreated in the new workspace.
- **Decision**: app runtime always uses Neon's **pooled** endpoint (`-pooler` hostname); Alembic and other DDL paths use the **direct** endpoint. Reason: pgbouncer transaction-pooling mode doesn't support some DDL (advisory locks, prepared statements, `SET LOCAL` quirks), and Alembic relies on those during migrations. Implication: the per-PR Neon branch path in S1-33 must also expose both forms.
- **Discovery**: Neon **pre-installs `vector` on every new project** as part of the control plane's default extension set. Only `citext` had to be created. The migration `0001_create_extensions` is still correct (`IF NOT EXISTS`-guarded), but the implication is that fresh Neon environments are already partway through the migration before Alembic runs - the migration's idempotency is doing real work here, not just defensive coding.
- **Discovery**: **`apps/api` Alembic stack cannot connect to Neon as-is.** Neon's canonical URL ships with `?sslmode=require&channel_binding=require`; `bullet_api.config.get_async_database_url()` swaps the scheme prefix but passes the query string through unchanged. asyncpg rejects libpq-only params (`sslmode`, `channel_binding`) with `TypeError: connect() got an unexpected keyword argument 'sslmode'`, so `alembic upgrade head` against a Neon URL fails immediately. **Open follow-up**: strip libpq-only query params in `get_async_database_url()` (and configure SSL via asyncpg's `ssl=` connect arg instead) before the Render-deployed `bullet-api-staging` first boots against `bullet-staging-env`, otherwise the service will crash at startup the moment it tries to open a pool. This is S1-05/S1-12 follow-up scope, not S1-03 - S1-03 satisfied its `pg_extension` test by running `CREATE EXTENSION` via psql against the direct endpoint.
- **Discovery**: Render MCP server **does not expose env group CRUD**. `update_environment_variables` only sets vars on individual services; there is no `create_env_group` / `update_env_group` / `list_env_groups` tool. Env groups have to be created and edited via the Render dashboard (or the underlying REST API directly, bypassing the MCP surface). Used a Playwright-driven dashboard flow for this task.
- **Discovery**: Neon's "Show password" toggle in the Connect dialog reveals a single password per role, **independent of the pooling toggle**. The pooled and direct URLs for the same project share an identical password; only the hostname differs (`-pooler` suffix on or off). This means once the password is revealed under either pooling setting, both URLs can be reconstructed without flipping the toggle.

**Verification (all passing, 18/05/2026)**

| Step | Command | Result |
|---|---|---|
| 1 | `psql "$STAGING_DATABASE_URL_POOLED" -c "SELECT version();"` | OK, PostgreSQL 16.12 on aarch64-unknown-linux-gnu |
| 2 | `psql "$PROD_DATABASE_URL_POOLED" -c "SELECT version();"` | OK, PostgreSQL 16.12 on aarch64-unknown-linux-gnu |
| 3 | `psql "$STAGING_DATABASE_URL_DIRECT" -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS citext;"` | OK (vector NOTICE: already exists; citext CREATE EXTENSION) |
| 4 | `psql "$PROD_DATABASE_URL_DIRECT" -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS citext;"` | OK (vector NOTICE: already exists; citext CREATE EXTENSION) |
| 5 | `psql "$STAGING_DATABASE_URL_POOLED" -c "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','citext');"` | 2 rows: `citext 1.6`, `vector 0.8.0` |
| 6 | `psql "$PROD_DATABASE_URL_POOLED" -c "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','citext');"` | 2 rows: `citext 1.6`, `vector 0.8.0` |
| 7 | Render dashboard - `bullet-staging-env` env group | Exists, 1 variable (`DATABASE_URL` = staging pooled URL, value masked server-side) |
| 8 | Render dashboard - `bullet-prod-env` env group | Exists, 1 variable (`DATABASE_URL` = prod pooled URL, value masked server-side) |

### 14/05/2026 - S1-04: Render staging service provisioning

- **Added**: `apps/api/src/bullet_api/main.py` - minimal FastAPI app exposing `GET /healthz` returning `{"status":"ok"}`. Module-level imports kept to FastAPI only; no `Settings` import at boot, so the endpoint stays cheap and the test does not need a `DATABASE_URL` fixture. Real routes, DB session wiring, middleware, and Sentry hook land in S1-12 onwards.
- **Added**: `apps/api/tests/test_healthz.py` - smoke test using `fastapi.testclient.TestClient` that asserts HTTP 200 and exact body `{"status":"ok"}`. Mirrors the Render Blueprint health-check contract so any regression fails locally before the deploy round-trip.
- **Added**: `httpx>=0.27,<1.0` to `apps/api` dev deps (required by `fastapi.testclient.TestClient`).
- **Added**: `render.yaml` staging service block. New `envVarGroups` entry `bullet-staging-env` holds `DATABASE_URL` (`sync: false`, pasted into Render once), `PYTHON_VERSION=3.12`, `NODE_VERSION=20`. Four new services declared:
  - `bullet-api-staging` (`type: web`, `rootDir: apps/api`, build = `pip install uv && uv sync --frozen`, start = `uv run uvicorn bullet_api.main:app --host 0.0.0.0 --port $PORT`, `healthCheckPath: /healthz`) - **fully functional**.
  - `bullet-worker-staging` (`type: worker`, same `rootDir: apps/api`) - placeholder `uv run python -c "...sleep loop..."` that prints a marker line then sleeps; replaced in S1-19 when Inngest worker registration lands.
  - `bullet-dashboard-staging` (`type: web`, `runtime: static`) - placeholder one-pager; replaced in S1-16 with the real Next.js build + `app/healthz/route.ts`.
  - `bullet-cron-staging` (`type: cron`, schedule `0 2 * * *`, `rootDir: apps/api`) - placeholder `print(...)`; replaced when the first reconciliation job lands.
- **Added**: `Makefile` target `api-dev` running `uv run uvicorn bullet_api.main:app --reload --host 0.0.0.0 --port 8000`. Keeps local and Render start commands aligned (Render omits `--reload`).
- **Decision**: S1-04 scope narrowed to `api fully wired + placeholders for the other three`. Reason: the api scaffold from S1-05 was already deep enough to deploy meaningfully, while worker/dashboard/cron have no app code yet and their owning tasks (S1-19, S1-16, future) are where the real shells belong. Implication: their `render.yaml` blocks get re-edited (`rootDir`, `startCommand`, `runtime`) when those tasks land - same file, no new infrastructure scaffolding.
- **Decision**: `bullet-cron-staging` uses Render's native cron-job type (not a long-running worker with an internal scheduler). Reason: matches the PRD §11.1 wording ("Cron - daily reconciliation 03:00 UK"), bills per-run instead of always-on, and the Render dashboard's "Run Now" gives a clean exit-code-based verification path. Implication: cron has no `/healthz` endpoint; the S1-04 test "Each staging service serves `/healthz`" applies to the three long-running services (api, worker, dashboard).
- **Decision**: `bullet-worker-staging` and `bullet-cron-staging` both point at `rootDir: apps/api` for the duration of the placeholder phase. Reason: they need a buildable Python package to install dependencies against, and `apps/api` is the only one that exists yet. S1-19 moves the worker to its own `apps/worker/` package (or keeps it in `apps/api` and just adds an Inngest worker entrypoint - decision deferred to S1-19).
- **Decision**: `DATABASE_URL` is the only secret in `bullet-staging-env` for now (`sync: false`). Other secrets (Anthropic, OpenAI, Resend, HubSpot, PandaDoc, Stripe, Xero, GHL, Asana, Sentry DSN, Firecrawl, R2) get added to the same env group as each integration task lands. Keeps the env group as the single source of truth per `docs/infrastructure.md` Section A3.
- **Discovery**: `fastapi.testclient.TestClient` requires `httpx` at runtime (not just `starlette`). Adding it surfaced the dev-dep gap before the first endpoint test was written.

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
