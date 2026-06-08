"""GoHighLevel (LeadConnector) API integration.

`client` holds the REST client abstraction (Protocol + production HTTP
client + test double), mirroring `bullet_api.pandadoc`. S1-25
(`create_ghl_subaccount`) is the first consumer: it creates a client's
GHL sub-account directly against the agency API, retiring the old Pabbly
middleman.
"""

from __future__ import annotations

from bullet_api.ghl.client import (
    GHL_API_BASE_URL,
    GHL_API_VERSION,
    FakeGhlClient,
    GhlClient,
    GhlClientError,
    GhlError,
    GhlLocation,
    GhlServerError,
    HttpGhlClient,
    get_ghl_client,
)

__all__ = [
    "GHL_API_BASE_URL",
    "GHL_API_VERSION",
    "FakeGhlClient",
    "GhlClient",
    "GhlClientError",
    "GhlError",
    "GhlLocation",
    "GhlServerError",
    "HttpGhlClient",
    "get_ghl_client",
]
