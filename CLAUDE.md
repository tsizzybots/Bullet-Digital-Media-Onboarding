# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Active state (cross-session continuity)

Claude maintains a single active-state file inside the project's auto-memory directory so a new chat picks up exactly where the last one left off. The file is indexed as the first entry in `MEMORY.md` and tracks: the currently-in-flight ticket, its sub-tasks (done vs not done), recently-completed tickets in the current sprint, open follow-ups discovered during the work but not yet ticketed, and cross-session references (Flow card ids, branches, PR locations, plan files).

**Read it first.** At the start of every non-trivial session, after auto-loading `MEMORY.md`, read the entry titled "Active state" before doing anything else. Use it to recover context instead of asking the user "where were we?".

**Keep it accurate.** Update the file (overwrite, do not accrete) whenever any of the following happen:

- A ticket changes column on the Flow board (e.g. In Progress → To Review).
- A sub-task on the current ticket is completed or a new one is identified.
- A blocker or follow-up is discovered.
- A scope, architecture, or product decision is made that affects the active work (these still get logged to `docs/CHANGELOG.md` per the changelog rules below; the active-state file is the short, scannable summary).
- The current ticket lands (merged + deployed) — move its entry from "Currently in flight" to "Recently completed (current sprint, for context)" and promote the next-up ticket to "Currently in flight".

Do not let the file go stale. If the in-flight ticket entry says "code-complete, awaiting human review" but the PR has been merged, the file is wrong and must be updated before any further work.

**Scope.** Keep entries short and link-heavy: ticket id + column, branch name, PR location, one-line status, a brief sub-task checklist. The full prose lives in `docs/CHANGELOG.md`; this file is the index that lets a fresh session find the right CHANGELOG section / Flow card / plan file in one read.

## Project Overview

This is a **project planning and documentation workspace** for the IzzyAgents & Bullet Digital Media AI engagement. Bullet Digital Media is a gym/fitness marketing agency (~91-100 active clients, ~8-12 team members) that IzzyAgents is building AI solutions for.

**Current status**: Active development phase (entered 03/05/2026). Planning is complete; Phase 1 plan v3.2 is locked as the build spec. Application code is being added to this workspace as the build progresses.

**Current focus**: Phase 1 only. Future phases will not begin until Phase 1 is fully complete.

## Directory Structure

- `scope/` - Project scope documents and development priorities
- `meeting_notes/` - Onboarding and meeting transcripts/summaries (subdirs per topic, e.g. `meeting_notes/onboarding/`)
- `questionnaire_responses/` - Client discovery questionnaire data (CSV)
- `emails/` - Source email briefs from Bullet (e.g. `Bullet Onboarding Process.pdf`)
- `docs/` - Plan documents, Loom summaries, and other project documentation
  - `docs/CHANGELOG.md` - **canonical log of all development updates, decisions, and discoveries** (see "Changelog Discipline" below)
  - `docs/phase-1-plan.md` and `docs/phase-1-plan-client.md` - current Phase 1 plans (internal + client-facing)
  - `docs/loom-video-summaries/` - Steve's walkthroughs of the current onboarding mechanics (OB-Phase-1, OB-Phase-2)
  - `docs/archive/` - Deferred earlier-scope plans (knowledge bank, Telegram bot)

## Client Context

- **Bullet Digital Media**: Performance marketing agency specializing in Meta ads for gyms/fitness studios
- **Key stakeholders**: John Limber (Founder), Stephen Taylor (Founder), Max & Luchiano (Performance Directors)
- **Current capacity**: 22-23 clients per team member, 18-month ramp to full capacity for new hires
- **Primary communication**: Trello (backlog/ideas), email (formal), WhatsApp (quick check-ins)

## Phase 1: Onboarding Process Automation (Months 1-2)

This is the sole active phase. The goal is to automate Bullet's end-to-end client onboarding process (sales call through campaign go-live) to compress agreement-to-go-live from ~2 weeks toward a single day.

