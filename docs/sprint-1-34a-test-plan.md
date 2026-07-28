# S1-34a - Sprint 1 Live Integration Test Plan

**Ticket:** S1-34a (Sprint 1 close-out - live integration verification + production config)
**Status:** In Progress
**Author:** Claude (IzzyAgents)
**Created:** 21/07/2026 · **Updated:** 22/07/2026
**Companion document:** `docs/sprint-1-34a-test-results.md` (execution log + evidence)

---

## 1. Purpose

S1-34a is the single close-out gate that runs BEFORE S1-35 (Sprint 1 acceptance). Most Sprint 1 fan-out tickets (S1-25 GHL, S1-25b R2, S1-25c PandaDoc dual-account, S1-26 returning-client, S1-29 summary, S1-30 embeddings) shipped with their external contracts verified only against unit tests and OpenAPI specs, using faked provider seams. This plan verifies each of those contracts against the **real** provider APIs on the live staging stack, and collects every "before production" configuration item into one checklist.

Nothing here is a new feature. It is verification: prove that a diff already stress-tested against fakes behaves correctly against the real PandaDoc, GoHighLevel, R2, Google, Anthropic and OpenAI APIs.

## 2. Environment

- **Target:** Render **staging** (client-owned workspace `Agents's workspace`).
- **Services:** `bullet-api-staging` (FastAPI receiver + admin), `bullet-worker-staging` (Inngest workers), `bullet-cron-staging` (reconciliation), `bullet-dashboard-staging-web` (Next.js).
- **Data:** Neon Postgres at migration head `0012`.
- **PandaDoc:** SANDBOX accounts (UK + INT). Live/production keys are a separate, later step.
- **Provider accounts:** Anthropic + OpenAI are IzzyAgents-owned; GHL agency, PandaDoc, R2/Cloudflare, Google Cloud are Bullet-owned.

## 3. The two chains under test

**Chain 1 - Signing to onboarding fan-out** (PandaDoc + GHL + R2)

```
PandaDoc "document.completed" webhook
  -> POST /webhooks/pandadoc     (HMAC verify per account; fail-closed 401 if neither secret verifies)
  -> onboarding_events row; emit Inngest  pandadoc.signed
  -> create_client_record worker -> clients row; emit  client.created
  -> FAN-OUT:  create_ghl_subaccount   +   store_signed_pdf (downloads PDF from PandaDoc, puts to R2)
  -> each writes a platform_actions row (success / external_id / retry_count)
```

**Chain 2 - Sales call to knowledge** (Google Meet + Anthropic + OpenAI + R2)

```
Google Meet transcript ready -> Pub/Sub push
  -> capture_meet_transcript worker -> store transcript (R2); auto-link by calendar-invite email
  -> emit  transcript.linked
  -> summarise-sales-call worker (Anthropic messages.parse, structured output) -> emit  sales_summary.ready
  -> store_sales_knowledge worker (reads transcript from R2; OpenAI embeddings) -> 7 client_knowledge rows (1536-dim vectors)
```

## 4. How tests are triggered

There are three deterministic triggers - tests are not fired by chance.

| Method | What it does | Best for |
|---|---|---|
| **Real sandbox sign** | Complete a document in the PandaDoc sandbox from the real onboarding template -> live webhook fires the whole chain | The one true end-to-end proof (needs the webhook subscription live) |
| **Replay endpoint** `POST /admin/pandadoc/replay/{document_id}` | Fetches a real sandbox document from PandaDoc's API and drives the identical chain, on demand, repeatably | The workhorse - real data, deterministic, re-runnable idempotency + fan-out testing |
| **Focused seam smoke** | A standalone script exercising one live client seam (GHL create, Anthropic summarise, OpenAI embed) with plain inputs | Verifying a single external contract without R2/DB/Inngest - the "unblocked now" scope |

## 5. Verification surfaces

Every test is verified through up to three lenses:

1. **Dashboard** - `/clients`, `/clients/[id]` (platform ids, deep-link grid, last 20 `platform_actions` with Inngest run links), `/transcripts`.
2. **Inngest Cloud** - per-function run logs (success / retry / dead-letter).
3. **`platform_actions` table** - each fan-out action's `success`/`failure`, `external_id`, `retry_count`, `last_error`.

## 6. Prerequisites and gates

