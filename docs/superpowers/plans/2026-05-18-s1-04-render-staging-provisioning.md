# S1-04: Render Staging Service Provisioning - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the four `bullet-*-staging` Render services live in the IzzyAgents workspace, wired to `bullet-staging-env`, with `bullet-api-staging` serving `/healthz` over HTTPS and a verified zero-downtime restart.

**Architecture:** The blueprint (`render.yaml`) and the `/healthz` endpoint code are already committed locally (commit `3f32ff5`). This task is **almost entirely operational**: push the commits, sync the Render blueprint, then verify each service end-to-end. No new application code is expected unless verification surfaces a bug.

**Tech Stack:** Render Blueprints (render.yaml), FastAPI (`uv run uvicorn`), Render env groups, Neon Postgres (URL only - no DB calls at this stage), GitHub auto-deploy via `branch: main`.

---

## Pre-flight State (verified 18/05/2026)

- **Local commits ahead of origin/main:** 4 (`3f32ff5`, `af02d77`, `1ae573d`, `e08f5ce`). **None are pushed yet.** Until they are, Render's blueprint sync cannot see the new services.
- **render.yaml in repo:** declares `bullet-progress` (already live) + `bullet-api-staging` + `bullet-worker-staging` + `bullet-dashboard-staging` + `bullet-cron-staging` + env groups `bullet-staging-env` and `bullet-prod-env`. File path: `render.yaml`.
- **`apps/api/src/bullet_api/main.py`:** exposes `GET /healthz` returning `{"status":"ok"}`. Module-level imports are FastAPI-only - no DB pool opens at boot.
- **`apps/api/tests/test_healthz.py`:** passes locally (1 passed in 0.14s, confirmed 18/05/2026).
- **Render workspace:** IzzyAgents (`tea-cunci5popnds73d4n8g0`). Already has `bullet-progress` static site live; this is the workspace's first blueprint sync that adds non-static services.
- **`bullet-staging-env` env group:** exists (`evg-d8591rh9rddc73a5c7gg`), already holds `DATABASE_URL` = staging pooled Neon URL. `PYTHON_VERSION` + `NODE_VERSION` will be merged in on this first blueprint sync.
- **`bullet-prod-env` env group:** exists, holds prod pooled `DATABASE_URL`. No prod services consume it yet - this task does not change that.
- **GitHub account required:** `tsizzybots` (per project CLAUDE.md). Repo: `tsizzybots/bullet_digital_media`.

---

## Scope Clarifications (locked in by 14/05/2026 changelog entry)

The original task wording says *"Each staging service serves a `/healthz` endpoint returning `{"status":"ok"}` over HTTPS"*. The committed scope narrowed this:

| Service | Type | `/healthz` contract | Verification path |
|---|---|---|---|
| `bullet-api-staging` | web (FastAPI) | **Full JSON `/healthz` over HTTPS** | `curl https://bullet-api-staging.onrender.com/healthz` |
| `bullet-worker-staging` | worker (background) | **Exempt** - no HTTP port | Logs show placeholder marker line; service status = "live" |
| `bullet-dashboard-staging` | web (static placeholder) | **Deferred to S1-16** - placeholder is a one-page HTML, no route handler. The root URL must return 200 over HTTPS. | `curl -I https://bullet-dashboard-staging.onrender.com/` |
| `bullet-cron-staging` | cron (one-shot) | **Exempt** - no HTTP surface | "Run Now" exits 0 in Render UI |

Per-service env-group scoping (the second half of the test contract) is satisfied by every long-running service declaring `envVars: - fromGroup: bullet-staging-env` in `render.yaml`; verification is a dashboard check that each service shows the env-group binding.

---

## File Structure

This task creates no new source files. Expected modifications:

- Modify: `docs/CHANGELOG.md` (append S1-04 provisioning entry under `[Unreleased]`)
- Potentially modify: `render.yaml` (only if a verification step surfaces a fix; not expected)

No `apps/api/**` changes expected. No `progress-site/**` changes.

---

## Risks and Constraints