Primary sources of truth for the plan:
- `docs/phase-1-plan.md` (internal plan, current version v3 - 22/04/2026)
- `docs/phase-1-plan-client.md` (client-facing version)
- `emails/Bullet Onboarding Process.pdf` (John Limber, 13/04/2026 - original brief)
- `meeting_notes/onboarding/` (most recent: IzzyAgents & Bullet Digital Media 21/04/2026)
- `docs/loom-video-summaries/` (Steve's OB-Phase-1 and OB-Phase-2 walkthroughs of the current onboarding mechanics - authoritative source for today's Zapier chains, GHL workflows, and manual workarounds)

Previous Phase 1 scope (internal knowledge bank + client-facing Telegram bot) is deferred to a later phase; archived under `docs/archive/`.

Key deliverables:
- **Database + dashboard as the central source of truth** - Postgres holds all client data; dashboard is live from Sprint 1 (not a Sprint 4 polish item). Google Sheets, Google Docs, and GoHighLevel custom fields become optional mirrors, never the primary store
- **Trigger orchestration layer** - a PandaDoc signing event fans out to Slack, Asana, Google Sheets/Drive/Calendar, Stripe, Xero, Timely, Gmail/GHL reliably and idempotently; retires the current Zapier + Pabbly chain
- **Onboarding status dashboard** - single view of every client's step, platform links, knowledge profile, and per-action health; live asset checklist per client replacing the 16-branch GHL Outstanding Elements workflow
- **Sales call intelligence** - transcript to structured summary stored in the client's knowledge profile in the database and surfaced in the dashboard
- **Kick-off follow-up email generator** - AI-drafted post-call email with deterministic offer pricing (75% membership anchor + consultation + body scan + bring-a-friend/MBG framing); Stripe subscription activation after sign-off
- **Client research agent** (Sprint 4) - website scraping, competitor identification, Meta audience sizing, offer suggestions stored in the knowledge profile

Key decisions (confirmed 21/04/2026):
- **Agreement platform: PandaDoc stays** (HubSpot does not offer the document handling Bullet needs; PandaDoc is already natively integrated with HubSpot). No abstraction layer.
- **Client onboarding portal: GHL portal retained for Phase 1**. Custom-branded portal is a Phase 2 engagement deliverable.
- **Pabbly middleman: retired**. Direct GoHighLevel API for sub-account creation, with returning-client existence check.
- **Loom videos as documentation standard**: Steve continues to record Loom walkthroughs of team processes to feed future AI agents.

Long-term vision (from 21/04/2026 call):
- **AI agent conveyor belt**: individual agents per step (sales, onboarding, research, post-onboarding) reporting to one orchestrator agent
- **Agnostic interface**: clients interact only with the IzzyAgents front-end; underlying tools (Meta, GHL, Canva, etc.) can be swapped without clients feeling the churn ("Perplexity for gyms and fitness")
- Phase 1's database, orchestration layer, and knowledge profile are the concrete foundation for this vision

Key constraints:
- Platforms involved: HubSpot, PandaDoc, GoHighLevel, Asana, Stripe, Xero, Timely, Slack, Google Workspace (Sheets/Docs/Drive/Calendar/Gmail), Meta Business Manager, Canva, Loom, Leadsy (for one-click Facebook asset access)
- GoHighLevel conditional email workflows are triggered where they still make sense; the 16-branch Outstanding Elements tech follow-up workflow is replaced with a DB-driven dashboard checklist and single conditional email template
- Zoom to Google Meet migration in progress; transcript capture must work against whichever is live
- Internal-facing tool; single-tenant (Bullet team only)
- Every fan-out action is idempotent, retryable, and individually auditable - partial failures must be visible in the dashboard, never silent
- Every job writes its outcome (success/failure, external ID, retry count) back to Postgres - nothing is inferred from live platform state at view-time

## Ticket lifecycle and documentation rituals

Every ticket follows the same documentation touchpoints. Skipping any of these breaks cross-session continuity, code review, or the audit trail. The list below is exhaustive — if a step has no obvious change to record, write a one-liner explaining that (the absence-of-change is itself useful signal).

### When STARTING work on a ticket

