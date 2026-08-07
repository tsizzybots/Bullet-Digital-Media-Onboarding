"""Unit tests for `bullet_api.observability.sentry`.

Pure functions only - no database, no FastAPI app import. These tests pin
the shared PII-scrubbing contract (denylist keys, free-text regexes, the
`[Filtered]` sentinel) so it can never silently drift from the dashboard
slice, and they assert that an empty DSN is a true no-op.
"""

from __future__ import annotations

import json
import types

import pytest
import sentry_sdk

from bullet_api.observability.sentry import (
    DENYLIST_KEYS,
    init_sentry,
    scrub_event,
)

FILTERED = "[Filtered]"


def _dumps(event: object) -> str:
    """Serialise a scrubbed event to JSON so we can assert no raw PII
    substring survives anywhere in the structure (including nested dicts,
    lists, and tuples-coerced-to-lists)."""
    return json.dumps(event, default=str)


def test_email_scrubbed_in_every_event_location() -> None:
    """An email appearing in the message, the exception value, a stack-frame
    `vars` dict, and `request.data` must all be redacted; no raw
    `@example.com` substring may survive."""
    raw_email = "victim@example.com"
    event = {
        "logentry": {"message": f"login failed for {raw_email}"},
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": f"bad address {raw_email}",
                    "stacktrace": {
                        "frames": [
                            {
                                "function": "handle",
                                "vars": {"user_input": raw_email},
                            }
                        ]
                    },
                }
            ]
        },
        "request": {"data": {"note": f"contact {raw_email} asap"}},
    }

    scrubbed = scrub_event(event, {})
    serialized = _dumps(scrubbed)

    assert raw_email not in serialized
    assert "@example.com" not in serialized
    assert FILTERED in scrubbed["logentry"]["message"]
    assert FILTERED in scrubbed["exception"]["values"][0]["value"]
    # Structural message stays readable - only the email is redacted.
    assert scrubbed["exception"]["values"][0]["value"].startswith("bad address ")
    frame_vars = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    assert frame_vars["user_input"] == FILTERED
    assert FILTERED in scrubbed["request"]["data"]["note"]


def test_phone_number_redacted() -> None:
    event = {"logentry": {"message": "call the lead on +44 7700 900123 today"}}
    scrubbed = scrub_event(event, {})
    serialized = _dumps(scrubbed)
    assert "900123" not in serialized
    assert "+44 7700 900123" not in serialized
    assert FILTERED in scrubbed["logentry"]["message"]


def test_pandadoc_signed_url_redacted() -> None:
    """A PandaDoc share / signed URL must be removed wholesale (the host
    match alone is enough, the token param doubly so)."""
    url = "https://app.pandadoc.com/s/abc?token=xyz"
    event = {"extra": {"link": f"see {url} to sign"}}
    scrubbed = scrub_event(event, {})
    serialized = _dumps(scrubbed)
    assert "pandadoc.com" not in serialized
    assert "token=xyz" not in serialized
    assert FILTERED in scrubbed["extra"]["link"]


def test_signed_url_with_token_param_redacted_non_pandadoc() -> None:
    """A non-PandaDoc URL still gets redacted when it carries a signing
    query param (R2 signed URL pattern)."""
    url = "https://r2.example.net/file.pdf?x-amz-signature=deadbeef&expires=123"
    event = {"extra": {"asset": url}}
    scrubbed = scrub_event(event, {})
    serialized = _dumps(scrubbed)
    assert "x-amz-signature" not in serialized
    assert scrubbed["extra"]["asset"] == FILTERED


