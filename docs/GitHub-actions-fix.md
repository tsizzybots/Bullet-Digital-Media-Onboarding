# GitHub Actions Fix - Action Required

**Date:** 03/06/2026
**Owner:** Tim (repo admin - only you can add the secrets below)
**Repo:** `tsizzybots/Bullet-Digital-Media-Onboarding`
**Status:** DONE (04/06/2026) - the three Neon `python-tests` secrets are configured and CI is now **5 of 5 green** on `main` (run `26931499074`, commit `f11e3ce`). Adding the secrets made `python-tests` execute for the first time, which surfaced two latent regressions already on `main` (a reserved `created` LogRecord key in the S1-23 cron, and trailing whitespace in a QA report) - both fixed in commit `f11e3ce`. Items 2-4 in the table below remain open dev follow-ups (not blocking CI).

---

## Outstanding items at a glance

| # | Item | Who | Blocking CI? |
|---|---|---|---|
| 1 | DONE (04/06/2026) - 3 Neon secrets added; CI now 5/5 green (run `26931499074`) | Tim - complete | No (was the blocker) |
| 2 | Wire the S1-18 Playwright E2E suite into CI | Dev task (new card) | No - runs locally via `make e2e` |
| 3 | Add an ESLint flat config so `next lint` actually runs | Dev task (new card) | No - currently soft-passed with `\|\| true` |
| 4 | (Optional) bump deprecated Node-20 actions | Dev task | No |

Item 1 is now complete (04/06/2026) - CI is 5/5 green. Items 2-4 are normal dev cards discovered during S1-18 and do not need repo-admin access.

---

## What this is

While landing S1-17, CI was found to have been red on `main` since 22/05/2026. Three problems were fixed in code and pushed:

1. `ruff format` drift (blocked the `python-checks` job) - fixed by reformatting `apps/api`.
2. Inngest `serve()` import error in tests - fixed by running the suite in Inngest dev mode.
3. A bad action pin `neondatabase/delete-branch-action@v5` (no such version) - repinned to `@v3`.

After those fixes the only remaining failure is the **`python-tests`** job. It is **not a code problem** - the repository has no GitHub Actions secrets configured at all (`gh secret list` returns nothing), so the job cannot reach a database. Your action below closes that gap and turns CI fully green (5 of 5).

---

## Action 1 (urgent, repo-admin only): add three Neon secrets

> **COMPLETED 04/06/2026.** All three secrets below are set on `tsizzybots/Bullet-Digital-Media-Onboarding` (verify with `gh secret list`). `NEON_PROJECT_ID` is `old-credit-26332098` (the `bullet-staging` project). The `NEON_API_KEY` is a *personal* Neon key because the logged-in account is an org member, not an org admin (the org-level "Create new API key" button is disabled); a member's personal key still inherits access to the org's `bullet-staging` project, which is enough for CI branch create/delete. CI run `26931499074` is 5/5 green. The steps below are retained for reference and future key rotation.

Add **three repository secrets** to the GitHub repo.

| Secret name | What it is | Where to get the value |
|---|---|---|
| `NEON_API_KEY` | Neon API key used to create/delete an ephemeral test branch per CI run | Neon console -> top-right account menu -> **Account settings** -> **API keys** -> *Create new key* |
| `NEON_PROJECT_ID` | The Neon project the test branch is created in | Neon console -> your project -> **Settings** -> **General** -> *Project ID* (e.g. `cool-darkness-12345678`) |
| `NEON_STAGING_DATABASE_URL` | Fallback `DATABASE_URL` used if per-run branch creation fails | Neon console -> your project -> **Connect** -> connection string for the **main/staging** branch (the full `postgresql://...` URL) |

These names are fixed - the workflow reads them verbatim in `.github/workflows/ci.yml` (the `python-tests` job). Use the exact spelling above.

---

## How to add them

### Option A - Web UI (simplest for pasting a long URL)

1. Open: `https://github.com/tsizzybots/Bullet-Digital-Media-Onboarding/settings/secrets/actions`
   (or: repo -> **Settings** -> **Secrets and variables** -> **Actions**)
2. Click **New repository secret**.
3. Enter the **Name** (e.g. `NEON_API_KEY`) and paste the **Secret** value.
4. Click **Add secret**. Repeat for all three.

### Option B - gh CLI (run in your own terminal)

