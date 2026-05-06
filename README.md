# Bullet Digital Media x IzzyAgents

AI engagement between **IzzyAgents** and **Bullet Digital Media**, a performance marketing agency specialising in Meta ads for gyms and fitness studios (~100 active clients, ~8-12 team members).

> **Current Focus: Phase 1** - all other phases are on hold until Phase 1 is fully delivered and validated.

This repo is the monorepo for the Phase 1 onboarding automation build (sole active phase, May - June 2026). For background, deliverables, decisions, and the full sprint plan see [`docs/`](docs/).

---

## Quickstart

**Prerequisites**

- Node.js 20 LTS (`.nvmrc` pins 20)
- pnpm 10.x (`packageManager` in root `package.json` pins 10.17.0)
- Python 3.12 (`.python-version` pins 3.12)
- [`uv`](https://docs.astral.sh/uv/) 0.11+
- Docker (for `docker compose` local dev, added in Sprint 1 task S1-02)

**Install everything**

```bash
pnpm install
uv sync --all-packages
```

`uv sync --all-packages` installs the root + every workspace member (`apps/api` today). Plain `uv sync` only installs the root and is not what you want.

**Install the local pre-commit hook (one-time per clone)**

```bash
uvx pre-commit install
```

The hook scans every staged file for secrets via [gitleaks](https://github.com/gitleaks/gitleaks) and enforces basic hygiene (trailing whitespace, EOF newlines, JSON/YAML/TOML validity, large-file guard).

**Common scripts (root)**

```bash
make install            # pnpm install + uv sync
make precommit-install  # install the git hook (one-time per clone)
make precommit-run      # run all hooks against every file
make typecheck          # pnpm -r typecheck
make build              # pnpm -r build
make test               # uv pytest + pnpm -r test
make lint               # pnpm -r lint + ruff check apps/api
```

---

## Repo layout

```
apps/
  api/                 # FastAPI service (Python, uv) - onboarding orchestration
  dashboard/           # Internal Bullet ops dashboard (TS, Next.js arrives in S1-16)
packages/
  shared/              # Shared TS types + generated REST client (S1-17)
.github/workflows/     # CI: pre-commit, pnpm typecheck/build, uv ruff/pytest, actionlint
docs/                  # PRD, sprint plan, infrastructure, changelog, meeting notes
progress-site/         # Standalone Vite site for the client-facing progress dashboard
                       # (deployed via render.yaml, NOT part of the pnpm workspace)
scripts/               # One-off Python utilities (e.g. progress snapshot transform)
public/                # Shared static assets consumed by the progress site
```

The pnpm workspace covers `apps/*` + `packages/*` only. The uv workspace covers `apps/api` only. `progress-site/` lives at root and manages its own deps; the existing Render deploy is unchanged.

---

## What this is

Bullet Digital Media faces capacity constraints that cap growth:

- Each team member maxes out at 22-23 clients
- 18-month ramp time for new hires to reach full capacity
- Manual onboarding spans ~3 weeks across ~12 platforms (HubSpot, PandaDoc, GoHighLevel, Asana, Stripe, Xero, Timely, Slack, Google Workspace, Meta, Canva, Loom)
- Repetitive client comms consume time that should go to strategy and campaign management
- Client knowledge is scattered across Google Docs, Sheets, email, Loom, and Canva

### Phase 1: Onboarding Process Automation (Months 1-2) `ACTIVE`

Automate Bullet's end-to-end client onboarding (sales call to campaign go-live) to compress agreement-to-go-live from ~2 weeks toward a single day. Authoritative sources:

- [`docs/PRD.md`](docs/PRD.md) - product requirements, schema, integrations
- [`docs/phase-1-plan.md`](docs/phase-1-plan.md) - internal plan v3.2
- [`docs/phase-1-plan-client.md`](docs/phase-1-plan-client.md) - client-facing version
- [`docs/development-sprints.md`](docs/development-sprints.md) - canonical, ordered task list (S1-01, S1-02, ...)
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) - canonical log of every decision, discovery, and change
- [`docs/infrastructure.md`](docs/infrastructure.md) - hosting, env groups, credentials checklist
- [`emails/Bullet Onboarding Process.pdf`](emails/Bullet%20Onboarding%20Process.pdf) - John Limber's original brief (13/04/2026)

Deliverables:

- Trigger orchestration layer - signed agreement fans out to Slack, Asana, Google Sheets/Drive/Docs/Calendar, Stripe, Xero, Timely, Gmail/GHL reliably and idempotently
- Onboarding status dashboard - single source of truth for every client's step, platform links, and per-action health
- Sales call intelligence - transcript to structured summary stored in the client's knowledge profile
- Kick-off follow-up email generator - AI-drafted post-call email + Stripe subscription activation
- Research agent (Sprint 4) - website scraping, competitor identification, Meta audience sizing

### Future phases

Scoped but not started:

- Phase 2 - internal knowledge bank + client-facing Telegram bot ([archived plan](docs/archive/phase-1-plan-knowledge-bank.md))
- Phase 3 - "Steve AI" digital twin for team query support
- Phase 4 - productised AI tools, AI as a service for gym clients

---

## Stakeholders

| Name | Role |
|------|------|
| John Limber | Founder, Bullet Digital Media |
| Stephen Taylor | Founder, Bullet Digital Media |
| Max | Performance Director |
| Luchiano | Performance Director |

---

## Contributing

- All code follows the conventions in `CLAUDE.md` and the global Anthropic agent guidelines
- Update [`docs/CHANGELOG.md`](docs/CHANGELOG.md) for every code change, decision, or discovery (unconditional)
- Run `make precommit-run` before committing
- Use UK date format (DD/MM/YYYY), 24-hour time, USD currency, no em dashes

Prepared by **IzzyAgents** | AI Solutions Consultancy
