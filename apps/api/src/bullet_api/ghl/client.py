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
- `GhlNotConfiguredError` - the agency API key is empty, so no call is
  possible. Non-retriable. It has its own type rather than a bare
  `RuntimeError` because `httpx.StreamError` IS a `RuntimeError`, so a
  broad catch dead-lettered recoverable transport failures alongside it.
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

# 4xx codes that are transient rather than structural, so the worker retries
# instead of dead-lettering:
#
# - 408 request timeout: the server saying "you took too long". The identical
#   request can succeed next time.
# - 429 rate-limit: explicitly a "try again later".
# - 401 / 403 auth: a rotated or briefly-unavailable agency key. Dead-lettering
#   these was the sharpest edge here - one key blip would TERMINALLY fail every
#   signing in flight, and each needs a human to re-drive. A genuinely wrong key
#   still dead-letters, just via Inngest's retry budget instead of instantly, so
#   retrying costs a few minutes of backoff and buys back the whole blip case.
#
# Everything else in the 4xx range means the request itself is wrong and will
# keep failing, so it stays non-retriable.
_RETRIABLE_STATUS = frozenset({401, 403, 408, 429})


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


# Longest raw response body allowed into an exception MESSAGE. `.body` keeps
# the full text for callers; only the human-readable message is clipped.
# Uncapped, a 502 HTML page or a large JSON error becomes a multi-KB exception
# string that `scrub_event` then regex-walks inline on the event loop - and
# `_EMAIL_RE` is quadratic on a long unbroken token run (review round 5
# measured ~1.9s for a 20 KB unbroken body). Sentry's `max_value_length` caps
# it a second time; this caps it at the source.
_MAX_ERROR_BODY_CHARS = 512


def _clip(body: str) -> str:
    if len(body) <= _MAX_ERROR_BODY_CHARS:
        return body
    return f"{body[:_MAX_ERROR_BODY_CHARS]}... [{len(body)} chars total]"


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
        super().__init__(f"GHL returned {status_code}: {_clip(body)}")


class GhlNotConfiguredError(GhlError, RuntimeError):
    """The agency API key is empty, so no call can be made at all.

    Non-retriable - retrying an unset env var achieves nothing - but it needs
    its own type rather than a bare `RuntimeError` (review round 5). The worker
    caught `RuntimeError` to dead-letter this case, and **`httpx.StreamError`
    is a subclass of `RuntimeError`**, so a transport-level streaming failure
    on a perfectly recoverable signing was being dead-lettered alongside it.

    Still inherits `RuntimeError` so any caller that catches the old type keeps
    working; the worker now catches THIS, which `httpx.StreamError` is not.
    """


