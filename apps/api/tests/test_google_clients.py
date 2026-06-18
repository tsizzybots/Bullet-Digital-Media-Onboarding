"""Unit tests for the production HTTP Google clients (S1-27).

No DB, no network, no real Google credentials: every test drives the real
`HttpMeetClient` / `HttpCalendarClient` through an `httpx.MockTransport` with an
injected `token_provider`, the same pattern `test_pandadoc_client.py` /
`test_ghl_client.py` use. This exercises the response-parsing + pagination +
error-mapping branches that the Fake* doubles skip.
"""

from __future__ import annotations

import threading
import time

import httpx
import pytest

from bullet_api.google.calendar_client import CalendarEvent, HttpCalendarClient, _normalise_emails
from bullet_api.google.meet_client import GoogleApiNotFound, HttpMeetClient

TOKEN = "test-bearer-token"


def _meet(handler) -> HttpMeetClient:
    return HttpMeetClient(
        token_provider=lambda: TOKEN,
        base_url="https://meet.example.com",
        transport=httpx.MockTransport(handler),
    )


def _calendar(handler) -> HttpCalendarClient:
    return HttpCalendarClient(
        token_provider=lambda: TOKEN,
        base_url="https://cal.example.com",
        transport=httpx.MockTransport(handler),
    )


# --------------------------------------------------------------------------- #
# HttpMeetClient
# --------------------------------------------------------------------------- #


async def test_meet_conference_record_parses_space_dict_meeting_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(
            200,
            json={
                "name": "conferenceRecords/abc",
                "space": {"name": "spaces/s1", "meetingCode": "abc-defg-hij"},
                "startTime": "2026-06-10T10:00:00Z",
                "endTime": "2026-06-10T10:30:00Z",
            },
        )

    record = await _meet(handler).get_conference_record("conferenceRecords/abc")
    assert record.meeting_code == "abc-defg-hij"
    assert record.space_name == "spaces/s1"
    assert record.start_time == "2026-06-10T10:00:00Z"


async def test_meet_conference_record_space_as_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "conferenceRecords/abc", "space": "spaces/s1"})

    record = await _meet(handler).get_conference_record("conferenceRecords/abc")
    assert record.space_name == "spaces/s1"
    assert record.meeting_code is None


async def test_meet_404_raises_google_api_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(GoogleApiNotFound):
        await _meet(handler).get_conference_record("conferenceRecords/missing")


async def test_meet_5xx_raises_for_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    with pytest.raises(httpx.HTTPStatusError):
        await _meet(handler).get_conference_record("conferenceRecords/abc")


async def test_meet_transcript_entries_follow_pagination() -> None:
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        calls.append(token)
        if token is None:
            return httpx.Response(
                200,
                json={
                    "transcriptEntries": [{"participant": "Rep", "text": "Hello"}],
                    "nextPageToken": "PAGE2",
                },
            )
        return httpx.Response(
            200, json={"transcriptEntries": [{"participant": "Prospect", "text": "Hi"}]}
        )

    entries = await _meet(handler).fetch_transcript_entries("conferenceRecords/abc/transcripts/t1")
    assert [e.text for e in entries] == ["Hello", "Hi"]
    assert calls == [None, "PAGE2"]  # second page was fetched


async def test_meet_list_participants_maps_signed_in_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "participants": [
                    {"signedinUser": {"user": "users/42", "displayName": "Rep Name"}},
                    {"displayName": "Anon"},
                ]
            },
        )

    parts = await _meet(handler).list_participants("conferenceRecords/abc")
    assert parts[0].signedin_user_id == "users/42"
    assert parts[0].display_name == "Rep Name"
    assert parts[1].display_name == "Anon"


async def test_meet_empty_token_raises_runtimeerror() -> None:
    client = HttpMeetClient(token_provider=lambda: "", base_url="https://meet.example.com")
    with pytest.raises(RuntimeError, match="bearer token is empty"):
        await client.get_conference_record("conferenceRecords/abc")


# --------------------------------------------------------------------------- #
# HttpCalendarClient
# --------------------------------------------------------------------------- #


async def test_calendar_matches_on_conference_id_and_lowercases_emails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "abc-defg-hij"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "evt1",
                        "conferenceData": {"conferenceId": "abc-defg-hij"},
                        "attendees": [
                            {"email": "Prospect@Gym.com"},
                            {"email": "rep@bullet.com", "organizer": True},
                        ],
                    }
                ]
            },
        )

    event = await _calendar(handler).find_event_by_meeting_code(
        meeting_code="abc-defg-hij", time_min=None, time_max=None
    )
    assert event == CalendarEvent(event_id="evt1", attendee_emails=("prospect@gym.com",))