1. **The 4 unpushed commits are a hard blocker.** Render auto-deploys from `main` and can only see what's been pushed. Step 1 must succeed before anything downstream works.
2. **`tsizzybots` is the correct GitHub account.** Pushing under the wrong identity will create a permission error or push to the wrong fork. `gh auth status` check is mandatory.
3. **Neon `DATABASE_URL` will crash asyncpg if anything imports it** (S1-03 changelog discovery: `?sslmode=require&channel_binding=require` are libpq-only params asyncpg rejects). This **does not bite us in S1-04** because `bullet_api.main` does not import `Settings` and the `/healthz` handler does not touch the DB. But the moment S1-12 lands and DB session wiring is added, the env-group `DATABASE_URL` will need fixing in `bullet_api.config.get_async_database_url()`. **Flag in the changelog as a still-open follow-up.**
4. **First blueprint sync risk:** the IzzyAgents workspace has so far only synced the `bullet-progress` static site. Adding four more services in one sync is the first multi-service sync. Common failure modes: env group not yet attached when service first builds, branch protection blocking auto-deploy, `rootDir` path mismatch, `uv sync --frozen` failing if `uv.lock` drifts.
5. **`uv sync --frozen`** will fail the build if `uv.lock` is out of sync with `pyproject.toml`. `uv.lock` is currently committed; verify locally with `uv sync --frozen` from `apps/api` before pushing.
6. **Starter plan + zero-downtime restart:** Render's `starter` plan is a single instance. "Zero downtime" on starter is typically achieved by deploying a new instance, waiting for it to pass `healthCheckPath`, then cutting traffic. We must verify this empirically (not just trust Render's docs).
7. **Dashboard placeholder has no `/healthz`.** A literal reading of the task description would fail this. The scope narrowing decision is documented in the 14/05/2026 changelog entry; restate it in the new entry so the audit trail is intact.
8. **The Render MCP server does not expose env-group CRUD** (S1-03 discovery). If env-group merging during sync misbehaves, the fallback is the Playwright-driven dashboard flow already proven in S1-03.

---

## Task Decomposition

The plan is sequenced as: pre-flight checks → push → blueprint sync → per-service verification → zero-downtime check → log → close card.

---

### Task 1: Verify local state and GitHub account

**Files:**
- Read: `apps/api/src/bullet_api/main.py`, `apps/api/tests/test_healthz.py`, `render.yaml`, `apps/api/pyproject.toml`, `apps/api/uv.lock`

- [ ] **Step 1: Confirm local healthz test still passes**

Run: `uv run pytest apps/api/tests/test_healthz.py -v`
Expected: `1 passed`. Already verified at plan-write time but re-run before push - this is the contract the deployed service must satisfy.

- [ ] **Step 2: Confirm `uv.lock` is in sync with `apps/api/pyproject.toml`**

Run: `cd apps/api && uv sync --frozen`
Expected: exits 0 with no "out of sync" warning. If it fails, regenerate the lock (`uv lock`), commit, and re-run.

- [ ] **Step 3: Confirm GitHub account is `tsizzybots`**

Run: `gh auth status`
Expected: active account is `tsizzybots`. If not, run `gh auth switch --user tsizzybots`. If that account is not authenticated at all, stop and report to user - do not proceed.

- [ ] **Step 4: Confirm we have exactly the expected commits ahead of origin**

Run: `git log --oneline origin/main..main`
Expected: 4 commits, top one is `3f32ff5 feat: wire bullet-api-staging + Render staging blueprint (S1-04)`. If the list differs, stop and report - do not push unexpected commits.

- [ ] **Step 5: Confirm working tree is clean**

Run: `git status --short`
Expected: empty output. The plan document itself is in `docs/superpowers/plans/` which is gitignored... actually, this dir is not gitignored. If the plan file shows up, that is acceptable and will be committed at the end (Step in Task 8).

---

### Task 2: Push the four pre-existing commits

**Files:**
- No file changes - this is a remote-state operation only.

