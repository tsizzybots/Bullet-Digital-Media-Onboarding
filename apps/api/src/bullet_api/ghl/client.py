"""GoHighLevel (LeadConnector) REST API client abstraction.

`GhlClient` is a small Protocol so handlers depend on the interface
rather than on GoHighLevel specifically. `HttpGhlClient` is the
production wiring; `FakeGhlClient` is used by tests to return a preloaded
location (or raise a preset error) and to capture the payload it was
called with, without an API call. This mirrors the PandaDoc client seam
in `bullet_api.pandadoc.client`.

Error model (S1-25 needs these split so the Inngest wrapper can decide
retriable vs not):

- `GhlClientError` (4xx) - bad payload, bad/expired auth, validation
  failure. Retrying the same request will not fix it, so the worker
  wraps this in `inngest.NonRetriableError`.
- `GhlServerError` (5xx / 429) - GHL is down, rate-limiting, or timing
  out. Transient; the worker lets it propagate so Inngest retries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from bullet_api.config import get_settings

# GoHighLevel REST base. The create-sub-account endpoint is
# POST {base}/locations/ and auth is `Authorization: Bearer <agency key>`
# plus a `Version` header that pins the API contract.
GHL_API_BASE_URL = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"


@dataclass(frozen=True)
class GhlLocation:
    """Minimal projection of a created GHL sub-account (location).

    `id` is the new sub-account id written back to `clients.ghl_subaccount_id`.
    `raw` keeps the full response body so the caller can persist it on the
    `platform_actions.response` JSONB column for auditing.
    """

    id: str
    name: str
    company_id: str
    raw: dict


class GhlError(Exception):
    """Base for all GoHighLevel API errors."""


class GhlClientError(GhlError):
    """A 4xx from GHL - bad payload, bad/expired auth, validation error.

    Non-retriable: the same request will keep failing, so the worker
    dead-letters via `inngest.NonRetriableError`.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"GHL returned {status_code}: {body}")


class GhlServerError(GhlError):
    """A 5xx or 429 from GHL - server error, rate-limit, or overload.

    Retriable: the worker lets this propagate so Inngest retries with
    backoff.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"GHL returned {status_code}: {body}")


class GhlClient(Protocol):
    async def create_location(self, payload: dict) -> GhlLocation:
        """Create a sub-account (location) under the agency.

        `payload` is the create-location body (`name`, `companyId`, and
        optional `phone` / `prospectInfo` / `snapshotId`). Returns the new
        location projected to id / name / company_id. Raises GhlClientError
        on 4xx and GhlServerError on 5xx/429.
        """
        ...

    async def find_location_by_email(self, email: str, *, company_id: str) -> GhlLocation | None:
        """Look up an existing sub-account (location) for `email` under the agency.

        Used by S1-26's returning-client check to avoid creating a duplicate
        sub-account: if a location already exists for the signed-document
        email, the caller reuses its id instead of POSTing a new one. This
        also closes S1-25's at-least-once duplicate-create window - a retry
        after a lost create response finds the orphaned location here rather
        than provisioning a second one.

        Returns the matching location (first hit) or None when no location
        exists for the email. "No match" is a normal answer, NOT an error.
        Raises GhlClientError on 4xx and GhlServerError on 5xx/429, same
        split as `create_location`.
        """
        ...


class HttpGhlClient:
    """Production client - POSTs the GHL create-location endpoint.

    Accepts an optional httpx transport so tests can inject a MockTransport
    without a network call (the only testability hook; production passes
    None and httpx uses its default transport).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = GHL_API_BASE_URL,
        version: str = GHL_API_VERSION,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._version = version
        self._timeout = timeout
        self._transport = transport

    async def create_location(self, payload: dict) -> GhlLocation:
        if not self._api_key:
            # Fail loudly rather than silently no-op, mirroring HttpPandaDocClient.
            raise RuntimeError(
                "GHL_AGENCY_API_KEY is empty; cannot create sub-account. "
                "Set it on the Render env group."
            )
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.post(
                f"{self._base_url}/locations/",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Version": self._version,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if 200 <= response.status_code < 300:
            body = response.json()
            return GhlLocation(
                id=str(body["id"]),
                name=body.get("name", ""),
                company_id=str(body.get("companyId", "")),
                raw=body,
            )
        # 429 (rate-limit) is retriable alongside 5xx; everything else in the
        # 4xx range is a non-retriable client error.
        if response.status_code == 429 or response.status_code >= 500:
            raise GhlServerError(response.status_code, response.text)
        raise GhlClientError(response.status_code, response.text)

    async def find_location_by_email(self, email: str, *, company_id: str) -> GhlLocation | None:
        if not self._api_key:
            raise RuntimeError(
                "GHL_AGENCY_API_KEY is empty; cannot look up sub-account. "
                "Set it on the Render env group."
            )
        # CONFIRM PRE-PROD: GHL agency location search shape. Best-guess is
        # GET /locations/search?companyId=&email=&limit=1 returning
        # {"locations": [...]}. No live GHL access yet, so the exact path,
        # query params, and response envelope are confirmed against the real
        # API before rollout - same deferred posture as `create_location`.
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(
                f"{self._base_url}/locations/search",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Version": self._version,
                },
                params={"companyId": company_id, "email": email, "limit": 1},
            )
        if 200 <= response.status_code < 300:
            body = response.json()
            locations = body.get("locations") or []
            if not locations:
                return None
            hit = locations[0]
            return GhlLocation(
                id=str(hit["id"]),
                name=hit.get("name", ""),
                company_id=str(hit.get("companyId", "")),
                raw=hit,
            )
        # A 404 on the search endpoint means "no such resource", which we
        # treat as "no existing location" rather than a hard error.
        if response.status_code == 404:
            return None
        if response.status_code == 429 or response.status_code >= 500:
            raise GhlServerError(response.status_code, response.text)
        raise GhlClientError(response.status_code, response.text)


@dataclass
class FakeGhlClient:
    """Test double.

    `create_location` returns `location` when set, or raises `error` when
    set (set exactly one). Records every create payload on `calls` so tests
    can assert on the request body (e.g. that `snapshotId` is present/absent).

    `find_location_by_email` returns `lookup_result` (default None = "no
    existing location", the common case so existing create-path tests
    proceed to create unchanged), or raises `lookup_error` when set. Records
    every lookup on `lookup_calls` as `(email, company_id)`.
    """

    location: GhlLocation | None = None
    error: Exception | None = None
    calls: list[dict] = field(default_factory=list)
    lookup_result: GhlLocation | None = None
    lookup_error: Exception | None = None
    lookup_calls: list[tuple[str, str]] = field(default_factory=list)

    async def create_location(self, payload: dict) -> GhlLocation:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        if self.location is None:
            raise AssertionError("FakeGhlClient has neither location nor error configured")
        return self.location

    async def find_location_by_email(self, email: str, *, company_id: str) -> GhlLocation | None:
        self.lookup_calls.append((email, company_id))
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.lookup_result


def get_ghl_client() -> GhlClient:
    """FastAPI / worker factory. Tests substitute a FakeGhlClient."""
    settings = get_settings()
    return HttpGhlClient(
        api_key=settings.ghl_agency_api_key,
        base_url=settings.ghl_api_base_url,
        version=settings.ghl_api_version,
    )
