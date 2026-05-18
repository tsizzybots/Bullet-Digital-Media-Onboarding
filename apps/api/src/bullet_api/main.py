"""ASGI entrypoint for the Bullet Digital Media API.

S1-04 shipped the bare `/healthz`. S1-12 adds the structured-JSON
logging config, the `/version` endpoint, and the role-gated example
endpoints used to exercise `require_founder` / `require_pd` etc. Real
routes (PandaDoc webhook, clients dashboard, sales-call ingestion) land
in later sprint tasks.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI

from bullet_api import __version__
from bullet_api.auth import (
    CurrentUser,
    confirmation_router,
    get_current_user,
    login_router,
    require_founder,
)
from bullet_api.logging_config import configure_logging

configure_logging()

app = FastAPI(
    title="bullet-api",
    version=__version__,
    description=(
        "Bullet Digital Media onboarding orchestration API. "
        "Phase 1 - sales call to campaign go-live automation."
    ),
)

app.include_router(login_router)
app.include_router(confirmation_router)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    """Liveness probe consumed by Render's health check."""
    return {"status": "ok"}


@app.get("/version", tags=["meta"])
async def version() -> dict[str, str]:
    """Return the current API version. Used by deploy verification + the
    dashboard's build-info widget."""
    return {"version": __version__}


@app.get("/me", tags=["auth"])
async def me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict[str, object]:
    """Return the authenticated user's profile. 401 if no live session.

    Used by the dashboard on bootstrap to populate the header and decide
    which role-gated routes to render.
    """
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
    }


@app.get("/admin/ping", tags=["admin"])
async def admin_ping(
    user: Annotated[CurrentUser, Depends(require_founder)],
) -> dict[str, str]:
    """Smoke endpoint for the `require_founder` dependency. Returns 200
    only when the caller's session belongs to a `founder`; 403 for any
    other role; 401 with no session cookie."""
    return {"status": "ok", "email": user.email}
