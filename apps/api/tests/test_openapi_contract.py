"""Contract tests for the S1-17 typed-client surface.

The codegen pipeline (OpenAPI -> TS client) depends on two things this suite
guards:

1. Response bodies are *named schema components* with typed properties, not
   the loose `additionalProperties` object a bare `dict[str, str]` return
   produces. The named-component `$ref` is what gives the generated client an
   exact type (`{ status: string }`) instead of `Record<string, string>`, and
   is what lets the dashboard consume responses without `any`.

2. CORS is wired with credentials so the browser dashboard (a different
   origin) can call the API and send the session cookie.

Both are non-DB tests - they only read the generated OpenAPI document and
exercise middleware, so they run without a live Postgres.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from bullet_api.config import get_settings
from bullet_api.main import app


@pytest.mark.asyncio
async def test_meta_endpoints_use_named_response_components() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/openapi.json")
    assert response.status_code == 200
    doc = response.json()

    schemas = doc["components"]["schemas"]
    assert "HealthzResponse" in schemas
    assert schemas["HealthzResponse"]["properties"]["status"]["type"] == "string"

    # The 200 response references the named component rather than an inline
    # additionalProperties object. This `$ref` is the difference between a
    # generated `{ status: string }` and a weak `Record<string, string>`.
    healthz_schema = doc["paths"]["/healthz"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert healthz_schema == {"$ref": "#/components/schemas/HealthzResponse"}

    me_schema = doc["paths"]["/me"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert me_schema == {"$ref": "#/components/schemas/MeResponse"}
    for field in ("id", "email", "full_name", "role"):
        assert field in schemas["MeResponse"]["properties"]


@pytest.mark.asyncio
async def test_resend_confirmation_request_is_typed() -> None:
    """The request body moved from a bare dict to a validated model, so the
    generated client gets a typed `{ email: string }` request schema."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/openapi.json")
    doc = response.json()

    body_schema = doc["paths"]["/auth/resend-confirmation"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert body_schema == {"$ref": "#/components/schemas/ResendConfirmationRequest"}
    props = doc["components"]["schemas"]["ResendConfirmationRequest"]["properties"]
    assert "email" in props


@pytest.mark.asyncio
async def test_cors_allows_dashboard_origin_with_credentials() -> None:
    """A request from an allowed origin comes back with the matching
    allow-origin header and allow-credentials=true - the combination the
    dashboard's credentialed fetch needs."""
    origin = get_settings().cors_allow_origins_list[0]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/healthz", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
