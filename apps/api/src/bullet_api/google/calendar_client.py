"""Google Calendar API client abstraction (S1-27).

The prospect's email reliably reaches the meeting as a Calendar **invite
attendee** (John, 17/06/2026: booked via HubSpot or Google Calendar, "always
via email," "never send Google invite links"). The Meet participant list does
not expose a clean email, so the calendar invite is the auto-link match key.

`CalendarClient.find_event_by_meeting_code` locates the invite for a Meet
conference by its join code within the meeting's time window and returns the
attendee emails (lowercased, organiser excluded). `HttpCalendarClient` is the
production wiring (Calendar v3 over httpx, bearer auth); `FakeCalendarClient` is
the test double. Both are testable without real Google (injectable
`token_provider` + httpx `transport` / preloaded dict). The exact field paths in
the Http client are confirmed against the live API at the pre-prod wiring step;
the returned `CalendarEvent` contract is what the worker codes against.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import httpx

GOOGLE_CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"


@dataclass(frozen=True)
class CalendarEvent:
    """The invite behind a Meet conference. `attendee_emails` is lowercased and
    excludes the organiser (the rep), leaving the prospect side as the match
    candidates."""

    event_id: str
    attendee_emails: tuple[str, ...] = ()


def _normalise_emails(attendees: list[dict]) -> tuple[str, ...]:
    """Lowercase, drop the organiser/self, and de-dupe attendee emails.

    Lowercased so the jsonb match key in `sales_call_transcripts` (byte-exact
    containment) lines up with the lowercased `clients.email` used at signing.
    """
    seen: list[str] = []
    for attendee in attendees:
        email = (attendee.get("email") or "").strip().lower()
        if not email:
            continue
        # `self` is the impersonated organiser mailbox; `organizer` is the rep.
        # Neither is the prospect, so they never become a match key.
        if attendee.get("self") or attendee.get("organizer"):
            continue
        if email not in seen:
            seen.append(email)
    return tuple(seen)


class CalendarClient(Protocol):
    async def find_event_by_meeting_code(
        self, *, meeting_code: str, time_min: str | None, time_max: str | None
    ) -> CalendarEvent | None: ...


class HttpCalendarClient:
    """Production Calendar v3 client over the impersonated subject's calendar.

    Searches the primary calendar with `q=<meeting_code>` constrained to the
    meeting window and returns the first event that carries a matching
    `conferenceData` entry point. Returns None when nothing matches (the worker
    then parks the transcript unlinked for the manual fallback).
    """

    def __init__(
        self,
        token_provider: Callable[[], str],
        calendar_id: str = "primary",
        base_url: str = GOOGLE_CALENDAR_API_BASE_URL,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._calendar_id = calendar_id
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        token = self._token_provider()
        if not token:
            raise RuntimeError(
                "Google bearer token is empty; cannot call the Calendar API. "
                "Set GOOGLE_SERVICE_ACCOUNT_JSON / "
                "GOOGLE_WORKSPACE_IMPERSONATE_SUBJECT on the Render env group."
            )
        return {"Authorization": f"Bearer {token}"}

    async def find_event_by_meeting_code(
        self, *, meeting_code: str, time_min: str | None, time_max: str | None
    ) -> CalendarEvent | None:
        if not meeting_code:
            return None
        params: dict[str, str] = {"q": meeting_code, "singleEvents": "true", "maxResults": "10"}
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(
                f"{self._base_url}/calendars/{self._calendar_id}/events",
                headers=self._headers(),
                params=params,
            )
        response.raise_for_status()
        for event in response.json().get("items", []):
            conference = event.get("conferenceData") or {}
            code = conference.get("conferenceId")
            entry_matches = any(
                meeting_code in (ep.get("uri") or "") for ep in conference.get("entryPoints", [])
            )
            if code == meeting_code or entry_matches:
                return CalendarEvent(
                    event_id=event.get("id", ""),
                    attendee_emails=_normalise_emails(event.get("attendees", [])),
                )
        return None


@dataclass
class FakeCalendarClient:
    """Test double. Maps a meeting code to a preloaded `CalendarEvent`; returns
    None for unknown codes. `error`, when set, is raised to exercise
    transport-level failures."""

    events_by_meeting_code: dict[str, CalendarEvent] = field(default_factory=dict)
    error: Exception | None = None

    async def find_event_by_meeting_code(
        self, *, meeting_code: str, time_min: str | None, time_max: str | None
    ) -> CalendarEvent | None:
        if self.error is not None:
            raise self.error
        return self.events_by_meeting_code.get(meeting_code)


__all__ = [
    "GOOGLE_CALENDAR_API_BASE_URL",
    "CalendarClient",
    "CalendarEvent",
    "FakeCalendarClient",
    "HttpCalendarClient",
]
