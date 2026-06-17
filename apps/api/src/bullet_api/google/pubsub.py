"""Verify + parse the Google Pub/Sub push that delivers Meet transcript events.

Google Meet has no Zoom-style "recording-done" webhook. Instead a Google
Workspace Events subscription publishes Meet events to a Cloud Pub/Sub topic,
and a **push subscription** POSTs them to `/webhooks/google-meet`. Two jobs
here:

1. **Verify** the push is really from Google before any DB write. Google signs
   the push with an OIDC token (Authorization: Bearer) whose `aud` is the
   configured push audience and whose `email` is the push subscription's
   service account. `verify_pubsub_push` checks both; an empty audience / SA
   email in config makes it fail closed (the receiver returns 401), mirroring
   the PandaDoc empty-secret 401.

2. **Parse** the envelope into a `MeetTranscriptEvent`. The CloudEvents type
   lives in the Pub/Sub message attributes (`ce-type`); only the
   transcript-file-generated type is actionable - everything else (recording
   events, participant joins, conference lifecycle) is ignored. The transcript
   resource name (`conferenceRecords/{c}/transcripts/{t}`) is the idempotency
   key and the handle the worker fetches against.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass

from google.auth import exceptions as google_auth_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

# CloudEvents type Google sets for "the transcript file is ready". Recording and
# other Meet events arrive at the same endpoint and are ignored.
MEET_TRANSCRIPT_READY_CE_TYPE = "google.workspace.meet.transcript.v2.fileGenerated"


@dataclass(frozen=True)
class MeetTranscriptEvent:
    """A parsed, actionable transcript-ready event.

    `transcript_name` is the full Meet resource id and the idempotency key;
    `conference_record_name` is its parent, used to read the meeting window +
    participants.
    """

    transcript_name: str
    conference_record_name: str


def verify_pubsub_push(token: str, *, audience: str, sa_email: str) -> bool:
    """Return True iff `token` is a valid Google OIDC token for our push.

    Fails closed: an empty token, an empty configured `audience`/`sa_email`, a
    bad signature, a wrong audience, or a mismatched `email` claim all return
    False (the receiver turns that into 401). `verify_oauth2_token` checks the
    signature + expiry + audience against Google's public certs.
    """
    if not token or not audience or not sa_email:
        return False
    try:
        claims = id_token.verify_oauth2_token(token, google_requests.Request(), audience=audience)
    except (ValueError, google_auth_exceptions.GoogleAuthError):
        return False
    if claims.get("email") != sa_email:
        return False
    # Google only sets email_verified on a verified service-account identity.
    return bool(claims.get("email_verified", False))


def _conference_record_of(transcript_name: str) -> str | None:
    """Derive `conferenceRecords/{c}` from `conferenceRecords/{c}/transcripts/{t}`.

    Returns None for a name that is not the expected transcript shape so a
    malformed payload is ignored rather than producing a bad parent.
    """
    marker = "/transcripts/"
    if marker not in transcript_name:
        return None
    parent = transcript_name.split(marker, 1)[0]
    return parent if parent.startswith("conferenceRecords/") else None


def parse_workspace_event(envelope: dict) -> MeetTranscriptEvent | None:
    """Parse a Pub/Sub push envelope into a `MeetTranscriptEvent`, or None.

    Returns None (the receiver acks 200 + ignores) for any non-transcript event
    or a payload missing a well-formed transcript resource name. Raises
    ValueError only when the envelope itself is structurally unreadable (bad
    base64 / non-JSON data), which the receiver maps to 400.
    """
    message = envelope.get("message")
    if not isinstance(message, dict):
        return None

    attributes = message.get("attributes") or {}
    if attributes.get("ce-type") != MEET_TRANSCRIPT_READY_CE_TYPE:
        return None

    raw = message.get("data")
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw)
        payload = json.loads(decoded)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Pub/Sub message data is not base64-encoded JSON") from exc

    # The resource name can arrive a couple of ways depending on the event
    # shape; accept the documented `transcript.name` and a top-level `name`.
    transcript = payload.get("transcript")
    transcript_name = ""
    if isinstance(transcript, dict):
        transcript_name = transcript.get("name", "")
    if not transcript_name and isinstance(payload.get("name"), str):
        candidate = payload["name"]
        if "/transcripts/" in candidate:
            transcript_name = candidate
    if not transcript_name:
        return None

    conference_record_name = _conference_record_of(transcript_name)
    if conference_record_name is None:
        return None

    return MeetTranscriptEvent(
        transcript_name=transcript_name,
        conference_record_name=conference_record_name,
    )


__all__ = [
    "MEET_TRANSCRIPT_READY_CE_TYPE",
    "MeetTranscriptEvent",
    "parse_workspace_event",
    "verify_pubsub_push",
]