class GhlServerError(GhlError):
    """A 5xx or 429 from GHL - server error, rate-limit, or overload.

    Retriable: the worker lets this propagate so Inngest retries with
    backoff.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"GHL returned {status_code}: {_clip(body)}")


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
        was ALSO meant to close S1-25's at-least-once duplicate-create window,
        but does NOT (S1-26d): the search is eventually consistent, so a create
        whose response was lost and is retried seconds later cannot see its own
        orphan and provisions a second location anyway. That window is OPEN.

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
            raise GhlNotConfiguredError(
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
            # GUARDED (round 12, P2): a 2xx with an unexpected shape used to
            # raise AFTER the location exists in GHL - and deterministically,
            # so every Inngest retry re-POSTed and re-failed, minting one more
            # orphan per attempt. A shape mismatch cannot heal on retry, so it
            # maps to the NON-retriable error: one orphan at most, a visible
            # failed action recording the body, and a human decides.
            try:
                body = response.json()
                location_id = str(body["id"])
            except (ValueError, KeyError, TypeError) as exc:
                raise GhlClientError(
                    response.status_code,
                    "2xx create-location response could not be parsed "
                    f"({type(exc).__name__}); a location LIKELY EXISTS in GHL that no "
                    f"client row records - reconcile before retrying: {response.text[:500]}",
                ) from exc
            return GhlLocation(
                id=location_id,
                name=body.get("name", ""),
                company_id=str(body.get("companyId", "")),
                raw=body,
            )
        # 408 (request timeout) and 429 (rate-limit) are retriable alongside
        # 5xx; everything else in the 4xx range is a non-retriable client error.
        # 408 matters because it is a TRANSIENT server-side condition wearing a
        # 4xx code - dead-lettering it would terminally fail a signing that
        # would have succeeded on the next attempt.
        if response.status_code in _RETRIABLE_STATUS or response.status_code >= 500:
            raise GhlServerError(response.status_code, response.text)
        raise GhlClientError(response.status_code, response.text)

    async def find_location_by_email(self, email: str, *, company_id: str) -> GhlLocation | None:
        if not self._api_key:
            raise GhlNotConfiguredError(
                "GHL_AGENCY_API_KEY is empty; cannot look up sub-account. "
                "Set it on the Render env group."
            )
        # VERIFIED LIVE against Bullet's agency: the path, query params and the
        # `{"locations": [...], "traceId": ...}` envelope all hold (21/07/2026
        # read-only probe, then again in the 30/07 end-to-end Chain 1 run). The
        # earlier CONFIRM PRE-PROD marker is cleared.
        #
        # KNOWN CAVEAT (S1-26d, found 30/07): this search is EVENTUALLY
        # CONSISTENT. A location created seconds ago is not yet findable by
        # email even though GET by id returns it with that exact address, while
        # a long-established location resolves fine. So this is a reliable
        # lookup for a genuinely returning client and an UNRELIABLE backstop for
        # the at-least-once window it was also meant to cover (a create whose
        # response was lost, retried immediately, will not find its own
        # orphan). Callers must not treat a `None` as proof no location exists.
        #
        # `limit=1` means a business with several locations returns an arbitrary
        # one; the caller corroborates the hit on name, postcode and phone
        # (vetoing on a divergent address) before reusing
        # it, so an unrelated hit is flagged rather than merged.
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
        # Same retriable set as `create_location` (review round 4, finding 1):
        # this lookup runs before the create on every attempt, so a key blip
        # here has to be retriable too, or the create-path fix is unreachable
        # - every in-flight signing dead-letters at the lookup before it ever
        # gets to the POST that would have survived the blip.
        if response.status_code in _RETRIABLE_STATUS or response.status_code >= 500:
            raise GhlServerError(response.status_code, response.text)
        raise GhlClientError(response.status_code, response.text)


class _Unset:
    """Sentinel for "the test did not say what the GHL lookup returns".

    `lookup_result` used to default to `None`, meaning "no existing location".
    That default silently disarmed the returning-client email leg in 27 of the
    GHL tests, so an entire production code path was invisible to the suite -
    which is how a franchise-merging bug shipped with a green test that could
    never have caught it (review rounds 1 and 2 both landed on an instance of
    this). Requiring an explicit value makes the omission a loud failure
    instead of a silent pass; tests that genuinely want "no existing location"
    pass `lookup_result=None`.
    """


_UNSET = _Unset()


@dataclass
class FakeGhlClient:
    """Test double.

    `create_location` returns `location` when set, or raises `error` when
    set (set exactly one). Records every create payload on `calls` so tests
    can assert on the request body (e.g. that `snapshotId` is present/absent).

    `find_location_by_email` returns `lookup_result` when set to a value, or
    raises `lookup_error` when set. Records every lookup on `lookup_calls` as
    `(email, company_id)`.

    `lookup_result` defaults to `_UNSET` (fixed docstring, review round 4:
    this used to say it defaulted to `None`, which was true before the
    `_UNSET` sentinel above replaced that default - see its docstring for
    why). An unset call raises `AssertionError` rather than silently
    behaving as "no existing location"; pass `lookup_result=None` explicitly
    for that case.
    """

    location: GhlLocation | None = None
    error: Exception | None = None
    calls: list[dict] = field(default_factory=list)
    lookup_result: GhlLocation | None | _Unset = _UNSET
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
        if isinstance(self.lookup_result, _Unset):
            raise AssertionError(
                "FakeGhlClient.find_location_by_email was called but the test never "
                "said what it returns. This leg decides whether an existing GHL "
                "sub-account is REUSED, so leaving it implicit hides the reuse path "
                "entirely - exactly how a franchise-merging bug shipped green twice. "
                "Pass lookup_result=None for 'no existing location', or a GhlLocation "
                "for a hit."
            )
        return self.lookup_result


def get_ghl_client() -> GhlClient:
    """FastAPI / worker factory. Tests substitute a FakeGhlClient."""
    settings = get_settings()
    return HttpGhlClient(
        api_key=settings.ghl_agency_api_key,
        base_url=settings.ghl_api_base_url,
        version=settings.ghl_api_version,
    )
