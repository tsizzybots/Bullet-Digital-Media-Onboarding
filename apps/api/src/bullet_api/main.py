"""ASGI entrypoint for the Bullet Digital Media API.

S1-04 ships the minimal surface needed to provision the Render staging
service: a single `/healthz` endpoint that lets Render's health checks
mark the service live. Real routes, DB session wiring, auth, and Inngest
worker registration land in subsequent Sprint 1 tasks (S1-12 onwards).
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="bullet-api", version="0.0.1")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
