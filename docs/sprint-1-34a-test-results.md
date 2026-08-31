# S1-34a - Live Integration Test Results

**Ticket:** S1-34a · **Environment:** Render staging (client-owned workspace)
**Author:** Claude (IzzyAgents) · **Started:** 21/07/2026 · **Updated:** 22/07/2026
**Companion document:** `docs/sprint-1-34a-test-plan.md`

> This is the execution log for the plan. Each executed test lists what was run, the actual result, and any finding/fix. Pending tests list their blocker and owner. Section 6 flags recommended retest areas for lead review.

---

## 1. Scorecard

| # | Test | Result | Notes |
|---|------|--------|-------|
| Config | PandaDoc webhook keys (UK + INT) loaded + API redeployed | PASS | Live; fail-closed 401 verified |
| Step 0 | Pre-flight (boot / env / Inngest registration) | PASS | Clean boot on `a20194c` |
| Step 1a | GHL read-only probe (auth + search contract) | PASS | Contract confirmed, no code change |
| Step 1b | GHL create/delete smoke | PASS | Create contract confirmed; delete-param bug caught + fixed (script only) |
| Step 4b (i) | Anthropic summariser (live) | PASS | `claude-opus-4-7` structured outputs confirmed |
| Step 4b (ii) | OpenAI embeddings (live) | PASS (22/07) | Account funded; re-ran -> 7 vectors, all 1536-dim |
| Step 2 | Chain 1 end-to-end (signing) | PENDING | Needs PandaDoc access + 1 real doc + R2 |
| Step 3 | Idempotency / replay | PENDING | Needs a real document to replay |
| Step 4a | Meet transcript capture (front half) | PENDING | Needs GCP org access + DWD |
| Step 5 | Failure + reconciliation paths | PENDING | Run alongside Chain 1 |

**Headline:** every test that can run without R2 / GCP / PandaDoc-access is now GREEN (the OpenAI account-balance gate cleared on 22/07 once Bullet funded it). The code is proving out - one real contract bug was found and fixed, and no shipped-code defects surfaced. What remains is gated purely on client-side access (PandaDoc, R2, GCP).

---

## 2. Executed tests - detail + evidence

### Config - PandaDoc webhook shared keys loaded (21/07/2026)
- Loaded `PANDADOC_WEBHOOK_SECRET_UK` + `PANDADOC_WEBHOOK_SECRET_INT` onto `bullet-staging-env` via the Render REST API (single-var `PUT`, HTTP 200 each; other 18 vars untouched; group now 20 vars). Field names per `config.py:186-200`; note the International suffix is `_INT`, not `_INTL`.
- Redeployed `bullet-api-staging` (deploy `<redacted>`, commit `a20194c`) -> live.
- **Verified:** `GET /healthz` -> 200 (~0.42s); `POST /webhooks/pandadoc` with no signature -> **401** (fail-closed path active, receiver loaded).
- **Residual:** a genuine signed-HMAC verify still needs a real webhook from Bullet's sandbox (see Step 2).

### Step 0 - Pre-flight (21/07/2026) - PASS
- Render boot logs for `bullet-api-staging` on `a20194c`: `Application startup complete`, uvicorn up.
- No `MISSING_ENV`, no `SigningKeyMissingError`, no Inngest `registration_failed` - confirms the S1-26a all-or-nothing registration fix holds on the live deploy.
- `bullet-worker-staging` live on `a2a7e3c` (the two later commits are dashboard-only; no backend drift).
- Env groups confirmed owned by the client workspace (`<redacted, review round 4 finding 2>`) - the "migrate env groups" checklist item was already done.

### Step 1a - GHL read-only probe (21/07/2026) - PASS
- Ran `scripts/ghl_get_company_id.py` against live LeadConnector (agency key + company id `<redacted, real GHL agency company id - review round 4 finding 2>`).
- `GET /locations/search` -> **HTTP 200**, body `{"locations": [], "traceId": ...}`.
- **Result:** the S1-26 returning-client lookup envelope `{"locations": [...]}` matches `find_location_by_email`'s parse. The `CONFIRM PRE-PROD` marker on `ghl/client.py:169` is cleared; no envelope change needed. Agency key + `Version: 2021-07-28` header authenticate. Read-only; nothing created.