def test_denylisted_keys_replaced_wholesale() -> None:
    """Denylisted keys have their entire value replaced, even when the value
    is a non-string (dict / list) that the regexes would never touch."""
    event = {
        "extra": {
            "transcript": "the entire sales call narrative, names and all",
            "payload": {"nested": "anything", "deep": [1, 2, 3]},
            "full_name": "Jane Founder",
            "authorization": "Bearer sometokenvalue",
        }
    }
    scrubbed = scrub_event(event, {})
    extra = scrubbed["extra"]
    assert extra["transcript"] == FILTERED
    assert extra["payload"] == FILTERED
    assert extra["full_name"] == FILTERED
    assert extra["authorization"] == FILTERED
    # The denylisted payload value is gone entirely - none of its nested
    # content leaks into the serialized event.
    serialized = _dumps(scrubbed)
    assert "sales call narrative" not in serialized
    assert "sometokenvalue" not in serialized


def test_denylist_is_case_insensitive() -> None:
    event = {"extra": {"Email": "a@b.com", "PAYLOAD": {"x": 1}, "Set-Cookie": "sid=abc"}}
    scrubbed = scrub_event(event, {})
    assert scrubbed["extra"]["Email"] == FILTERED
    assert scrubbed["extra"]["PAYLOAD"] == FILTERED
    assert scrubbed["extra"]["Set-Cookie"] == FILTERED


def test_non_pii_is_preserved() -> None:
    """Error type names, URL paths, and the human-readable exception message
    must survive (only embedded PII inside them is redacted) so events stay
    useful for debugging.

    `value` IS a denylisted key, but it is also the key Sentry uses for the
    exception MESSAGE in the structural envelope. The scrubber applies the
    denylist only inside data sub-trees (request / extra / contexts / user /
    tags / vars / data), so a structural `exception.values[].value` with no
    PII is left intact rather than nuked to `[Filtered]`."""
    event = {
        "exception": {"values": [{"type": "RuntimeError", "value": "boom while loading /clients"}]},
        "request": {"url": "/clients", "method": "GET"},
        "transaction": "/clients",
        "level": "error",
    }
    scrubbed = scrub_event(event, {})
    assert scrubbed["exception"]["values"][0]["type"] == "RuntimeError"
    # The structural error message is preserved (no PII to redact).
    assert scrubbed["exception"]["values"][0]["value"] == "boom while loading /clients"
    assert scrubbed["request"]["url"] == "/clients"
    assert scrubbed["request"]["method"] == "GET"
    assert scrubbed["transaction"] == "/clients"
    assert scrubbed["level"] == "error"


