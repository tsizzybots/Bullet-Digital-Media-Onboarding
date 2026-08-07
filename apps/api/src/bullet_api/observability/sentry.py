"""Sentry error monitoring with PII scrubbing for the Bullet API.

Disabled by default: when `settings.sentry_dsn` is empty (local dev, CI,
and the test suite) `init_sentry()` is a no-op and nothing is ever sent.
Render staging / prod env groups set `SENTRY_DSN` (plus `SENTRY_ENVIRONMENT`
and `SENTRY_TRACES_SAMPLE_RATE`) to switch it on.

PII scrubbing is layered:

1. `send_default_pii=False` keeps Sentry from attaching request bodies,
   cookies, and user IP/email by default.
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
`send_default_pii=False` keeping transcript bodies out of events in the
first place. Do not put raw transcript prose into a custom-named string
field that is not on the denylist.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Shared contract with the dashboard slice - keep these three in lockstep.
FILTERED = "[Filtered]"

# Denylisted keys (lowercased). Any mapping key whose `.lower()` is in this
# set has its value replaced wholesale with FILTERED, no matter what the
# value is. Mirrors the dashboard denylist exactly.
DENYLIST_KEYS: frozenset[str] = frozenset(
    {
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

    When `sentry_dsn` is empty this returns immediately and `sentry_sdk` is
    never imported or initialised, so local / CI / test runs send nothing.
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
        # SEVEN modules bind `settings = get_settings()` as a frame local -
        # `worker/_inngest.py`, `client_record.py`, `ghl_subaccount.py`,
        # `signed_pdf.py`, `meet_transcript.py`, `embedding_client.py`,
        # `summary_client.py`. So the FIRST captured exception in any of those
        # frames would ship `ghl_agency_api_key`, `inngest_signing_key`,
        # `resend_api_key` and `email_token_secret` to Sentry in plaintext.
        # `scrub_event` cannot save us: it matches on KEY names, and these
        # arrive nested inside a `settings` repr rather than as top-level keys.
        # Turning this off is the one-line containment; making the secrets
        # `SecretStr` is the structural fix and has its own ticket.
        include_local_variables=False,
        before_send=scrub_event,
        before_send_transaction=scrub_event,
    )
    # FastAPI / Starlette integration auto-enables because fastapi is
    # installed; the built-in EventScrubber stays at its default (on). No
    # explicit integration list needed.
