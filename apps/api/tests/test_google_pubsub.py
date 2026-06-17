"""Unit tests for the Google Pub/Sub verify + Workspace-Event parse (S1-27).

Pure functions, no DB and no network: the OIDC verification is exercised by
monkeypatching `id_token.verify_oauth2_token` (the only Google call), so the
fail-closed branches and the claim checks are covered without real Google certs.
"""

from __future__ import annotations

import base64
import json

from bullet_api.google import pubsub
from bullet_api.google.pubsub import (
    MEET_TRANSCRIPT_READY_CE_TYPE,
    parse_workspace_event,
    verify_pubsub_push,
)


def _envelope(ce_type: str, payload: dict) -> dict:
    return {
        "message": {
            "attributes": {"ce-type": ce_type},
            "data": base64.b64encode(json.dumps(payload).encode()).decode(),
        }
    }


# --------------------------------------------------------------------------- #
# parse_workspace_event
# --------------------------------------------------------------------------- #


def test_parses_transcript_event_from_transcript_name() -> None:
    env = _envelope(
        MEET_TRANSCRIPT_READY_CE_TYPE,
        {"transcript": {"name": "conferenceRecords/abc/transcripts/xyz"}},
    )
    event = parse_workspace_event(env)
    assert event is not None
    assert event.transcript_name == "conferenceRecords/abc/transcripts/xyz"
    assert event.conference_record_name == "conferenceRecords/abc"


def test_parses_transcript_event_from_top_level_name() -> None:
    env = _envelope(
        MEET_TRANSCRIPT_READY_CE_TYPE,
        {"name": "conferenceRecords/c1/transcripts/t1"},
    )
    event = parse_workspace_event(env)
    assert event is not None
    assert event.conference_record_name == "conferenceRecords/c1"


def test_non_transcript_event_type_is_ignored() -> None:
    env = _envelope(
        "google.workspace.meet.recording.v2.fileGenerated",
        {"recording": {"name": "conferenceRecords/abc/recordings/r1"}},
    )
    assert parse_workspace_event(env) is None


def test_transcript_event_without_resource_name_is_ignored() -> None:
    env = _envelope(MEET_TRANSCRIPT_READY_CE_TYPE, {"unrelated": "payload"})
    assert parse_workspace_event(env) is None


def test_malformed_resource_name_is_ignored() -> None:
    env = _envelope(
        MEET_TRANSCRIPT_READY_CE_TYPE,
        {"transcript": {"name": "spaces/abc/transcripts/xyz"}},  # not a conferenceRecords parent
    )
    assert parse_workspace_event(env) is None


def test_missing_message_is_ignored() -> None:
    assert parse_workspace_event({}) is None


def test_bad_base64_data_raises_valueerror() -> None:
    env = {
        "message": {
            "attributes": {"ce-type": MEET_TRANSCRIPT_READY_CE_TYPE},
            "data": "!!!not base64!!!",
        }
    }
    try:
        parse_workspace_event(env)
    except ValueError:
        pass
    else:  # pragma: no cover - the assertion below fires instead
        raise AssertionError("expected ValueError for bad base64 data")


# --------------------------------------------------------------------------- #
# verify_pubsub_push (fail-closed + claim checks)
# --------------------------------------------------------------------------- #

GOOD_AUD = "https://api.example.com/webhooks/google-meet"
GOOD_SA = "pubsub-push@project.iam.gserviceaccount.com"


def test_verify_fails_closed_on_empty_inputs() -> None:
    assert verify_pubsub_push("", audience=GOOD_AUD, sa_email=GOOD_SA) is False
    assert verify_pubsub_push("tok", audience="", sa_email=GOOD_SA) is False
    assert verify_pubsub_push("tok", audience=GOOD_AUD, sa_email="") is False


def test_verify_accepts_valid_token(monkeypatch) -> None:
    monkeypatch.setattr(
        pubsub.id_token,
        "verify_oauth2_token",
        lambda token, request, audience=None: {"email": GOOD_SA, "email_verified": True},
    )
    assert verify_pubsub_push("tok", audience=GOOD_AUD, sa_email=GOOD_SA) is True


def test_verify_rejects_wrong_service_account(monkeypatch) -> None:
    monkeypatch.setattr(
        pubsub.id_token,
        "verify_oauth2_token",
        lambda token, request, audience=None: {
            "email": "attacker@evil.com",
            "email_verified": True,
        },
    )
    assert verify_pubsub_push("tok", audience=GOOD_AUD, sa_email=GOOD_SA) is False


def test_verify_rejects_unverified_email(monkeypatch) -> None:
    monkeypatch.setattr(
        pubsub.id_token,
        "verify_oauth2_token",
        lambda token, request, audience=None: {"email": GOOD_SA, "email_verified": False},
    )
    assert verify_pubsub_push("tok", audience=GOOD_AUD, sa_email=GOOD_SA) is False


def test_verify_rejects_when_google_raises(monkeypatch) -> None:
    def _raise(token, request, audience=None):
        raise ValueError("bad signature")

    monkeypatch.setattr(pubsub.id_token, "verify_oauth2_token", _raise)
    assert verify_pubsub_push("tok", audience=GOOD_AUD, sa_email=GOOD_SA) is False