| Gate | Needed for | Owner | Status |
|---|---|---|---|
| PandaDoc webhook shared keys (UK + INT) on Render | Chain 1 webhook verify | Bullet -> IzzyAgents | DONE (loaded 21/07) |
| PandaDoc account access + 1 real sandbox document | Chain 1 live + replay | Bullet | Requested |
| GHL agency key + company id on Render | GHL fan-out | Bullet -> IzzyAgents | DONE (loaded) |
| GHL agency UI access | Verify + clean up sub-account smoke | Bullet | Granted (22/07) |
| R2 (`R2_*`) - Cloudflare billing enabled, then creds | signed-PDF + full end-to-end | Bullet (billing) -> IzzyAgents | Cloudflare invite sent |
| Google Cloud org access + domain-wide delegation | Meet transcript capture | Bullet | Requested |
| OpenAI account funded | Embeddings | IzzyAgents | DONE (funded 22/07; embeddings verified) |
| Anthropic key on Render | Summary | IzzyAgents | DONE |
| `ANTHROPIC_MODEL` valid + supports structured outputs | Summary | IzzyAgents | DONE (`claude-opus-4-7` verified) |

## 7. Test cases

### Step 0 - Pre-flight
- **Objective:** every staging service boots with all env resolved; Inngest functions register.
- **Run:** inspect Render boot logs for `bullet-api-staging` + `bullet-worker-staging`.
- **Expected:** `Application startup complete`; NO `MISSING_ENV`, NO `SigningKeyMissingError`, NO Inngest `registration_failed`; `GET /healthz` -> 200.

### Step 1 - GHL contract (isolated, no client data)
- **1a (read-only):** run `scripts/ghl_get_company_id.py`. **Expected:** agency key authenticates; `GET /locations/search` returns `{"locations": [...]}` matching `find_location_by_email`'s parse (clears the CONFIRM-PRE-PROD marker in `ghl/client.py`).
- **1b (create/delete):** run `scripts/smoke_ghl_subaccount.py` (guarded by `GHL_SMOKE_CONFIRM=yes`). **Expected:** `POST /locations/` -> 200 with a location object whose fields map to the `GhlLocation` projection; the throwaway is then deleted cleanly.
- **Verification:** create + delete both HTTP 2xx; no throwaway left in the agency.

### Step 2 - Chain 1 end-to-end (signing -> fan-out)
- **Preconditions:** PandaDoc webhook subscription live; R2 configured.
- **Run:** complete a sandbox document from the real template (throwaway "Test Gym Ltd", unique email).
- **Expected:** webhook verifies + routes to the correct account; `pandadoc.signed` -> `client.created` -> GHL sub-account created + signed PDF in R2; every `platform_actions` row `success` with a real `external_id`.
- **Verification:** dashboard shows the client + healthy actions; Inngest runs green; `platform_actions` all success.

### Step 3 - Idempotency / replay
- **Run:** `POST /admin/pandadoc/replay/{document_id}` on the same document.
- **Expected:** NO duplicate client, GHL location, or PDF; the `(event_type, external_id)` unique constraint dedupes; the flow returns cleanly.

### Step 4 - Chain 2 (sales call -> summary -> knowledge)
- **4a (front half, needs GCP):** hold a real Google Meet with a transcript; invite email matches the test client. **Expected:** transcript captured to R2, auto-linked, `transcript.linked` emitted.
- **4b (back half, runnable without Meet):** run `scripts/smoke_summary_embeddings.py` with the Anthropic + OpenAI keys. **Expected:** `summarise()` returns a validated `SalesCallSummary` (7 §7.1 fields); `render_knowledge_fields()` -> 7 rows; `embed()` returns one 1536-dim vector per non-empty value_text.

### Step 5 - Failure + reconciliation paths (do not skip)
- Force a failure (e.g. temporarily bad GHL key) -> `platform_actions` records it with `last_error`; dashboard shows UNHEALTHY (not silently missing); manual retry recovers.
- Transport-level failure (timeout/reset) leaves NO zombie `in_progress` action.
- PandaDoc reconciliation cron back-fills a deliberately-missed webhook; over-fetch is deduped (no duplicates).

## 8. Exit criteria

- All env groups populated and staging green; no `MISSING_ENV`.
- All live smokes (Steps 1-4) pass with real creds; any contract tweaks merged to main.
- Idempotency proven (Step 3); failure path visible + recoverable; reconciliation self-heals (Step 5).
- Code-hardening items (S1-26 riders) confirmed landed.
- All Bullet-side confirmations recorded in `docs/CHANGELOG.md`.
- THEN, and only then, start S1-35 (acceptance verification).

## 9. What can run BEFORE R2 / GCP / PandaDoc access

- Step 0 pre-flight.
- Step 1 (GHL smokes) - creds already on Render.
- Step 4b (summary + embeddings seam smoke) - once the OpenAI account is funded.

Everything else waits on the client-side gates in section 6.