- [ ] **Step 1: Push to origin/main**

Run: `git push origin main`
Expected: 4 commits pushed cleanly. If push fails on pre-commit hook or remote rejection, do not use `--no-verify` or `--force` - stop and report.

- [ ] **Step 2: Confirm push landed**

Run: `gh api repos/tsizzybots/bullet_digital_media/commits/main --jq '.sha'`
Expected: returns `3f32ff5...` (full SHA, with `3f32ff5` prefix).

- [ ] **Step 3: Verify CI passes on the pushed commits**

Run: `gh run list --branch main --limit 3`
Expected: top run is `success` (or `in_progress`; wait for completion if so). If a CI job fails, stop and triage - do not proceed to Render sync with a red build.

---

### Task 3: Trigger Render blueprint sync

**Files:**
- No file changes.

- [ ] **Step 1: Confirm Render MCP workspace is set to IzzyAgents**

Run via MCP: `mcp__render__get_selected_workspace`
Expected: returns the workspace where `bullet-progress` lives (`tea-cunci5popnds73d4n8g0`). If not, run `mcp__render__select_workspace` for that ID before proceeding.

- [ ] **Step 2: List current services in the workspace to capture baseline**

Run via MCP: `mcp__render__list_services`
Expected: at minimum `bullet-progress` exists. Record service IDs of any `bullet-*-staging` already present (likely none).

- [ ] **Step 3: Trigger blueprint sync from the Render dashboard**

Render's MCP API does not expose a direct "sync blueprint" RPC. The reliable path is the Render UI: Dashboard -> Blueprints -> select the `bullet_digital_media` blueprint -> "Sync" / "Apply Changes". A push to `main` may auto-trigger this if the blueprint is set to "Auto Sync" - confirm in the UI.

Drive via Playwright MCP (`mcp__plugin_playwright_playwright__*`) following the S1-03 dashboard-flow pattern:

1. Navigate to `https://dashboard.render.com/`.
2. Confirm logged-in account is the same IzzyAgents account used for S1-03 (Playwright session reuse may already be in place under `.playwright-mcp/`).
3. Open the blueprint linked to `tsizzybots/bullet_digital_media`.
4. Click "Sync" and confirm.
5. Watch the "What will change" preview. Expected new services: `bullet-api-staging`, `bullet-worker-staging`, `bullet-dashboard-staging`, `bullet-cron-staging`. Expected env-group change: `bullet-staging-env` gains `PYTHON_VERSION=3.12` and `NODE_VERSION=20` (merged in alongside the existing `DATABASE_URL`); `bullet-prod-env` gains the same two non-secret keys.
6. Apply.

Expected: four new services begin building. The env group keeps the manually-set `DATABASE_URL` value intact; the two new non-secret keys are merged in.

- [ ] **Step 4: Wait for the first deploy of each service to finish**

For each new service, run via MCP: `mcp__render__list_deploys` (per service) until the most-recent deploy status is `live` (api/dashboard) or `running` (worker) or `succeeded`/no-active-state (cron).

Expected end state:
- `bullet-api-staging`: `live`
- `bullet-worker-staging`: `running` (background worker has no health check; "live" means the process started and did not exit)
- `bullet-dashboard-staging`: `live` (static, builds once)
- `bullet-cron-staging`: no active deploy - cron services only show a status after their first scheduled or manual run. Move to Task 5 for cron verification.

If any deploy fails:
- Pull logs via `mcp__render__list_logs` for the failed service.
- Common cause #1: `uv sync --frozen` failed -> means `uv.lock` is stale. Fix locally, commit, re-push, re-sync.
- Common cause #2: `rootDir: apps/api` path not found -> blueprint syntax issue. Validate with `cat render.yaml`.
- Common cause #3: env group not yet attached when build started -> retry the deploy from the Render UI once the env group binding shows green.

---

### Task 4: Verify `bullet-api-staging` serves `/healthz` over HTTPS

**Files:**
- No file changes.

- [ ] **Step 1: Confirm the service URL is HTTPS**