### Step 1b - GHL create/delete smoke (21/07/2026) - PASS
- Ran `scripts/smoke_ghl_subaccount.py` against the live agency.
- **Create:** `POST /locations/` with `{name, companyId}` -> **HTTP 200**. Response is a full location object; `raw` keys: `brandId, business, companyId, country, currency, dateAdded, defaultEmailService, domain, email, id, isAgencySubAccount, logoUrl, name, permissions, settings, snapshotId, social, timezone, traceId, website`. The `create_location -> GhlLocation` projection (`id`/`name`/`companyId`) maps correctly, so the S1-25 assumptions hold. Because the object carries `id`/`name`/`companyId`, this also confirms the search-hit field names for `find_location_by_email`.
- **Finding + fix (see 3.1):** the first cleanup DELETE 422'd and left a throwaway location; it was deleted by hand immediately, the script's delete params were corrected, and a re-run did a clean create+delete.
- **Cleanup:** two throwaway locations created across the two runs (ids `<redacted, live GHL location ids - review round 5>`); BOTH confirmed deleted (delete -> 200 "Deleted location"; follow-up GET -> 403). No leak.

### Step 4b - Summary + embeddings seam smoke (21/07 + 22/07/2026) - ALL PASS
Ran `scripts/smoke_summary_embeddings.py` (exercises the two live client seams; skips R2/DB/Inngest by design - the production summariser reads the transcript from R2, but the live-API risk lives entirely in the seams, which take/return plain objects).

**(i) Anthropic summariser - PASS.** `claude-opus-4-7` **supports structured outputs** (`messages.parse(output_format=SalesCallSummary)`) with `anthropic==0.109.2` - a real prior unknown, now cleared; no need to bump to `claude-opus-4-8`. Raw response:
```
id           : msg_011CdF89cYC5deepQYhienCr
model        : claude-opus-4-7
stop_reason  : end_turn
usage        : input_tokens=311, output_tokens=417, cache_read_input_tokens=2531,
               inference_geo='global', service_tier='standard'
```
The `SalesCallSummary` validated with all 7 PRD §7.1 fields populated correctly (business_type, business_goals, budget, pain_points, red_flags, next_steps, 2 notable_quotes). `render_knowledge_fields()` produced all 7 rows, non-empty and embed-ready. The `cache_read_input_tokens=2531` confirms the prompt-cache prefix works.

**(ii) OpenAI embeddings - PASS (22/07/2026, after Bullet funded the account).** On the first run (21/07) this returned `429 insufficient_quota` - an account-balance gate, not a code or key problem (the key authenticated; a bad key would 401; there were no `x-ratelimit-*`/`retry-after` headers, the tell for a billing issue vs a throughput throttle). Bullet funded the OpenAI account, the smoke was re-run, and it passed: `embed()` returned **7 vectors for 7 inputs, all dimension 1536** (matches the `client_knowledge.embedding vector(1536)` column). The `text-embedding-3-small` integration is now verified live end to end.

---

## 3. Findings and fixes log

### 3.1 GHL delete-location contract asymmetry (fixed - smoke script only)
The GHL delete endpoint is `DELETE /locations/{id}?deleteTwilioAccount=true` and **rejects** a `companyId` query param (`422 property companyId should not exist`), whereas create/search **require** `companyId`. The smoke script's cleanup used the wrong params, 422'd, and left a throwaway location (deleted by hand immediately). **No shipped code is affected** - the production GHL client (`create_location`, `find_location_by_email`) never deletes; only the smoke helper was wrong. Script fixed (`params={"deleteTwilioAccount": "true"}` + a comment documenting the asymmetry) and re-verified. Documented for any future ticket that adds delete/rollback.

### 3.2 `claude-opus-4-7` supports structured outputs (confirmed)
Prior uncertainty (some model-support lists name 4.8/Sonnet-5/Haiku but not 4.7) is resolved: `messages.parse` with `output_format` works on `claude-opus-4-7` with the installed SDK. No model bump required.