1. **Flow card → "In Progress"** via `mcp__flow__cards_move`. Top of the column so it's the most-recently-touched card.
2. **Drop a "starting" comment** on the card via `mcp__flow__cards_add_note` describing: scope as you understand it, any open questions before you write code, dependencies on other tickets, and any pre-existing context you are pulling in (Loom transcripts, past meetings, related PRs).
3. **Update `project_active_state.md`** (in the auto-memory dir): set this ticket as the "Currently in flight" entry; capture the initial sub-task checklist with everything marked TODO.
4. **Author a plan** when the work is non-trivial. Plan lives at `~/.claude/plans/<slug>.md` (Claude Code's plan-mode default location); reference it from the active-state file.

### DURING active work

5. **Update `project_active_state.md`** whenever a sub-task closes, a new sub-task is identified, a blocker is discovered, or a scope/architecture decision is made. Overwrite, do not accrete.
6. **Log decisions and discoveries to `docs/CHANGELOG.md`** the moment they happen, not retrospectively (see the Changelog Discipline section below for the rules). The active-state file is the short index; CHANGELOG is the full prose.
7. **Add comments on the Flow card** when a meaningful course correction lands (architecture pivot, scope change accepted by the owner, blocker resolution). Keep these short — they exist so a future reviewer scrolling the card sees the major moments without diving into git.

### When CODE-COMPLETE (ready for review)

8. **Local verification before push.** Run the full local equivalents of CI: `uv run pytest apps/api -q`, `uv run ruff check apps/api`, `uv run ruff format --check apps/api`, `make typecheck`. The first three are the must-pass gates; `make lint` overall is currently red on a pre-existing dashboard issue (see CHANGELOG entry on 02/06/2026 for context) so check the Python half only.

   **Docker must be UP for this to mean anything.** Without Postgres the DB-marked tests SKIP and the run still reports green - that is exactly how two review rounds shipped (~223 tests silently became skips). Confirm `docker info` succeeds and the run reports **0 skipped** before treating a green suite as verification.

8a. **Run `make review-gate` — REQUIRED, must be clean.** See "The review gate" section below. With Postgres up also run `make review-gate-db`, which mutation-tests the db-marked guards. `UNPROVEN` is not a pass.
9. **Run the `/pre-pr-review` hardening gate — REQUIRED, do not skip.** (Complementary to step 8a, not replaced by it: the gate catches the mechanical classes, this catches the ones needing judgement.) This is a deep, adversarial self-review of the diff (see `.claude/skills/pre-pr-review/SKILL.md`). It applies the project's hardening lenses: shared/reused seams judged against ALL callers + concurrent use (not just the first caller or its protective cap), failure paths INCLUDING transport-level errors (timeouts/resets, not only typed errors), idempotency / replay / at-least-once windows, and a success-AND-failure-AND-replay test matrix. **Every finding is FIXED (or consciously deferred with a logged reason + owning ticket) and the full suite re-run green before proceeding.** The bar: a diff that has already been stress-tested for the failure and concurrency cases, not just the happy path.
10. **Append the per-ticket entry to `docs/CHANGELOG.md`** under `[Unreleased]` with date heading `### DD/MM/YYYY - <ticket>: <short title>`. Bullets tagged Added / Changed / Removed / Decision / Discovery / Fixed / Verified, matching the format every prior ticket uses (read the latest 2-3 entries before writing to keep the voice consistent).
11. **Push the branch** as `feat/<ticket-id>-<slug>` (or `fix/...`, `docs/...`, etc.). Open a PR against `main` using the project's PR template (Summary / StrikeFlow card / Test plan / Checklist sections).
12. **Move the Flow card to "To Review"** via `mcp__flow__cards_move`.
13. **Drop a completion comment on the Flow card** via `mcp__flow__cards_add_note`. Standard sections: what was built (bullets), verification (test counts + local commands run + the `/pre-pr-review` pass), known follow-ups not in this PR's scope (so a reviewer or future-self can spot the parking lot).
14. **Update `project_active_state.md`**: ticket entry status becomes "code-complete, awaiting human review", note any outstanding actions still required (e.g. "append CI-green Verified bullet after green").

### After CI lands GREEN

15. **Append a `**Verified**: CI run <run-id> on the PR's HEAD commit (<sha>) is 5/5 GREEN ...` bullet** to the same CHANGELOG entry, listing each job that passed. Pattern set by the CI-green entry on 04/06/2026 (CHANGELOG line ~52).
16. **Commit the changelog update** as a small `docs: log CI 5/5 green for <ticket-id>` commit on the same branch and push. CI runs once more; once it's green again, the PR is ready for merge.
17. **Update `project_active_state.md`** to clear the "after CI lands green" outstanding action.

### When the PR is MERGED + branch is deleted

18. **In `project_active_state.md`**, move the ticket entry from "Currently in flight" into "Recently completed (current sprint, for context)" at the top of that list. Promote whichever ticket is next-up into "Currently in flight" (its initial sub-task checklist + plan slot stay empty until work actually starts).
19. **Any newly-discovered follow-ups** that were not tracked under "Open follow-ups discovered" should be added there now, with a one-line rationale + a proposed ticket id (e.g. "S1-25c").

### Universal rules

- **Never ask permission to update the changelog, the Flow card, or `project_active_state.md`.** Logging is unconditional and automatic; these three sources of truth must reflect reality at all times.
- **The active-state file is the index; CHANGELOG is the prose; Flow is the human-visible status.** When the three disagree, the active-state file is the most likely to be wrong (it can drift if a step is skipped) — fix it first, then update whichever others lag.
- **External-system writes are reversible-low-blast-radius.** Flow card moves, card comments, changelog appends, and memory-file overwrites do not require explicit user confirmation; they are part of the operating contract.

## The review gate (`make review-gate`)

Five consecutive review rounds on S1-26b/c returned the same shape of finding: **the fix was right, and the test proving it was missing or could not fail.** Round 5 stated it as a procedure - *"Delete all three guards and 609 tests still pass."* The gate mechanizes the reviewer's own catch techniques so a finding of that class fails locally instead of in a review round.

Added 25/08/2026. `apps/api/scripts/review_gate.py` (static) + `apps/api/scripts/review_gate_mutate.py` (mutation). **`make review-gate` runs BOTH halves, and the mutation half includes the db-marked guards by default, so Postgres must be up.** Without it those tests skip, a skipped test cannot fail, so it cannot prove a guard - the runner reports `UNPROVEN` and FAILS the build. On a laptop with no Postgres, `make review-gate MUTATE_ARGS=--allow-unproven` exits 0, knowing those guards are then unverified.

**The four static checks** (fast, no DB needed):

- **G1 comment-literal coverage** - a comment naming example data (`"1234567890"`, `"AB12 3CDE"`) must have that literal in a test. Our own fix comments tell a reviewer exactly what string to grep for; this greps first.
- **G4 no conditional assertions** - an `assert` behind an `if` that reads the system under test passes silently in the exact case it exists to catch.
- **G5 no defaults on discriminating fixture params** - a fixture defaulting `phone`/`contact_*`/`address` makes every seeded row agree for free, pinning a matching bar open so mutating it to a constant passes the whole suite.
- **G6 diff-scoped PII** - live ids / client emails counted against the merge base, net-new only.

**The mutation manifest** (`make review-gate-db` runs it alone; needs Postgres): `apps/api/tests/mutation_manifest.toml`. **Every guard you add must declare the test that kills it.** The runner breaks the guard, runs only that test, and asserts it FAILS. Outcomes: `KILLED` (defended), `SURVIVED` (revert-green - a finding), `UNPROVEN` (the named test skipped, so it cannot prove anything - **not** a pass, and it fails the build), `ERROR` (the `find` pattern went stale, or `must_fail` names a test that no longer exists - both mean the guard is unprotected while looking guarded).

**The contract: a new guard without a manifest entry is not finished.** Adding an entry is a two-line diff. Skipping one has cost a review round every time.

**What it cannot check**, and therefore goes in the PR body as answered questions:

- **Invariant statement** for any normalizer change: what must hold regardless of input form, and which axes you checked (order, separator, case, extra tokens). Round 4 fixed the order axis and broke the separator axis - the same class of bug it was fixing.
- **Source trace** for any new decision signal: which template populates it, and does it hold on **both** PandaDoc accounts (UK and INT)?
- **Sequence walk** per handler: lost response, retry, concurrent signing. Bugs live in histories, not states.
- **Docstring claim audit**: every "never / cannot / guarantees / prevents" needs a test named after it, or the claim gets softened. Treat every docstring as falsifiable - the reviewer does.

The gate is **additive to `/pre-pr-review`, not a replacement**. It catches the mechanical classes; the skill catches the ones needing judgement. Both run.

## Changelog Discipline

`docs/CHANGELOG.md` is the canonical log of development progress for this engagement. It exists because this is a multi-month build with shifting platform context and a single client; git log alone is not enough to retain decisions and discoveries.

**You must update `docs/CHANGELOG.md` whenever any of the following happen:**

- New code, files, features, or capabilities are added.
- Existing behaviour, scope, or structure is changed.
- Code, files, or capabilities are removed, deprecated, or retired.
- A scope, architecture, or product **decision** is confirmed (e.g. "we are using X over Y").
- A **discovery** is made about Bullet's existing processes, platforms, constraints, integrations, or data shapes (e.g. learning that a GHL workflow has a hidden branch, that a Stripe field is required, that a transcript format differs from what was assumed).
- A bug is fixed in a way that's worth remembering.

**Rules:**

1. Append new entries to the top of the `[Unreleased]` block under a `### DD/MM/YYYY` heading (UK date format).
2. Tag each bullet with one of: **Added**, **Changed**, **Removed**, **Decision**, **Discovery**, **Fixed**.
3. Capture decisions and discoveries **in the moment** they are made or learned, not retrospectively.
4. Reference commit hashes, file paths, or PR numbers where useful.
5. Do not skip the changelog because "the commit message says it" - the changelog is for the human reader picking up context weeks later, not for replaying git history.
6. Follow the project's formatting rules (no em dashes, UK dates, 24-hour times, USD).

If a task touches code AND introduces a decision/discovery, log both - one as Added/Changed/Fixed, the other as Decision/Discovery.

**Never ask for permission to update the changelog.** Logging is unconditional and automatic. Whenever any of the trigger conditions above occur, update `docs/CHANGELOG.md` in the same response that produced the change. Do not say "want me to log this?" or "should I update the changelog?" - just do it. The only exception is if the user has explicitly told you not to log a specific item in the current conversation.

## Document Formatting Rules

- Never use em dashes (-). Always use standard hyphens (-).
- All dates must use UK format: DD/MM/YYYY (e.g., 23/03/2026)
- All times must use 24-hour format (e.g., 14:30, not 2:30 PM)
- Currency in USD ($) unless otherwise specified

## GitHub
- Account: `tsizzybots` - always use this account for this project
- Repo: `tsizzybots/Bullet-Digital-Media-Onboarding` (the rename to `bullet_digital_media` planned in the S1-01 changelog entry was never executed; the original repo name remains in use)
- Before pushing, ensure active account: `gh auth switch --user tsizzybots`

## Flow Integration
- Board Name: "Bullet Digital Media Onboarding"
- Board ID: b78ad970-8628-4325-aaa8-4b9b1763c789
- Project board has been migrated from StrikeFlow (28/05/2026); the original StrikeFlow board (`c01081f2-c27c-4a8c-b7c5-0b2857254cd9`) is retained as read-only backup only. All new card work happens on Flow via the `mcp__flow__*` tools.

## Future Phases (Not Yet Active)

These phases are scoped but will only begin after Phase 1 is fully complete:
- **Phase 2 (originally planned Phase 1)**: Internal client knowledge bank + client-facing Telegram AI bot. Archived plan in `docs/archive/phase-1-plan-knowledge-bank.md` and `docs/archive/sprint-plan-knowledge-bank.md`
- **Phase 3**: Client comms AI ("Steve AI") - digital twin for team query support, library of standard responses
- **Phase 4**: Productised AI tools, AI-as-a-service to gym clients, staff training AI
