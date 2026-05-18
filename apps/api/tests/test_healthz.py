"""Smoke tests for the /healthz endpoint.

Render's Blueprint health check polls this path during deploys; the
contract is asserted here so any regression is caught locally before
the deploy round-trip.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from bullet_api.main import app


def test_healthz_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
