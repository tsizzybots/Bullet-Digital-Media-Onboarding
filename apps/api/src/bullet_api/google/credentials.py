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


def google_bearer_token() -> str:
    """Return a fresh OAuth bearer token for the delegated subject.

    `google-auth` handles the JWT-bearer grant + token caching internally; we
    refresh on demand and hand the token string to the Http clients as the
    `Authorization: Bearer <token>` header value.
    """
    creds = _service_account_credentials()
    creds.refresh(Request())
    return creds.token


__all__ = ["GOOGLE_SCOPES", "google_bearer_token"]