Run via MCP: `mcp__render__get_service` for `bullet-api-staging`. Read the `serviceDetails.url` field.
Expected: starts with `https://bullet-api-staging` and ends in `.onrender.com`.

- [ ] **Step 2: Hit `/healthz` with `curl`**

Run: `curl -sS -i https://bullet-api-staging.onrender.com/healthz`
Expected output (status line + body):

```
HTTP/2 200
content-type: application/json
...
{"status":"ok"}
```

If 502/503 returned within the first ~60s of deploy, wait 30s and retry - Render's edge can briefly route to a not-yet-ready instance. If still failing after 2 minutes, pull logs via `mcp__render__list_logs` and triage.

- [ ] **Step 3: Confirm Render's own health check shows green**

Render uses `healthCheckPath: /healthz` from `render.yaml` to mark the service live. In the dashboard (or via `get_service`), the "Health Check Status" should read "Healthy" / OK.

---

### Task 5: Verify the other three services

**Files:**
- No file changes.

- [ ] **Step 1: Confirm `bullet-worker-staging` is running**

Run via MCP: `mcp__render__list_logs` for `bullet-worker-staging`, filtered to the last ~5 minutes.
Expected: the placeholder marker line `bullet-worker-staging placeholder - replace in S1-19` appears once near the start, then no further output (the process is sleeping). Service status: `running`.

- [ ] **Step 2: Confirm `bullet-dashboard-staging` is reachable over HTTPS**

Run: `curl -sS -I https://bullet-dashboard-staging.onrender.com/`
Expected: `HTTP/2 200` with `content-type: text/html`. Optional sanity: `curl -sS https://bullet-dashboard-staging.onrender.com/ | grep -i placeholder` returns the placeholder text.

Note explicitly in the changelog: this service does **not** yet serve `/healthz` - that lands in S1-16 with the real Next.js build.

- [ ] **Step 3: Confirm `bullet-cron-staging` runs to completion when triggered manually**

In the Render dashboard, open `bullet-cron-staging` -> "Run Now". Wait for the run to finish.

Alternatively via MCP: trigger a run (Render MCP exposes `mcp__render__update_cron_job` but not a direct "run now" RPC; the dashboard "Run Now" button is the reliable path).

Expected: the run completes with exit code 0. The logs show `bullet-cron-staging placeholder - replace when first reconciliation job lands`. Status: `succeeded`.

---

### Task 6: Verify env-group scoping per service

**Files:**
- No file changes.

- [ ] **Step 1: Confirm `bullet-staging-env` shows all three expected keys**

In the dashboard, open Environment -> `bullet-staging-env`. Confirm:
- `DATABASE_URL` is present and masked (set manually in S1-03).
- `PYTHON_VERSION` = `3.12` (merged in by this blueprint sync).
- `NODE_VERSION` = `20` (merged in by this blueprint sync).

If `PYTHON_VERSION`/`NODE_VERSION` are missing, the blueprint sync did not merge them - re-sync. If `DATABASE_URL` value is empty or got blanked, restore the staging pooled Neon URL from `.env.neon` (`STAGING_DATABASE_URL_POOLED`) via the dashboard.

- [ ] **Step 2: Confirm each long-running service has the env group bound**

For `bullet-api-staging`, `bullet-worker-staging`, `bullet-cron-staging`: in the dashboard, open the service -> Environment tab -> confirm `bullet-staging-env` appears as a linked env group. `bullet-dashboard-staging` does not bind any env group (it is a static placeholder).

Equivalent via MCP: `mcp__render__get_service` and inspect the env-group bindings field if exposed; otherwise dashboard.

- [ ] **Step 3: Confirm `bullet-prod-env` was not accidentally touched**

Open `bullet-prod-env` in the dashboard. Confirm `DATABASE_URL` still holds the prod pooled URL (masked) and that the same two non-secret keys were merged in. No prod services should consume it yet - this is verification that the group is in place for later.

---

### Task 7: Verify zero-downtime restart on `bullet-api-staging`

**Files:**
- No file changes.