def test_exception_message_is_regex_redacted_not_wholesale() -> None:
    """An email in the exception message becomes `[Filtered]` while the rest
    of the message stays readable - this mirrors the `/_debug/sentry` raise
    and is the behaviour the card's test asserts (email scrubbed, the issue
    still legible, NOT a wholesale `[Filtered]` title)."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "value": "Sentry scrub test for scrub-check@example.com / fake tra",
                }
            ]
        }
    }
    scrubbed = scrub_event(event, {})
    message = scrubbed["exception"]["values"][0]["value"]
    assert "scrub-check@example.com" not in message
    assert message == "Sentry scrub test for [Filtered] / fake tra"


def test_scrub_handles_mixed_and_none_values() -> None:
    """Defensive: None, ints, floats, bools, and nested mixes pass through
    unchanged without raising."""
    event = {
        "a": None,
        "b": 42,
        "c": 3.14,
        "d": True,
        "e": [None, 1, "plain text", {"x": "y@z.com"}],
        "f": ("tuple", "items", "kept@list.com"),
    }
    scrubbed = scrub_event(event, {})
    assert scrubbed["a"] is None
    assert scrubbed["b"] == 42
    assert scrubbed["c"] == 3.14
    assert scrubbed["d"] is True
    assert scrubbed["e"][0] is None
    assert scrubbed["e"][1] == 1
    assert scrubbed["e"][2] == "plain text"
    assert scrubbed["e"][3]["x"] == FILTERED
    # Email inside a tuple element is still redacted.
    serialized = _dumps(scrubbed)
    assert "@z.com" not in serialized
    assert "@list.com" not in serialized


def test_init_sentry_noops_with_empty_dsn() -> None:
    """An empty DSN must leave the SDK uninitialised - no client, nothing
    sent. This is the disabled-by-default contract for local / CI / tests."""
    settings = types.SimpleNamespace(
        sentry_dsn="",
        sentry_environment="local",
        sentry_traces_sample_rate=0.0,
    )
    init_sentry(settings)
    # Modern sentry-sdk: get_client() returns a (possibly no-op) client whose
    # DSN is falsy and which is not active when init was never called.
    client = sentry_sdk.get_client()
    assert not getattr(client, "dsn", None)
    assert not client.is_active()


# ---------------------------------------------------------------------------
# The `sentry_sdk.init` options that ARE the security controls.
#
# Review round 4: `include_local_variables=False` had no test, so a silent
# revert would reopen plaintext secret capture with a fully green suite. These
# assert the kwargs actually reach `init`, because each one is load-bearing and
# none of them is observable from a scrubbed event.
# ---------------------------------------------------------------------------


def _init_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Call `init_sentry()` with a DSN and capture what it passed to `init`."""
    import sentry_sdk

    from bullet_api.observability import sentry as sentry_mod

    captured: dict[str, object] = {}

    def _fake_init(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(sentry_sdk, "init", _fake_init)
    sentry_mod.init_sentry(
        types.SimpleNamespace(
            sentry_dsn="https://abc@o0.ingest.sentry.io/0",
            sentry_environment="test",
            sentry_traces_sample_rate=0.0,
        )
    )
    assert captured, "init_sentry() did not call sentry_sdk.init"
    return captured


def test_local_variables_are_never_serialised(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIFTEEN modules bind `settings = get_settings()` as a frame local and
    `Settings` holds its secrets as plain `str`, so with this option left at its
    default (True) the first captured exception in any of those frames ships
    `ghl_agency_api_key`, `inngest_signing_key`, `resend_api_key` and
    `email_token_secret` to Sentry in plaintext. `scrub_event` cannot save us -
    it matches KEY names, and these arrive nested inside a `settings` repr.
    """
    assert _init_kwargs(monkeypatch)["include_local_variables"] is False


def test_request_bodies_are_never_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    """`send_default_pii=False` does NOT gate request bodies on FastAPI -
    `integrations/fastapi.py` sets `request_info["data"]` unconditionally and
    this option defaults to "medium" (10 KB). With Inngest failures now
    captured, the body IS the event payload: for `store_sales_knowledge` that is
    a whole `SalesCallSummary` including verbatim client speech.
    """
    assert _init_kwargs(monkeypatch)["max_request_body_size"] == "never"


def test_pii_and_scrub_hooks_are_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 5: the round-4 CHANGELOG claimed these were pinned. They were not -
    the assertion was dropped as out-of-scope and the claim was not updated, so
    the docs described coverage that did not exist. Asserted now."""
    kwargs = _init_kwargs(monkeypatch)
    assert kwargs["send_default_pii"] is False
    assert kwargs["before_send"] is scrub_event
    assert kwargs["before_send_transaction"] is scrub_event


def test_exception_message_length_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_EMAIL_RE` is quadratic on a long unbroken token run, and nothing
    truncates by default in 2.61.1, so an uncapped GHL error body would be
    regex-walked inline on the event loop."""
    assert _init_kwargs(monkeypatch)["max_value_length"] == 2048


def test_inngest_signature_header_is_denylisted() -> None:
    """The signature is logged on the same event as the body it authenticates,
    there is no freshness check in 0.5.18, and the body is JCS-canonicalised
    before the HMAC - so the pair is REPLAYABLE against the public
    /api/inngest by anyone with Sentry read access."""
    assert "x-inngest-signature" in DENYLIST_KEYS
    scrubbed = scrub_event(
        {"request": {"headers": {"X-Inngest-Signature": "t=1&s=deadbeef"}}}, None
    )
    assert scrubbed["request"]["headers"]["X-Inngest-Signature"] == FILTERED
