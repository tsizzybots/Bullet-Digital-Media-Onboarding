"""Google Meet REST API client abstraction (S1-27).

`MeetClient` is a Protocol so the worker depends on the interface, not on Google
specifically. `HttpMeetClient` is the production wiring (Meet REST v2 over
httpx, bearer auth from `credentials.google_bearer_token`); `FakeMeetClient`
returns preloaded data for tests.

The worker needs three reads off one conference record:
- `get_conference_record` -> the meeting window + the Meet space (whose meeting
  code links back to the Calendar invite the attendee emails come from).
- `fetch_transcript_entries` -> the spoken text, one entry per utterance.
- `list_participants` -> a secondary email signal for signed-in attendees.

Both `HttpMeetClient` and `FakeMeetClient` are testable without real Google: the
Http client takes an injectable `token_provider` + httpx `transport` (the same
hook `HttpPandaDocClient` uses), the Fake takes preloaded dicts. The exact JSON
field paths in `HttpMeetClient` are confirmed against the live API at the
pre-prod wiring step; the contract (the dataclasses below) is what the worker
codes against and is frozen here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import httpx

GOOGLE_MEET_API_BASE_URL = "https://meet.googleapis.com"
# Wall-clock bound on the (possibly blocking) off-loop bearer-token mint. The
# mint runs in a worker thread via `asyncio.to_thread`; `asyncio.wait_for`
# guarantees the event loop is never blocked beyond this even if Google's token
# endpoint hangs.
DEFAULT_TOKEN_TIMEOUT = 15.0


@dataclass(frozen=True)
class TranscriptEntry:
    """One utterance in a Meet transcript: who, what, and when it started."""

    participant: str
    text: str
    start_time: str | None = None


@dataclass(frozen=True)
class ConferenceRecord:
    """Minimal projection of a Meet conferenceRecord.

    `meeting_code` is the join code on the Meet space; it is the key that ties
    this conference back to the Calendar invite (via the event's
    `conferenceData`), which is where the prospect's email reliably lives.
    """

    name: str
    space_name: str | None = None
    meeting_code: str | None = None
    start_time: str | None = None
    end_time: str | None = None


@dataclass(frozen=True)
class MeetParticipant:
    """A conference participant. `signedin_user_id` is the opaque People API id
    Google exposes for signed-in users (NOT an email); resolving it to an email
    needs the Directory API and is a secondary signal behind the calendar
    invite. `email`, when a Fake/test supplies it, short-circuits that."""

    display_name: str | None = None
    signedin_user_id: str | None = None
    email: str | None = None


class GoogleApiNotFound(Exception):
    """Raised when the Meet API returns 404 for a resource name."""


class MeetClient(Protocol):
    async def get_conference_record(self, name: str) -> ConferenceRecord: ...

    async def fetch_transcript_entries(self, transcript_name: str) -> list[TranscriptEntry]: ...

    async def list_participants(self, conference_record_name: str) -> list[MeetParticipant]: ...


class HttpMeetClient:
    """Production Meet REST v2 client.

    `token_provider` defaults to the delegated service-account token; tests pass
    a lambda returning a static string + an httpx `MockTransport` so the client
    is exercised without real Google credentials.
    """

    def __init__(
        self,
        token_provider: Callable[[], str],
        base_url: str = GOOGLE_MEET_API_BASE_URL,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        token_timeout: float = DEFAULT_TOKEN_TIMEOUT,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport
        self._token_timeout = token_timeout

    async def _bearer_token(self) -> str:
        """Mint the bearer token OFF the event loop, bounded by a timeout.

        `token_provider` may do blocking network I/O (the production
        `google_bearer_token` mints over the network), so it runs in a worker
        thread via `asyncio.to_thread`; `asyncio.wait_for` bounds it so a hung
        token endpoint can never freeze the loop.
        """
        try:
            token = await asyncio.wait_for(
                asyncio.to_thread(self._token_provider), timeout=self._token_timeout
            )
        except TimeoutError as exc:
            raise RuntimeError(
                f"Google bearer-token mint timed out after {self._token_timeout}s; "
                "cannot call the Meet API."
            ) from exc
        if not token:
            raise RuntimeError(
                "Google bearer token is empty; cannot call the Meet API. "
                "Set GOOGLE_SERVICE_ACCOUNT_JSON / "
                "GOOGLE_WORKSPACE_IMPERSONATE_SUBJECT on the Render env group."
            )
        return token

    async def _get(self, path: str, params: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {await self._bearer_token()}"}
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(
                f"{self._base_url}/v2/{path}", headers=headers, params=params
            )
        if response.status_code == 404:
            raise GoogleApiNotFound(path)
        response.raise_for_status()
        return response.json()

    async def get_conference_record(self, name: str) -> ConferenceRecord:
        body = await self._get(name)
        space = body.get("space")
        # `space` may arrive as a resource name string or an expanded object.
        space_name = space if isinstance(space, str) else (space or {}).get("name")
        meeting_code = None
        if isinstance(space, dict):
            meeting_code = space.get("meetingCode") or (space.get("config") or {}).get(
                "meetingCode"
            )
        return ConferenceRecord(
            name=body.get("name", name),
            space_name=space_name,
            meeting_code=meeting_code,
            start_time=body.get("startTime"),
            end_time=body.get("endTime"),
        )

    async def fetch_transcript_entries(self, transcript_name: str) -> list[TranscriptEntry]:
        entries: list[TranscriptEntry] = []
        page_token: str | None = None
        # The Meet API paginates transcript entries; follow nextPageToken so a
        # long call is captured in full, not just the first page.
        while True:
            params = {"pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            body = await self._get(f"{transcript_name}/entries", params=params)
            for raw in body.get("transcriptEntries", []):
                entries.append(
                    TranscriptEntry(
                        participant=raw.get("participant", ""),
                        text=raw.get("text", ""),
                        start_time=raw.get("startTime"),
                    )
                )
            page_token = body.get("nextPageToken")
            if not page_token:
                return entries

    async def list_participants(self, conference_record_name: str) -> list[MeetParticipant]:
        participants: list[MeetParticipant] = []
        page_token: str | None = None
        while True:
            params = {"pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            body = await self._get(f"{conference_record_name}/participants", params=params)
            for raw in body.get("participants", []):
                signedin = raw.get("signedinUser") or {}
                participants.append(
                    MeetParticipant(
                        display_name=signedin.get("displayName") or raw.get("displayName"),
                        signedin_user_id=signedin.get("user"),
                    )
                )
            page_token = body.get("nextPageToken")
            if not page_token:
                return participants


@dataclass
class FakeMeetClient:
    """Test double. Returns preloaded data keyed by resource name; raises
    `GoogleApiNotFound` for unknown conference records. `error`, when set, is
    raised by every method to exercise transport-level failures that are NOT a
    typed 404."""

    conference_records: dict[str, ConferenceRecord] = field(default_factory=dict)
    transcript_entries: dict[str, list[TranscriptEntry]] = field(default_factory=dict)
    participants: dict[str, list[MeetParticipant]] = field(default_factory=dict)
    error: Exception | None = None

    async def get_conference_record(self, name: str) -> ConferenceRecord:
        if self.error is not None:
            raise self.error
        try:
            return self.conference_records[name]
        except KeyError:
            raise GoogleApiNotFound(name) from None

    async def fetch_transcript_entries(self, transcript_name: str) -> list[TranscriptEntry]:
        if self.error is not None:
            raise self.error
        return self.transcript_entries.get(transcript_name, [])

    async def list_participants(self, conference_record_name: str) -> list[MeetParticipant]:
        if self.error is not None:
            raise self.error
        return self.participants.get(conference_record_name, [])


__all__ = [
    "GOOGLE_MEET_API_BASE_URL",
    "ConferenceRecord",
    "FakeMeetClient",
    "GoogleApiNotFound",
    "HttpMeetClient",
    "MeetClient",
    "MeetParticipant",
    "TranscriptEntry",
]