- [ ] **Step 1: Start a continuous probe**

In a separate terminal, run a loop hammering `/healthz`:

```bash
while true; do
  ts=$(date -u +%H:%M:%S)
  code=$(curl -s -o /dev/null -w "%{http_code}" https://bullet-api-staging.onrender.com/healthz)
  echo "$ts $code"
  sleep 1
done
```

Expected baseline: continuous `HH:MM:SS 200` lines, one per second.

- [ ] **Step 2: Trigger a manual restart**

In the Render dashboard, open `bullet-api-staging` -> "Manual Deploy" -> "Restart Service". Alternatively (zero-config-change restart) push an empty commit:

```bash
git commit --allow-empty -m "chore: trigger zero-downtime restart probe for S1-04 verification"
git push origin main
```

The empty-commit path is cleaner because it exercises the same build+deploy flow real changes will use. Restart-button is faster but skips the build step.

- [ ] **Step 3: Confirm zero (or near-zero) non-200s during the cutover**

Watch the probe output during the deploy. On Render's starter plan, the deploy flow is: new instance builds -> passes `healthCheckPath: /healthz` -> traffic cuts over. Expected: an unbroken stream of `200`s through the cutover.

Pass criteria: zero non-200 responses during the cutover, **or** at most one transient non-200 (Render edge occasionally returns a single 502 during cutover on starter plan; document the count in the changelog if seen).

If there is a sustained burst of 502/503s or the service goes down for more than ~5s, that is a fail - capture the timeline, the deploy ID, and any logs, and report. Likely cause: the new instance is failing the health check, forcing a roll-back.

- [ ] **Step 4: Confirm the post-restart service is healthy**

Same as Task 4 Step 2: `curl -sS https://bullet-api-staging.onrender.com/healthz` returns `{"status":"ok"}`.

---

### Task 8: Log to changelog and close the card

**Files:**
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Append S1-04 provisioning entry under `[Unreleased]`**

Append above the existing `### 18/05/2026 - S1-03: Neon Postgres provisioning (prod + staging)` block. Use UK date `18/05/2026` (or the actual completion date - update if execution runs on a different day).

Entry should cover:
- **Added**: four staging services live in IzzyAgents Render workspace - `bullet-api-staging` (live, `/healthz` 200), `bullet-worker-staging` (running, placeholder marker logged), `bullet-dashboard-staging` (live, static placeholder), `bullet-cron-staging` (manual run exit 0). Capture each service ID returned by `mcp__render__list_services` for the changelog audit trail.
- **Added**: `bullet-staging-env` env group now also holds `PYTHON_VERSION=3.12` and `NODE_VERSION=20` (merged in by first blueprint sync alongside the manually-set `DATABASE_URL`). Same for `bullet-prod-env`.
- **Verified**: zero-downtime restart on `bullet-api-staging` - manual empty-commit deploy, probe-during-cutover, N total non-200 responses observed (expected: 0).
- **Decision**: restate the 14/05/2026 narrowing - dashboard `/healthz` deferred to S1-16; worker and cron exempt by topology. The S1-04 acceptance test "each staging service serves `/healthz`" is satisfied in practice by api alone; the other three are placeholders today.
- **Discovery (if any)**: note any issue encountered (e.g. transient 502s during cutover, env-group merge quirks, build-time fixes needed).
- **Open follow-up**: re-flag the S1-03 `get_async_database_url()` issue - `DATABASE_URL` query-string params `?sslmode=require&channel_binding=require` will crash asyncpg the moment any DB-touching code (S1-12+) imports settings. This task did not touch DB code, so it did not bite us; S1-12 must fix it before the first DB connection.

- [ ] **Step 2: Verification table**

