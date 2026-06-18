"""Service-account credentials for the Google Meet + Calendar REST calls.

Reading another user's Meet transcripts and Calendar events requires a service
account with **domain-wide delegation** impersonating a Workspace user (the
meeting organiser or an admin mailbox). `google_bearer_token()` mints a short
lived OAuth access token for the configured subject + scopes.

Fails loudly (RuntimeError) when the service-account JSON or the impersonation
subject is unset, mirroring `get_s3_client()` / `HttpPandaDocClient` - a
mis-configured deployment is loud, never silently unauthenticated. This module
is production-only; tests inject a fake token provider into the Http clients (or
use the Fake clients), so `google-auth` is never exercised under test.
"""

from __future__ import annotations

import json
import threading

from google.auth.transport.requests import Request
from google.oauth2 import service_account

from bullet_api.config import get_settings

# Read-only scopes: transcripts/participants on Meet, attendee emails on
# Calendar. `meetings.space.readonly` covers the conferenceRecords transcript +
# participant reads; `calendar.readonly` covers the invite lookup.
GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
)

# Process-cached delegated credentials. Built once and reused so `google-auth`'s
# own token cache survives between calls: previously a brand-new Credentials was
# constructed on every HTTP request (4-6+ per transcript capture), so the SA JSON
# was re-parsed and a fresh token minted each time, defeating the cache. The lock
# guards the one-time build AND serialises refreshes, so concurrent worker
# threads coalesce onto a single in-flight token mint instead of each minting
# their own.
_credentials_lock = threading.Lock()
_cached_credentials: service_account.Credentials | None = None


def _service_account_credentials() -> service_account.Credentials:
    """Build delegated service-account credentials from settings.

    Raises RuntimeError when the SA JSON or the impersonation subject is empty
    (an unconfigured deploy) so the failure is loud at first real use.
    """
    settings = get_settings()
    if not settings.google_service_account_json:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is empty; cannot authenticate to "
            "Google Meet / Calendar. Set it on the Render env group."
        )
    if not settings.google_workspace_impersonate_subject:
        raise RuntimeError(
            "GOOGLE_WORKSPACE_IMPERSONATE_SUBJECT is empty; domain-wide "
            "delegation needs a subject to impersonate. Set it on the Render "
            "env group."
        )
    info = json.loads(settings.google_service_account_json)
    return service_account.Credentials.from_service_account_info(
        info,
        scopes=list(GOOGLE_SCOPES),
        subject=settings.google_workspace_impersonate_subject,
    )


def _delegated_credentials() -> service_account.Credentials:
    """Return the process-cached delegated credentials, building them once."""
    global _cached_credentials
    if _cached_credentials is None:
        with _credentials_lock:
            if _cached_credentials is None:
                _cached_credentials = _service_account_credentials()
    return _cached_credentials


def google_bearer_token() -> str:
    """Return a valid OAuth bearer token for the delegated subject.

    SYNCHRONOUS and may perform a blocking network token mint (`google-auth`'s
    JWT-bearer grant), so callers running on the asyncio event loop MUST offload
    it - the Http* clients invoke this via `asyncio.to_thread` bounded by
    `asyncio.wait_for` so a hung Google token endpoint can never freeze the loop.

    Refreshes only when the cached token is missing/expired; `google-auth` tracks
    expiry on the cached credentials, and the lock coalesces concurrent refreshes
    onto a single network round-trip.
    """
    creds = _delegated_credentials()
    with _credentials_lock:
        if not creds.valid:
            creds.refresh(Request())
        return creds.token


__all__ = ["GOOGLE_SCOPES", "google_bearer_token"]
