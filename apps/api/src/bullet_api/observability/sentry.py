"""Sentry error monitoring with PII scrubbing for the Bullet API.

Disabled by default: when `settings.sentry_dsn` is empty (local dev, CI,
and the test suite) `init_sentry()` is a no-op and nothing is ever sent.
Render staging / prod env groups set `SENTRY_DSN` (plus `SENTRY_ENVIRONMENT`
and `SENTRY_TRACES_SAMPLE_RATE`) to switch it on.

PII scrubbing is layered:

1. `send_default_pii=False` keeps Sentry from attaching cookies and user
   IP/email. It does NOT cover request bodies on FastAPI - that is
   `max_request_body_size="never"`, set separately below; the two are often
   conflated and only one of them stops the Inngest event payload being sent.
2. The built-in `EventScrubber` (left at its default, on) strips the
   standard PII denylist.
3. `scrub_event` (this module) is registered as both `before_send` and
   `before_send_transaction`. It walks the event in two zones, applying the
   project-wide contract shared with the dashboard slice:
     - Inside DATA sub-trees (`request`, `extra`, `contexts`, `user`,
       `tags`, stack-frame `vars`, breadcrumb `data`) a denylisted key
       (case-insensitive) has its whole value replaced with the
       `[Filtered]` sentinel - this is where transcript / knowledge blobs,
       request bodies, and headers live.
     - In Sentry's STRUCTURAL envelope (exception messages, breadcrumb
       messages, the transaction name) the denylist is NOT applied, because
       keys there collide with data-column names - notably `value`, which
       is both a knowledge-column name AND the key Sentry uses for the
       exception message. Wholesale-filtering it would turn every Sentry
       issue title into "RuntimeError: [Filtered]" and defeat the point of
       error monitoring. Structural strings are instead regex-redacted, so
       an embedded email becomes `[Filtered]` while the surrounding message
       stays readable.
   Every string value, in either zone, is additionally regex-redacted for
   emails, phone numbers, and signed / bearer URLs (PandaDoc share links,
   R2 signed URLs with token/signature/expires/x-amz- params).

Structural limitation (documented deliberately): free-form transcript
PROSE inside a NON-denylisted string field cannot be pattern-matched - an
embedded email or phone is caught by the regexes, but a name or address
mentioned in passing is not. The rule therefore relies on (a) keeping
transcript-bearing fields (`transcript`, `transcript_text`, `value_text`,
`value`, ...) on the denylist within data sub-trees, and (b)
`max_request_body_size="never"` keeping request bodies - including the
Inngest event payload - out of events in the first place. NOTE this is
deliberately not `send_default_pii=False`, which does not cover bodies.
Do not put raw transcript prose into a custom-named string field that is
not on the denylist.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Shared contract with the dashboard slice - keep these three in lockstep.
FILTERED = "[Filtered]"

# Denylisted keys (lowercased). Any mapping key whose `.lower()` is in this
# set has its value replaced wholesale with FILTERED, no matter what the
# value is. Mirrors the dashboard denylist (`sentry-scrub.ts`) EXCEPT for
# `x-inngest-signature`, which is deliberately server-only: the dashboard
# never sees that header, and the divergence is recorded here rather than
# left for someone to discover by diffing the two lists (review round 5).
DENYLIST_KEYS: frozenset[str] = frozenset(
    {
        # The Inngest request signature, logged on the SAME event as the body
        # it authenticates. `_validate_sig` performs no freshness check in
        # 0.5.18 and the body is JCS-canonicalised before the HMAC, so the
        # (signature, body) pair is REPLAYABLE against the public
        # /api/inngest by anyone with Sentry read access. It is in neither
        # SENSITIVE_HEADERS nor sentry's own DEFAULT_DENYLIST.
        "x-inngest-signature",
        "email",
        "phone",
        "password",
        "password_hash",
        "full_name",
        "contact_first_name",
        "contact_last_name",
        "transcript",
        "transcript_text",
        "value_text",
        "value",
        "metadata",
        "external_url",
        "signed_url",
        "source_url",
        "r2_key",
        "pandadoc_document_id",
        "authorization",
        "cookie",
        "set-cookie",
        "session",
        "payload",
        "response",
        "before",
        "after",
    }
)

# Free-text regexes applied to every (non-denylisted) string value.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
# Any http(s) URL token that points at pandadoc.com OR carries a
# token/signature/expiry/AWS-presign query param is a signed / bearer URL
# and is replaced wholesale.
_SIGNED_URL_RE = re.compile(
    r"https?://\S*?"
    r"(?:pandadoc\.com|[?&](?:token|signature|sig|expires|x-amz-)[\w-]*=)"
    r"\S*",
    re.IGNORECASE,
)


def _redact_text(value: str) -> str:
    """Apply the three free-text regexes in turn to a single string.

    Signed-URL redaction runs first so a token-bearing URL is removed as a
    whole unit before the narrower email/phone patterns get a chance to
    nibble pieces out of it.
    """
    redacted = _SIGNED_URL_RE.sub(FILTERED, value)
    redacted = _EMAIL_RE.sub(FILTERED, redacted)
    redacted = _PHONE_RE.sub(FILTERED, redacted)
    return redacted


# Event sub-trees that hold arbitrary application / user data. The denylist
# is applied ONLY inside these (and anything nested under them). Outside them
# - in Sentry's structural envelope (exception messages, breadcrumb messages,
# the transaction name, etc.) - only the free-text regexes run, so a key that
# happens to share a data-column name (notably `value`, which is both a
# denylisted knowledge column AND the key Sentry uses for the exception
# message) does not wholesale-nuke a human-readable message.
_DATA_SUBTREE_KEYS: frozenset[str] = frozenset(
    {"request", "extra", "contexts", "user", "tags", "vars", "data"}
)


def _walk(value: Any, *, in_data: bool) -> Any:
    """Recursively scrub a value pulled from a Sentry event.

    `in_data` tracks whether we are inside a data sub-tree (see
    `_DATA_SUBTREE_KEYS`). A denylisted key has its whole value replaced with
    FILTERED only when `in_data` is true; everywhere else only the free-text
    regexes apply, so structural messages stay legible while embedded emails,
    phone numbers, and signed URLs are still redacted.

    - dict: recurse per key. Entering a `_DATA_SUBTREE_KEYS` key switches
      `in_data` on for that branch.
    - list / tuple: recurse element-wise, preserving the container type.
    - str: regex-redact via `_redact_text`.
    - anything else (None, int, float, bool, ...): returned unchanged.
    """
    if isinstance(value, dict):
        scrubbed: dict[Any, Any] = {}
        for key, item in value.items():
            key_l = key.lower() if isinstance(key, str) else ""
            child_in_data = in_data or key_l in _DATA_SUBTREE_KEYS
            if child_in_data and key_l in DENYLIST_KEYS:
                scrubbed[key] = FILTERED
            else:
                scrubbed[key] = _walk(item, in_data=child_in_data)
        return scrubbed
    if isinstance(value, list):
        return [_walk(item, in_data=in_data) for item in value]
    if isinstance(value, tuple):
        return tuple(_walk(item, in_data=in_data) for item in value)
    if isinstance(value, str):
        return _redact_text(value)
    return value


def scrub_event(event: Any, hint: Any) -> Any:
    """Sentry `before_send` / `before_send_transaction` hook.

    Walks the entire event and returns the scrubbed copy, starting outside
    any data sub-tree. Signature is `(event, hint) -> event` so the same
    callable serves both error events and transaction (performance) events.
    Never returns None, so no event is dropped - only redacted.
    """
    return _walk(event, in_data=False)


def init_sentry(settings: Any) -> None:
    """Initialise Sentry from `settings`, or no-op when no DSN is set.

    `settings` only needs the three Sentry attributes (`sentry_dsn`,
    `sentry_environment`, `sentry_traces_sample_rate`); typed loosely so a
    `types.SimpleNamespace` works in tests without importing the real
    Settings model.

    When `sentry_dsn` is empty this returns immediately and Sentry is never
    INITIALISED, so local / CI / test runs send nothing. Note the module is
    still IMPORTED regardless: `observability/inngest_sentry.py` imports `sentry_sdk`
    at module top and `worker/_inngest.py` imports that unconditionally (only
    the middleware REGISTRATION is DSN-gated). The deferred import below is
    therefore about init cost, not about avoiding the dependency.
    """
    if not settings.sentry_dsn:
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        # Render injects the deployed commit SHA; tag the release with it
        # when present so issues group by build.
        release=os.getenv("RENDER_GIT_COMMIT") or None,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # Belt-and-braces with the EventScrubber + scrub_event hook below.
        send_default_pii=False,
        # Do NOT serialise frame locals into events. This defaults to True, and
        # `Settings` holds its secrets as plain `str` (no `SecretStr`), while
        # FIFTEEN modules bind `settings = get_settings()` as a frame local -
        # across `worker/`, `ghl/`, `pandadoc/`, `storage/`, `email/`,
        # `google/`, `crons/`, `webhooks/` and this module itself. So the
        # FIRST captured exception in any of those
        # frames would ship `ghl_agency_api_key`, `inngest_signing_key`,
        # `resend_api_key` and `email_token_secret` to Sentry in plaintext.
        # `scrub_event` cannot save us: it matches on KEY names, and these
        # arrive nested inside a `settings` repr rather than as top-level keys.
        # Turning this off is the one-line containment; making the secrets
        # `SecretStr` is the structural fix and has its own ticket.
        include_local_variables=False,
        # Request bodies are NOT covered by `send_default_pii=False` on
        # FastAPI - `integrations/fastapi.py` sets `request_info["data"]`
        # unconditionally, and this option defaults to "medium" (10 KB). Now
        # that Inngest failures are captured, that body IS the event payload:
        # for `store_sales_knowledge` it is a whole `SalesCallSummary`, i.e.
        # `notable_quotes[].quote` (verbatim client speech), `.speaker`,
        # `pain_points` and `red_flags`. None of those are denylisted keys, and
        # scrubbing works on key names rather than nested payload shapes.
        max_request_body_size="never",
        # Cap every string BEFORE `scrub_event` regex-walks it. The default is
        # None in sentry-sdk 2.61.1, so nothing truncates - and `_EMAIL_RE` is
        # quadratic on a long unbroken `[\w.+-]` run with no `@`. Review round 5
        # measured it: a realistic 15.7 KB JSON error body costs ~1.07 ms
        # (punctuation breaks the run), but a 20 KB body containing one
        # unbroken token costs ~1,911 ms - inline, on the event loop shared
        # with the webhook and the dashboard API. Reachable rather than
        # typical, and a one-line cap removes the pathological case.
        max_value_length=2048,
        before_send=scrub_event,
        before_send_transaction=scrub_event,
    )
    # FastAPI / Starlette integration auto-enables because fastapi is
    # installed; the built-in EventScrubber stays at its default (on). No
    # explicit integration list needed.
