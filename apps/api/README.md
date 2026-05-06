# apps/api

FastAPI service for Bullet Digital Media's onboarding automation. Receives
PandaDoc webhooks, exposes the typed REST surface consumed by the dashboard,
and registers Inngest functions for cross-platform fan-out.

This package is currently a scaffold (S1-01). Real routes, DB layer, auth, and
Inngest worker registration land in subsequent Sprint 1 tasks (S1-12 onwards).

## Run

From the repo root:

```bash
uv sync
uv run pytest apps/api -q
```