You are already authenticated as `tsizzybots`. The prompt reads the value without echoing it, so nothing is stored in shell history:

```bash
gh secret set NEON_API_KEY
gh secret set NEON_PROJECT_ID
gh secret set NEON_STAGING_DATABASE_URL
```

---

## Verify and re-run CI

1. Confirm all three registered:

   ```bash
   gh secret list
   ```

   You should see `NEON_API_KEY`, `NEON_PROJECT_ID`, and `NEON_STAGING_DATABASE_URL`.

2. Re-run the latest CI without a new commit:

   ```bash
   gh run rerun $(gh run list --branch main --workflow CI --limit 1 --json databaseId --jq '.[0].databaseId')
   ```

3. Watch it finish:

   ```bash
   gh run watch $(gh run list --branch main --workflow CI --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
   ```

Expected result: all 5 jobs green. The `python-tests` job creates a throwaway Neon branch, runs the Alembic migrations up/down/up, runs `pytest`, then deletes the branch.

---

## Action 2 (dev card, no repo-admin access needed): wire the Playwright E2E suite into CI

S1-18 added a Playwright E2E suite (`apps/dashboard/e2e/`) that boots the API + dashboard and drives the real login and email-confirmation flows. It runs locally today via `make e2e`, but it is **not** in CI yet, so a future change could silently break login or confirmation without CI catching it.

What to add - a new `e2e` job in `.github/workflows/ci.yml` that:

1. Provisions Postgres: either a `services: postgres:` container, or reuse the per-PR Neon branch pattern already used by `python-tests` (depends on Action 1's secrets if you go the Neon route; a plain `services: postgres` container needs no secrets and is the simpler choice for E2E).
2. Installs uv (Python), pnpm + Node, and the Chromium browser: `pnpm --filter @bullet/dashboard exec playwright install --with-deps chromium`.
3. Runs `alembic upgrade head` against the test DB.
4. Runs `pnpm --filter @bullet/dashboard test:e2e` with `DATABASE_URL` and `INNGEST_DEV=1` exported.

Notes:
- Playwright's `webServer` config boots **both** servers and `global-setup` seeds the two fixture users itself (via `apps/api/scripts/seed_e2e_users.py`), so the job does not start servers or seed manually - it just needs a migrated DB and the env vars.
- The API webServer block in `playwright.config.ts` already sets `INNGEST_DEV=1`; exporting it at the job level too is belt-and-braces.
- In Actions, `CI=true` makes Playwright boot fresh servers (it disables `reuseExistingServer`).
- This is self-contained dev work - no repo-admin action required. (Deferred deliberately in S1-18.)

---

## Action 3 (dev card, no repo-admin access needed): make `next lint` actually run

The `typescript-checks` job runs `pnpm -r --if-present lint || true`. The dashboard has **no ESLint flat config committed**, so `next lint` is non-functional: it drops into its interactive "How would you like to configure ESLint?" setup and exits non-zero, and the trailing `|| true` swallows that. Net effect: **lint provides zero coverage in CI today** - only `tsc --noEmit` and `next build` actually gate the TypeScript.

What to do:

1. Add an ESLint flat config to `apps/dashboard` (e.g. `eslint.config.mjs` using `eslint-config-next`'s flat export, or run `npx @next/codemod@latest next-lint-to-eslint-cli .`).
2. Confirm `pnpm --filter @bullet/dashboard lint` runs non-interactively and passes.
3. Drop the `|| true` from the `Lint` step in `ci.yml` so lint becomes a real gate.

This is self-contained dev work - no repo-admin action required.

---

## Notes

- **Scope:** these are added as **repository** secrets, which the workflow reads directly (`secrets.NEON_*`). No workflow change is needed. (If you would rather scope them to a GitHub *Environment*, the `python-tests` job would need an `environment:` key added - the simpler path is repository secrets.)
- **Security:** `NEON_STAGING_DATABASE_URL` contains a live password. Adding it as a secret is the correct place for it; never commit it to the repo. The `.env*` files stay gitignored.
- **Optional hardening (separate, not required for green CI):** GitHub showed deprecation warnings that `actions/checkout@v4`, `actions/setup-node@v4`, `astral-sh/setup-uv@v4`, `pnpm/action-setup@v4`, and `actions/setup-python@v5` run on Node.js 20, which is being retired. Bumping these to their newer major versions can be done later in a small CI maintenance pass.