### 3.3 Budget currency is GBP, not USD (confirmed from live data)
The live summary returned `budget_range_usd` with `currency='GBP'`. `BudgetRange.currency` captures the real currency correctly; only the field NAME is misleading. Confirms the existing open follow-up (`budget_range_usd` / `clients.monthly_fee_usd` schema rename + a dedicated currency column). Not blocking; tracked separately.

### 3.4 OpenAI account had no quota (RESOLVED 22/07)
The first embeddings run hit `429 insufficient_quota` - an account-balance gate, not a code or key problem. Bullet funded the OpenAI account and the re-run passed (7 vectors, 1536-dim). No code change was needed.

---

## 4. Pending tests - blockers and owners

| Test | Blocker | Owner |
|---|---|---|
| Step 2 Chain 1 end-to-end | PandaDoc account access + 1 real sandbox document + R2 configured | Bullet |
| Step 3 replay / idempotency | A real document to replay (depends on Step 2) | Bullet |
| Step 4a Meet transcript capture | Google Cloud org access + domain-wide delegation | Bullet |
| signed-PDF R2 put (part of Step 2) | R2 - Cloudflare billing then `R2_*` creds | Bullet (billing) |
| Step 5 failure + reconciliation | Run alongside Chain 1 once R2/PandaDoc are live | IzzyAgents |

## 5. Configuration snapshot (staging, as of 22/07/2026)

**Loaded on `bullet-staging-env`:** DATABASE_URL, INNGEST_SIGNING_KEY + INNGEST_EVENT_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, GHL_AGENCY_API_KEY, GHL_COMPANY_ID, PANDADOC_API_KEY_UK/_INT + base URL, **PANDADOC_WEBHOOK_SECRET_UK/_INT** (new), RESEND_API_KEY + EMAIL_FROM, SENTRY_ENVIRONMENT, EMAIL_TOKEN_SECRET + EMAIL_CONFIRMATION_BASE_URL, GOOGLE_SERVICE_ACCOUNT_JSON. Service-level: CORS_ALLOW_ORIGINS + SENTRY_DSN on the API.

**Not yet set (AS AT 22/07/2026 - SUPERSEDED, see below):** `R2_*` (4 vars), `GOOGLE_WORKSPACE_IMPERSONATE_SUBJECT`, `GOOGLE_PUBSUB_PUSH_AUDIENCE`, `GOOGLE_PUBSUB_PUSH_SA_EMAIL`, `GHL_SNAPSHOT_ID` (optional).

> **STATUS UPDATE - this document is a point-in-time record, and the list above is now stale.** Every variable named there was subsequently loaded: the four `R2_*` vars and `GHL_SNAPSHOT_ID` on 22/07, and the three Google vars with the Pub/Sub setup later the same day ("All Render credential/config loads are now complete", CHANGELOG 22/07). Staging's PandaDoc keys were also switched from SANDBOX to **PRODUCTION** on 28/07, so the "PandaDoc: SANDBOX accounts" premise in the test plan no longer holds either. Read the CHANGELOG entries from 22/07 onward for the current position; the only outstanding GCP item is the domain-wide-delegation authorisation in Bullet's Google Admin (client id redacted, scopes `meetings.space.readonly` + `calendar.readonly`).

**Note:** the DB migration step is manual (no `preDeployCommand` in `render.yaml`) - fine while the DB is at head `0012`, but a future migration-bearing deploy needs `alembic upgrade head` run by hand until that follow-up lands.

## 6. Recommended retest areas (for lead review)

1. **Anthropic summariser** - re-run once more when Chain 2 goes fully live via R2 (real transcript from R2 rather than an inline sample) to confirm behaviour on real Meet transcript formatting, not just the synthetic sample.
2. **OpenAI embeddings** - re-run the seam smoke the moment the account is funded; then again inside the full Chain 2 (7 rows written to `client_knowledge`, dashboard reflects them).
3. **GHL create** - when GHL agency UI access lands, re-run 1b while watching the agency, and verify the returning-client lookup against a REAL existing location (Step 1a only proved the empty-result envelope).
4. **Model choice** - decide whether to keep `claude-opus-4-7` or move to `claude-opus-4-8` before acceptance (no code change; `4.7` is verified working).
5. **Currency** - decide whether the `budget_range_usd` -> currency-aware rename is in-scope for Sprint 1 acceptance or deferred.
