"""ASGI entrypoint for the Bullet Digital Media API.

S1-04 shipped the bare `/healthz`. S1-12 adds the structured-JSON
logging config, the `/version` endpoint, and the role-gated example
endpoints used to exercise `require_founder` / `require_pd` etc. Real
routes (PandaDoc webhook, clients dashboard, sales-call ingestion) land
in later sprint tasks.
"""

from __future__ import annotations

from typing import Annotated

import inngest.fast_api
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from bullet_api import __version__
from bullet_api.auth import (
    CurrentUser,
    confirmation_router,
    get_current_user,
    login_router,
    logout_router,
    require_founder,
)
from bullet_api.config import get_settings
from bullet_api.logging_config import configure_logging
from bullet_api.schemas import (
    AdminPingResponse,
    HealthzResponse,
    MeResponse,
    VersionResponse,
)
from bullet_api.worker.client import FUNCTIONS, inngest_client

configure_logging()

app = FastAPI(
    title="bullet-api",
    version=__version__,
    description=(
        "Bullet Digital Media onboarding orchestration API. "
        "Phase 1 - sales call to campaign go-live automation."
    ),
)

# Browser CORS for the dashboard (S1-17). The dashboard runs on a different
# origin (localhost:3000 in dev, the dashboard host in staging/prod) and
# calls this API directly with the session cookie, so credentialed CORS is
# required. `allow_credentials=True` forbids the `*` wildcard, hence the
# explicit origin allowlist from settings.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allow_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the Inngest serve endpoint at /api/inngest. Inngest Cloud calls this
# path to invoke registered background functions. For local dev the Inngest
# dev server (docker-compose, port 8288) handles routing to this same endpoint
# via -u http://host.docker.internal:8000/api/inngest (see docker-compose.yml).
inngest.fast_api.serve(app, inngest_client, FUNCTIONS)

app.include_router(login_router)
app.include_router(confirmation_router)
app.include_router(logout_router)


@app.get("/healthz", tags=["meta"])
async def healthz() -> HealthzResponse:
    """Liveness probe consumed by Render's health check."""
    return HealthzResponse(status="ok")


@app.get("/version", tags=["meta"])
async def version() -> VersionResponse:
    """Return the current API version. Used by deploy verification + the
    dashboard's build-info widget."""
    return VersionResponse(version=__version__)


@app.get("/me", tags=["auth"])
async def me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> MeResponse:
    """Return the authenticated user's profile. 401 if no live session.

    Used by the dashboard on bootstrap to populate the header and decide
    which role-gated routes to render.
    """
    return MeResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
    )


@app.get("/admin/ping", tags=["admin"])
async def admin_ping(
    user: Annotated[CurrentUser, Depends(require_founder)],
) -> AdminPingResponse:
    """Smoke endpoint for the `require_founder` dependency. Returns 200
    only when the caller's session belongs to a `founder`; 403 for any
    other role; 401 with no session cookie."""
    return AdminPingResponse(status="ok", email=user.email)
