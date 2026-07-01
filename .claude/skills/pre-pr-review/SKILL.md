---
name: pre-pr-review
description: |
  Deep, adversarial self-review of the current ticket's diff BEFORE it goes
  to To Review / a PR. Encodes the hardening lenses our human reviewer has
  repeatedly caught us on (concurrency races in shared helpers, transport-level
  failure handling, idempotency, failure-path test coverage, rename completeness
  in operator-facing strings, degenerate-config fail-closed tests) plus
  project-future awareness. Find issues, FIX them, re-verify, and only then raise the PR.
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

**S1-25c lesson (PR #5):** even a green suite AND a prior pass of this gate still shipped findings to the human reviewer - because the lenses were applied to the *logic* but not to (a) the operator-facing **strings** a rename touched (three `RuntimeError` messages + docs still named the deleted `PANDADOC_API_KEY` env var, pointing an operator at a variable that no longer exists), (b) the **degenerate config** permutations (only one of N accounts configured -> must fail closed, not open), and (c) a test that was **deferred too cheaply** ("no precedent for invoking the Inngest wrapper" - the precedent was `._handler`, the logic was real account->key branching). Those three are now first-class lenses below. The rule: a rename is not done until every *string* is updated, multi-config is not done until the one-and-none permutations are tested, and a deferral is only valid when the path is genuinely redundant - never because the harness is unfamiliar.

**S1-27a lesson (PR #9):** the first dashboard-heavy diff to hit review shipped three findings the Python-shaped lenses above could never catch, because they live in the **React/TypeScript client**, not the worker: (a) a **telemetry hole** - the attach mutation's `catch {}` bound nothing and normalised every failure to a generic `AttachError(0)`, so a real transport failure or a genuine JS bug on the app's only write path was invisible to Sentry (the backend rule "every external call records its outcome" has a frontend twin: every caught client-side error is `Sentry.captureException`'d and preserved as `cause` before it is normalised for display); (b) a **type-assertion that masks bugs** - `(attach.error as Error).message` re-asserted a type TanStack Query v5 already gives you, so a wrong error type would compile silently instead of surfacing (a cast is a hole in the type system - drop it if the type is already correct, and never reach for `as any` / `!` to quiet the compiler on a genuine mismatch); (c) a **polled-state desync** - `clientId` was held in local state and the Attach button gated on the raw id, so when the 10s clients poll dropped the picked client the button stayed enabled and fired a doomed 400; the fix gates the action on the *resolved* entity (`clients.find(id) ?? null`), not the stale id. These are now lens #9. The rule: the dashboard is a first-class surface - a mutation, a cast, and a piece of poll-refreshed state each get the same adversarial pass a worker does.

## When to run

- BEFORE `cards_move` to **To Review**.
- BEFORE pushing a branch / opening a PR.
- After any review-fix commit, before re-requesting review.

## Process

1. **Get the diff.** `git diff main...HEAD` (or the working tree if uncommitted). Read every changed file in full, not just the hunks - context matters for the lenses below.
2. **Run the built-in `/code-review`** for an independent correctness pass, then layer THIS skill's project-specific lenses on top. (`/code-review high` for substantial diffs.)
3. **Apply every lens** in the checklist below. For each, write a concrete finding as `file:line - issue - fix`. Be adversarial: actively try to break the change, don't confirm it works.
4. **FIX every finding.** If something is genuinely out of scope, defer it explicitly: log it in `docs/CHANGELOG.md` (or the card) with the reason and the ticket that owns it - never silently skip.
5. **Add the missing tests** the lenses surface (see the Test Matrix). A finding without a regression test is not closed. **The deferral bar is high:** a test may be deferred ONLY when the path is genuinely redundant with existing coverage or owned by a named later ticket - NOT because the invocation harness is unfamiliar. New conditional/branching logic in a changed unit (e.g. an account->key selection inside an Inngest wrapper) MUST be tested directly; find the invocation mechanism (`fn._handler`, `fn.get_config("").main`) rather than deferring. (S1-25c deferred exactly this and the reviewer bounced it - the precedent existed.)
6. **Re-verify**: `cd apps/api && uv run pytest . -q` (full suite), `uv run ruff check . && uv run ruff format --check .`, `make typecheck`. All green. **If the diff touches `apps/dashboard/`, also** `pnpm --filter dashboard build` (a green typecheck alone does not catch a broken build) plus any dashboard test runner that exists (Playwright e2e / vitest once S1-36 lands).
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

### 6. Rename / redefinition completeness
- When the diff **renames or removes** a config key, env var, function, constant, or column, grep the WHOLE repo for the old name and confirm EVERY occurrence is updated - not just the code references. The misses that reach review live in **strings**: `RuntimeError`/exception messages, log lines, docstrings, code comments, `render.yaml` / infra NOTE blocks, `config.py` section headers, `.env.example`. (S1-25c: the `PANDADOC_API_KEY` -> `_UK`/`_INT` rename left three live error messages naming the deleted var, so an operator following the error would set a variable that no longer exists.)
- **Operator-facing rule:** any message a human reads on failure must name a thing that currently EXISTS and is actionable. After a rename, re-read every error/log string the diff touches and ask "if an operator does exactly what this says, does it work?"
- A value union (e.g. `Literal["uk","int"]`) used at more than one boundary gets ONE shared alias/constant (`pandadoc/accounts.py`), not re-spelled per call site - so the next value added changes one place.

### 7. Test matrix (every change needs the relevant rows)
- **Success** - happy path with DB writes asserted (rows created, columns written back, payload persisted).
- **Each failure mode** - INCLUDING a transport-level error (e.g. `httpx.ReadTimeout`), not only typed errors. Assert the action flips to `failed` + `last_error` + `retry_count` bump, and no orphan/partial rows.
- **Replay / idempotency** - same event twice -> no duplicate row, no duplicate external call, no duplicate object. When the diff adds a **new non-key column** (descriptive metadata, not part of the idempotency key), add a replay with a DIFFERENT value for that column and assert it still dedupes to one row AND keeps the first writer's value - proving the column is metadata, not identity. (S1-25c: cross-account replay of one document.)
- **Degenerate config / fail-closed** - any change that adds multi-config (N accounts / keys / secrets / optional credentials) MUST test the one-configured and none-configured permutations and assert it **fails closed** (e.g. an INT-signed webhook is rejected 401 when only the UK secret is set), never fails open. (S1-25c gap.)
- **Sequential fan-out loop isolation** - a loop over independent items (accounts, clients): assert a later item raising does NOT undo an earlier item's committed work, and the failure still propagates/surfaces. (S1-25c reconcile `_run`.)
- **Concurrency / config declaration** - assert the Inngest concurrency caps and trigger event via `fn.get_config("").main`. Branching logic INSIDE a decorated Inngest wrapper is exercised directly via `fn._handler` - do not skip it as "untestable".
- **Shared helpers** - a concurrent-duplicate scenario (two callers, same idempotency key) where applicable.
- **Pure helpers** - malformed / None / empty / wrong-type input returns safely rather than raising.

### 8. Standards & hygiene
- `ruff check` + `ruff format --check` clean; `make typecheck` green.
- Imports at module top (never deferred into function bodies). No em dashes; UK dates (DD/MM/YYYY); 24-hour times; currency correctness (GBP vs USD).
- `docs/CHANGELOG.md` updated (Added / Changed / Decision / Discovery / Fixed) AND the active-state memory updated.
- Secrets never committed; throwaway/smoke scripts kept LOCAL (untracked), not in the PR.
- No silent caps/truncation - if coverage is bounded, say so.

### 9. Dashboard / frontend (React + TanStack Query + openapi-fetch)
Apply this lens to ANY change under `apps/dashboard/`. The Python lenses above do not cover the client; these are its equivalents. (Verify with `make typecheck` + `pnpm --filter dashboard build`; there is no ruff for TS.)
- **No error swallowed silently - the frontend twin of "every call records its outcome."** A `catch {}` / `catch (e) {}` that discards the caught value, or that normalises every failure to one generic display error, is a **telemetry hole**: a real transport failure (openapi-fetch RE-THROWS timeouts/resets rather than returning `{ error }`) or a genuine JS bug vanishes. Every caught client-side error on a mutation/query path must be `Sentry.captureException(err)`'d AND preserved as `cause` (`throw new XError(..., { cause: err })`) BEFORE it is normalised for the user. Mutations especially - they are the app's write paths (S1-27a `catch {}` on the attach, the app's only mutation).
- **No type assertion that masks a real mismatch.** `(x as T)`, `as any`, and non-null `!` are holes in the type checker. If the library already gives the correct type (TanStack Query v5 types `mutation.error`/`query.error` as `Error` and narrows it under `isError`), DROP the cast so a wrong type surfaces at compile time. A cast is only legitimate when you know something the compiler provably cannot; never to quiet it on a genuine mismatch. (S1-27a `(attach.error as Error).message`.)
- **Polled/refetched state desync.** Any value held in local state (`useState`) that references a row in a list which a background poll (`refetchInterval`) can refresh is a **staleness trap**: the referenced row can vanish between selection and action. Gate the action on the **resolved entity** (`list.find(x => x.id === heldId) ?? null`), not the raw held id, so the control disables itself when the entity drops out - never fire a request with an id the current data no longer contains. (S1-27a: `clientId` in state + button gated on the id -> stale 400 after the clients poll dropped the pick.)
- **Query-key + queryFn drift.** A `queryKey` reused across components (`['clients']`) must not re-spell an untyped key + inline `queryFn` per call site - that is the frontend twin of lens #6's "one shared alias". Extract a single typed `useClients()` hook so the key and fetch shape cannot silently diverge. (S1-27a picker duplicated the S1-31 board's key - flagged as a follow-up; catch it at authoring time next.)
- **Frontend sad-path tests.** The status->message mapping (409/404/400/timeout), the transport-error normaliser, and pure formatters (`formatDateTime`/`formatCharCount` on null/NaN) each need an assertion, not just a happy-path e2e. An e2e empty-state assertion must target the **specific seeded row** disappearing, not a global "exactly one row" count that another test's data can break. (Tracked under S1-36 vitest - but new mapping/formatter logic in a diff is tested with the diff, not deferred.)

## Output

A short report: findings (each `file:line - issue - fix - test added`), what was fixed, what was consciously deferred (with the owning ticket + reason), and the final green test/lint/typecheck result. Then - and only then - proceed to To Review / PR.

## Scaling the review

- Small diff (one handler) → run the lenses inline yourself.
- Large or cross-cutting diff → spawn parallel review agents (Agent tool), one per lens cluster (concurrency+idempotency / failure-paths / tests+standards), then synthesise and fix. Use a Workflow only if the user has opted into multi-agent orchestration.
