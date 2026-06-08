"""Unit tests for the GoHighLevel HTTP client (`HttpGhlClient`).

These exercise the request shaping (URL, headers, body) and the
status-code -> exception mapping via an injected `httpx.MockTransport`,
so no network call is made. The retriable/non-retriable split
(`GhlServerError` vs `GhlClientError`) is what the S1-25 worker relies on
to decide whether Inngest should retry.
"""

from __future__ import annotations

import json

import httpx
import pytest

from bullet_api.ghl.client import (
    GHL_API_VERSION,
    GhlClientError,
    GhlLocation,
    GhlServerError,
    HttpGhlClient,
)


def _transport(status_code: int, body: dict | str) -> httpx.MockTransport:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        if isinstance(body, dict):
            return httpx.Response(status_code, json=body)
        return httpx.Response(status_code, text=body)

    transport = httpx.MockTransport(handler)
    transport.captured = captured  # type: ignore[attr-defined]
    return transport


async def test_create_location_success_parses_location() -> None:
    transport = _transport(
        200, {"id": "loc_abc123", "name": "Sample Gym Ltd", "companyId": "comp_1"}
    )
    client = HttpGhlClient(api_key="agency-key", transport=transport)

    location = await client.create_location({"name": "Sample Gym Ltd", "companyId": "comp_1"})

    assert isinstance(location, GhlLocation)
    assert location.id == "loc_abc123"
    assert location.name == "Sample Gym Ltd"
    assert location.company_id == "comp_1"
    assert location.raw["id"] == "loc_abc123"


async def test_create_location_sends_expected_url_headers_and_body() -> None:
    transport = _transport(201, {"id": "loc_1"})
    client = HttpGhlClient(api_key="agency-key", transport=transport)

    payload = {"name": "Gym", "companyId": "comp_1", "snapshotId": "snap_1"}
    await client.create_location(payload)

    request: httpx.Request = transport.captured["request"]  # type: ignore[attr-defined]
    assert request.method == "POST"
    assert str(request.url) == "https://services.leadconnectorhq.com/locations/"
    assert request.headers["Authorization"] == "Bearer agency-key"
    assert request.headers["Version"] == GHL_API_VERSION
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == payload


async def test_empty_api_key_raises_runtime_error() -> None:
    client = HttpGhlClient(api_key="")
    with pytest.raises(RuntimeError) as exc:
        await client.create_location({"name": "Gym", "companyId": "c"})
    assert "GHL_AGENCY_API_KEY" in str(exc.value)


@pytest.mark.parametrize("status_code", [400, 401, 403, 422])
async def test_4xx_raises_client_error(status_code: int) -> None:
    transport = _transport(status_code, "bad request")
    client = HttpGhlClient(api_key="agency-key", transport=transport)
    with pytest.raises(GhlClientError) as exc:
        await client.create_location({"name": "Gym", "companyId": "c"})
    assert exc.value.status_code == status_code


@pytest.mark.parametrize("status_code", [429, 500, 502, 503])
async def test_429_and_5xx_raise_server_error(status_code: int) -> None:
    transport = _transport(status_code, "upstream error")
    client = HttpGhlClient(api_key="agency-key", transport=transport)
    with pytest.raises(GhlServerError) as exc:
        await client.create_location({"name": "Gym", "companyId": "c"})
    assert exc.value.status_code == status_code
