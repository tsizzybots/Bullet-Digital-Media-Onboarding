---
name: pre-pr-review
description: |
  Deep, adversarial self-review of the current ticket's diff BEFORE it goes
  to To Review / a PR. Encodes the hardening lenses our human reviewer has
  repeatedly caught us on (concurrency races in shared helpers, transport-level
  failure handling, idempotency, failure-path test coverage) plus project-future
  awareness. Find issues, FIX them, re-verify, and only then raise the PR.
triggers:
  - pre-pr-review
  - pre pr review
  - self review
  - harden review
  - review before pr
  - deep review
user_invocable: true
---

# /pre-pr-review

> The mandatory hardening gate. Run this on the current diff BEFORE moving a card to To Review or opening a PR. Every finding is FIXED (or consciously deferred with a logged reason), then the full test + lint + typecheck suite is re-run. The goal: the human reviewer finds nothing we could have found ourselves.

## Why this exists

The bugs that slip past a green test suite are almost always the ones *outside* the modelled paths: the happy path and typed errors get covered, while concurrency races and transport-level failures do not. This gate makes those lenses a required pass before a PR goes out, so the diff reaches review already stress-tested for the failure and concurrency cases.

## When to run

- BEFORE `cards_move` to **To Review**.
- BEFORE pushing a branch / opening a PR.
- After any review-fix commit, before re-requesting review.

## Process

1. **Get the diff.** `git diff main...HEAD` (or the working tree if uncommitted). Read every changed file in full, not just the hunks - context matters for the lenses below.
2. **Run the built-in `/code-review`** for an independent correctness pass, then layer THIS skill's project-specific lenses on top. (`/code-review high` for substantial diffs.)
3. **Apply every lens** in the checklist below. For each, write a concrete finding as `file:line - issue - fix`. Be adversarial: actively try to break the change, don't confirm it works.
4. **FIX every finding.** If something is genuinely out of scope, defer it explicitly: log it in `docs/CHANGELOG.md` (or the card) with the reason and the ticket that owns it - never silently skip.
5. **Add the missing tests** the lenses surface (see the Test Matrix). A finding without a regression test is not closed.
6. **Re-verify**: `cd apps/api && uv run pytest . -q` (full suite), `uv run ruff check . && uv run ruff format --check .`, `make typecheck`. All green.
7. **Only now** move the card to To Review / open the PR.

## Review lenses (the hardening checklist)

### 1. Shared components vs ALL callers + concurrency
- Does this change touch a **reused** seam (`worker/platform_actions.py`, `storage/`, `ghl/`, `pandadoc/`, `worker/events.py`, `db/`)? If so, judge it against EVERY future caller and **concurrent** invocation - NOT just the first caller.
- A caller-specific protection (e.g. S1-25's per-client `Concurrency(limit=1)`) does NOT protect other callers. The shared code must be correct on its own.
- **Concurrency races under READ COMMITTED**: `INSERT ... ON CONFLICT DO NOTHING` + a separate `SELECT` fallback can read back NOTHING when a concurrent inserter hasn't committed yet. Use `ON CONFLICT DO UPDATE SET col = EXCLUDED.col RETURNING ...` (no-op update) so RETURNING always yields the surviving row in one round-trip and the conflict path locks until the other tx commits. (This was the S1-25 begin_action fix.)

### 2. Failure paths & transport-level errors
- Do `except` clauses catch **only typed/modelled errors**? Transport-level failures (`httpx.ReadTimeout`, `ConnectError`, connection reset) happen BEFORE any HTTP response, so they are NOT your typed `GhlError`/`PandaDocNotFound` - they slip past a narrow `except`. Catch broadly enough to **record the failure**, then re-raise unchanged so the wrapper still classifies retriable-vs-not. (This was the S1-25 `except Exception` fix.)
- **Every external call records its outcome.** Never leave a `platform_actions` row stuck `in_progress` on an unexpected error - that is a silent zombie. `fail_action` must run on ALL failure paths.
- Classify **retriable vs non-retriable** correctly and verify the classification holds for transport errors (timeout -> retry; 4xx/empty-config -> NonRetriable dead-letter).

### 3. Idempotency / replay / at-least-once
- Inngest retries and events can be delivered more than once. Is the handler idempotent? Deterministic keys, `ON CONFLICT`/guarded `WHERE NOT EXISTS` inserts, short-circuit on already-succeeded, deterministic R2 keys (overwrite, no dup object).
- Identify the **at-least-once window** (any non-atomic "call external system, then commit to DB"). On a crash in the gap, is the worst case at least *visible* (recorded failed / in_progress) rather than silent? Is duplicate creation prevented or deferred to a named ticket?

### 4. DB correctness
- **Commit ordering** matches the retry source (commit-before-emit for Inngest-driven; commit `in_progress` before the external call so partial failures are visible).
- JSONB written via `cast(:p AS jsonb)` + `json.dumps(...)`; enum values written by name from `db/enums.py` (add the constant if missing); NOT NULL columns handled; FK targets exist; **no migration when the columns already exist** (check the schema first).
- Connection hygiene: don't hold a pooled connection across a slow external call when volume could exhaust the pool (the S1-25a fetch-outside-session rule).

### 5. Project-future awareness
- This is a multi-fan-out **orchestration platform**: one `client.created` fans out to many independent, retryable, individually-audited jobs. Shared seams get reused by every later fan-out (Asana, Stripe, Xero, Timely, transcripts).
- Review for that reuse: clean Protocol + production impl + Fake test double; no caller-specific assumptions baked into shared code; the audit trail (`platform_actions`) stays consistent across fan-outs.
- "Every fan-out action is idempotent, retryable, and individually auditable; partial failures must be visible in the dashboard, never silent; every job writes its outcome back to Postgres" (CLAUDE.md constraints) - verify the change upholds all of these.

### 6. Test matrix (every change needs the relevant rows)
- **Success** - happy path with DB writes asserted (rows created, columns written back, payload persisted).
- **Each failure mode** - INCLUDING a transport-level error (e.g. `httpx.ReadTimeout`), not only typed errors. Assert the action flips to `failed` + `last_error` + `retry_count` bump, and no orphan/partial rows.
- **Replay / idempotency** - same event twice -> no duplicate row, no duplicate external call, no duplicate object.
- **Concurrency / config declaration** - assert the Inngest concurrency caps and trigger event via `fn.get_config("").main`.
- **Shared helpers** - a concurrent-duplicate scenario (two callers, same idempotency key) where applicable.
- **Pure helpers** - malformed / None / empty / wrong-type input returns safely rather than raising.

### 7. Standards & hygiene
- `ruff check` + `ruff format --check` clean; `make typecheck` green.
- Imports at module top (never deferred into function bodies). No em dashes; UK dates (DD/MM/YYYY); 24-hour times; currency correctness (GBP vs USD).
- `docs/CHANGELOG.md` updated (Added / Changed / Decision / Discovery / Fixed) AND the active-state memory updated.
- Secrets never committed; throwaway/smoke scripts kept LOCAL (untracked), not in the PR.
- No silent caps/truncation - if coverage is bounded, say so.

## Output

A short report: findings (each `file:line - issue - fix - test added`), what was fixed, what was consciously deferred (with the owning ticket + reason), and the final green test/lint/typecheck result. Then - and only then - proceed to To Review / PR.

## Scaling the review

- Small diff (one handler) → run the lenses inline yourself.
- Large or cross-cutting diff → spawn parallel review agents (Agent tool), one per lens cluster (concurrency+idempotency / failure-paths / tests+standards), then synthesise and fix. Use a Workflow only if the user has opted into multi-agent orchestration.