async def test_calendar_matches_on_entrypoint_uri() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "evt2",
                        "conferenceData": {
                            "conferenceId": "other",
                            "entryPoints": [{"uri": "https://meet.google.com/abc-defg-hij"}],
                        },
                        "attendees": [{"email": "p@gym.com"}],
                    }
                ]
            },
        )

    event = await _calendar(handler).find_event_by_meeting_code(
        meeting_code="abc-defg-hij", time_min=None, time_max=None
    )
    assert event is not None
    assert event.event_id == "evt2"


async def test_calendar_no_match_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"id": "x", "conferenceData": {}}]})

    event = await _calendar(handler).find_event_by_meeting_code(
        meeting_code="abc-defg-hij", time_min=None, time_max=None
    )
    assert event is None


async def test_calendar_empty_meeting_code_returns_none_without_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not hit the network for an empty meeting code")

    event = await _calendar(handler).find_event_by_meeting_code(
        meeting_code="", time_min=None, time_max=None
    )
    assert event is None


async def test_calendar_empty_token_raises_runtimeerror() -> None:
    client = HttpCalendarClient(token_provider=lambda: "", base_url="https://cal.example.com")
    with pytest.raises(RuntimeError, match="bearer token is empty"):
        await client.find_event_by_meeting_code(
            meeting_code="abc-defg-hij", time_min=None, time_max=None
        )


# --------------------------------------------------------------------------- #
# _normalise_emails (pure helper)
# --------------------------------------------------------------------------- #


def test_normalise_emails_drops_organiser_self_blanks_and_dedupes() -> None:
    out = _normalise_emails(
        [
            {"email": "A@B.com"},
            {"email": "rep@bullet.com", "organizer": True},
            {"email": "me@bullet.com", "self": True},
            {"email": ""},
            {"email": "a@b.com"},  # case-dup of the first
        ]
    )
    assert out == ("a@b.com",)


# --------------------------------------------------------------------------- #
# Bearer-token mint hygiene (S1-27 hardening, PR #8 review)
#
# The production `google_bearer_token` does a BLOCKING network token mint. These
# tests lock the two fixes: the mint must run OFF the event loop (so a slow mint
# cannot stall every other concurrent request/worker), and it must be bounded by
# a timeout (so a hung Google token endpoint cannot freeze the loop forever).
# --------------------------------------------------------------------------- #


async def test_meet_token_minted_off_the_event_loop() -> None:
    """The token_provider runs in a worker thread (via asyncio.to_thread), not on
    the event-loop thread, so a blocking mint never stalls the loop."""
    loop_thread = threading.get_ident()
    mint_thread: dict[str, int] = {}

    def token_provider() -> str:
        mint_thread["id"] = threading.get_ident()
        return TOKEN

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json={"name": "conferenceRecords/abc"})

    client = HttpMeetClient(
        token_provider=token_provider,
        base_url="https://meet.example.com",
        transport=httpx.MockTransport(handler),
    )
    record = await client.get_conference_record("conferenceRecords/abc")

    assert record.name == "conferenceRecords/abc"
    assert mint_thread["id"] != loop_thread  # minted off the event-loop thread


async def test_meet_token_mint_times_out_raises_runtimeerror() -> None:
    """A token mint that hangs past `token_timeout` raises a RuntimeError instead
    of blocking the loop indefinitely."""

    def slow_token() -> str:
        time.sleep(0.5)
        return TOKEN

    client = HttpMeetClient(
        token_provider=slow_token,
        base_url="https://meet.example.com",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        token_timeout=0.05,
    )
    with pytest.raises(RuntimeError, match="timed out"):
        await client.get_conference_record("conferenceRecords/abc")


async def test_calendar_token_mint_times_out_raises_runtimeerror() -> None:
    """Same timeout guard on the Calendar client's token mint."""

    def slow_token() -> str:
        time.sleep(0.5)
        return TOKEN

    client = HttpCalendarClient(
        token_provider=slow_token,
        base_url="https://cal.example.com",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"items": []})),
        token_timeout=0.05,
    )
    with pytest.raises(RuntimeError, match="timed out"):
        await client.find_event_by_meeting_code(
            meeting_code="abc-defg-hij", time_min=None, time_max=None
        )