Append the standard verification table (matching the S1-03 and S1-01 entries' formatting) covering every step run, with the exact command, the actual result, and the timestamp.

- [ ] **Step 3: Commit the changelog**

Run:

```bash
git add docs/CHANGELOG.md docs/superpowers/plans/2026-05-18-s1-04-render-staging-provisioning.md
git commit -m "$(cat <<'EOF'
docs: log S1-04 Render staging provisioning + verification

Records the first multi-service Render blueprint sync into the
IzzyAgents workspace. Four staging services live; bullet-api-staging
serves /healthz over HTTPS; zero-downtime restart verified.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push the changelog commit**

Run: `git push origin main`

Expected: pushes cleanly. This push *will* trigger another auto-deploy on each service in the blueprint - that is fine (docs-only change rebuilds, but the build itself is idempotent and the service stays healthy). If you want to avoid the rebuild noise, push with `[skip render]` in the commit message (Render honours this convention), but the default path is to just let it rebuild - it is the same probe we already verified in Task 7.

- [ ] **Step 5: Move the StrikeFlow card to "To Review"**

This is a `/task-complete` workflow. Run the skill against card `ab449efe-fecf-424e-a3c5-cbf9db11c571`. The skill will move the card, attach a completion note linking to the changelog entry and verification table, and confirm the test contract is met.

---

## Self-Review

**Spec coverage:**

- "Create staging Render services: `bullet-api-staging` (FastAPI), `bullet-worker-staging` (Inngest worker), `bullet-cron-staging` (cron), `bullet-dashboard-staging` (Next.js)." -> Tasks 2-3 push the blueprint and sync. Worker and dashboard are placeholders by an earlier scope-narrowing decision (14/05/2026 changelog), restated above and re-logged in Task 8. **Covered.**
- "Wire env groups to Neon staging." -> Task 6 verifies env-group binding per service and confirms `bullet-staging-env` holds the staging Neon `DATABASE_URL` plus the two non-secret keys. **Covered.**
- "Confirm zero-downtime restart works." -> Task 7. **Covered.**
- Test: "Each staging service serves a `/healthz` endpoint returning `{"status":"ok"}` over HTTPS." -> Task 4 covers api fully. Task 5 documents the scope narrowing for the other three. **Covered with explicit scope note.**
- Test: "Render env vars are scoped per service via env groups." -> Task 6. **Covered.**
- Dependencies S1-01 and S1-03 -> already satisfied (S1-01 ships scaffold/CI; S1-03 ships Neon + env groups).

**Placeholder scan:** No `TBD`, `TODO`, `fill in details`, `appropriate error handling` etc. found. Every step has a specific command, an exact expected output, and a stated failure-mode path.

**Type consistency:** No code types defined - this is an ops task. Service names (`bullet-api-staging` etc.) are consistent across all tasks and match `render.yaml`. Env-group names consistent. Service IDs are runtime-discovered and captured for the changelog.

---

## Open Questions for the User Before Execution

1. **Are you happy with the explicit scope narrowing in Task 5?** The original task wording says all four services should serve `/healthz`. The committed narrowing (14/05/2026) accepts that worker, cron, and the dashboard placeholder do not. If you want the dashboard placeholder to *literally* serve `/healthz` over HTTPS today, that needs a one-line `render.yaml` change (e.g. add a `.placeholder/healthz` file with `{"status":"ok"}` and an appropriate content-type rule) - small but worth confirming before I do it.
2. **Is the IzzyAgents Render workspace still the right home for staging?** S1-03 confirmed yes. Re-confirm before this first multi-service sync, since it is much cheaper to recreate four services now than after S1-12 wires real code into them.
3. **Empty-commit deploy vs Restart-button for the zero-downtime probe (Task 7 Step 2)?** I have defaulted to "empty commit" because it tests the build pipeline too. Happy to switch to Restart-button if you want a faster pass.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-18-s1-04-render-staging-provisioning.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration. Best fit here because Tasks 3/4/5/6/7 each involve a different Render surface (blueprint sync, HTTPS probe, log inspection, env-group dashboard, cutover probe) and benefit from a clean context per task.

**2. Inline Execution** - I run tasks myself in this session using superpowers:executing-plans, with checkpoints for review after Task 2 (push), Task 3 (blueprint sync), and Task 7 (zero-downtime cutover).

Which approach?
