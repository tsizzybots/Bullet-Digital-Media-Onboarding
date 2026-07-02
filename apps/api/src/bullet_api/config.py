"""Application settings loaded from environment variables.

`Settings` is the typed entrypoint for every env-driven knob in the API.
Future tasks (HubSpot, PandaDoc, Resend, Stripe, GHL, etc.) add fields here;
the goal is that no other module reads `os.environ` directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# libpq-only query string parameters that asyncpg does NOT accept. Neon's
# canonical URL ships with `?sslmode=require&channel_binding=require`; if
# either reaches asyncpg via the SQLAlchemy URL it raises
# `TypeError: connect() got an unexpected keyword argument 'sslmode'`
# at pool checkout, taking down `alembic upgrade head`, the FastAPI app,
# and any worker. SSL is configured separately via `connect_args` on the
# SQLAlchemy engine (see `bullet_api.db.session`).
_LIBPQ_ONLY_QUERY_PARAMS = frozenset(
    {
        "sslmode",
        "sslrootcert",
        "sslcert",
        "sslkey",
        "sslpassword",
        "sslcrl",
        "sslcrldir",
        "sslcompression",
        "channel_binding",
        "gssencmode",
        "krbsrvname",
        "gsslib",
    }
)


AsyncpgSslMode = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(
        ...,
        description=(
            "Canonical Postgres URL (postgresql://user:pass@host:port/db). "
            "Used as-is by psql and verify_pgvector.py; rewritten to "
            "postgresql+asyncpg:// by get_async_database_url() for "
            "SQLAlchemy async + Alembic. Any libpq-only query params "
            "(sslmode, channel_binding, etc.) are stripped during rewrite."
        ),
    )
    database_ssl_mode: AsyncpgSslMode = Field(
        default="prefer",
        description=(
            "asyncpg ssl mode applied via connect_args on the SQLAlchemy "
            "engine. 'prefer' works for both local docker Postgres (TLS "
            "absent, falls back to plain) and Neon (TLS mandatory, used "
            "automatically). Render env groups for staging / prod should "
            "force 'require' (or stricter) so a mis-configured local "
            "override cannot disable TLS in those environments."
        ),
    )

    # ----- S1-14 email confirmation flow -----
    email_token_secret: str = Field(
        default="dev-insecure-change-me",
        description=(
            "Secret used by itsdangerous to sign email confirmation tokens. "
            "Must be set to a high-entropy value in staging and production "
            "Render env groups; the dev default is intentionally obvious so "
            "it never lands in production silently."
        ),
    )
    resend_api_key: str = Field(
        default="",
        description=(
            "API key for Resend (https://resend.com). Empty in dev / local "
            "test - the Resend client will refuse to send when the key is "
            "empty so a misconfigured deployment fails loudly."
        ),
    )
    email_from: str = Field(
        default="onboarding@bulletdigitalmedia.com",
        description=(
            "From address used on outbound system mail. Resolved 06/05/2026 "
            "Q-01: single system mailbox; per-user from-addresses not used "
            "in Phase 1."
        ),
    )
    email_confirmation_base_url: str = Field(
        default="http://localhost:3000/confirm",
        description=(
            "Base URL the user clicks to confirm. The token is appended as "
            "`/{token}`. Render env groups set this to the dashboard host."
        ),
    )

    # ----- S1-17 dashboard CORS -----
    cors_allow_origins: str = Field(
        default="http://localhost:3000",
        description=(
            "Comma-separated list of browser origins allowed to call the API "
            "with credentials (the dashboard). Local dev defaults to the "
            "Next.js dev server; Render env groups set this to the staging / "
            "prod dashboard host(s). Parsed into a list by "
            "`cors_allow_origins_list`. Credentialed CORS forbids the `*` "
            "wildcard, so origins must be enumerated explicitly."
        ),
    )

    @property
    def cors_allow_origins_list(self) -> list[str]:
        """Split `cors_allow_origins` into a clean list of origins.

        Blank entries are dropped so a trailing comma or an empty env value
        does not produce an `""` origin that would match nothing.
        """
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    # ----- S1-19 Inngest -----
    inngest_signing_key: str = Field(
        default="",
        description=(
            "Inngest signing key for webhook verification. Set in Render env groups "
            "for staging/prod. Empty in local dev - Inngest dev server does not "
            "require a signing key."
        ),
    )
    inngest_event_key: str = Field(
        default="",
        description=(
            "Inngest event key for sending events. Set in Render env groups. "
            "Empty in local dev - the dev server accepts unsigned events."
        ),
    )
    inngest_serve_path: str = Field(
        default="/api/inngest",
        description="URL path where Inngest calls this service.",
    )

    # ----- S1-20 Sentry error monitoring -----
    sentry_dsn: str = Field(
        default="",
        description=(
            "Sentry DSN. Empty (local dev / CI / tests) disables Sentry "
            "entirely - `init_sentry()` no-ops and nothing is ever sent. "
            "Render staging / prod env groups set this to the project DSN to "
            "switch error monitoring on."
        ),
    )
    sentry_environment: str = Field(
        default="local",
        description=(
            "Sentry environment tag (local / staging / production). Render "
            "env groups override this per deployment so issues segregate by "
            "environment; the `local` default keeps dev events (if a DSN is "
            "ever set locally) out of the staging / prod streams."
        ),
    )
    sentry_traces_sample_rate: float = Field(
        default=0.0,
        description=(
            "Fraction of transactions captured for performance tracing "
            "(0.0-1.0). Defaults to 0.0 (no performance data). Render env "
            "groups raise this for staging / prod if tracing is wanted."
        ),
    )

    # ----- S1-22 / S1-25c PandaDoc webhook (dual-account: UK + International) -----
    # Bullet runs two independent PandaDoc accounts, each signing its webhooks
    # with its OWN shared key. The webhook receiver tries each configured secret
    # and routes by whichever verifies (see bullet_api.pandadoc.accounts). Empty
    # in local dev / tests; set per account in the Render env groups. The handler
    # fails closed (401) when neither secret verifies.
    pandadoc_webhook_secret_uk: str = Field(
        default="",
        description=(
            "Shared key for verifying UK-account PandaDoc webhook HMAC-SHA256 "
            "signatures. Empty in local dev / tests; set in the Render env groups."
        ),
    )
    pandadoc_webhook_secret_int: str = Field(
        default="",
        description=(
            "Shared key for verifying International-account PandaDoc webhook "
            "HMAC-SHA256 signatures. Empty in local dev / tests; set in the Render "
            "env groups."
        ),
    )

    # ----- S1-24 / S1-25c PandaDoc API (per-account: replay + reconcile + workers) -----
    pandadoc_api_key_uk: str = Field(
        default="",
        description=(
            "UK-account PandaDoc REST API key (header `Authorization: API-Key "
            "<key>`) used to fetch / download / list UK documents (replay endpoint, "
            "reconciliation cron, client-record + signed-PDF workers). Empty in "
            "local dev / tests (the FakePandaDocClient is used); set in the Render "
            "env groups. HttpPandaDocClient fails loudly (RuntimeError) if empty on "
            "a real call; the reconciliation cron skips an account whose key is "
            "empty, so an unconfigured account is a safe no-op."
        ),
    )
    pandadoc_api_key_int: str = Field(
        default="",
        description=(
            "International-account PandaDoc REST API key. Same usage + empty-key "
            "semantics as `pandadoc_api_key_uk`, for the International account."
        ),
    )

    # ----- S1-23/S1-24 PandaDoc API base URL + reconciliation cron -----
    pandadoc_api_base_url: str = Field(
        default="https://api.pandadoc.com",
        description=(
            "Base URL for the PandaDoc public API. Overridable per environment "
            "(e.g. a sandbox host) but defaults to production."
        ),
    )
    slack_webhook_url: str = Field(
        default="",
        description=(
            "Slack incoming-webhook URL for reconciliation alerts (a signed document "
            "the webhook missed). Empty in local dev / tests; the notifier no-ops and "
            "logs a warning instead of posting, mirroring the disabled-by-default "
            "Sentry / PandaDoc-secret pattern."
        ),
    )
    reconciliation_lookback_days: int = Field(
        default=7,
        description=(
            "How many days back the reconciliation cron asks PandaDoc for completed "
            "documents (completed_from = now - this). Over-fetching is free because the "
            "onboarding_events (event_type, external_id) unique constraint dedupes, so a "
            "generous window is self-healing for missed webhook deliveries."
        ),
    )

    # ----- S1-25 GoHighLevel sub-account creation (direct agency API) -----
    ghl_agency_api_key: str = Field(
        default="",
        description=(
            "GoHighLevel agency API token, sent as `Authorization: Bearer <key>` on "
            "POST /locations/. Empty in local dev / tests (the FakeGhlClient is used); "
            "set in the Render staging / prod env groups. HttpGhlClient fails loudly "
            "(RuntimeError) if this is empty on a real call, so an unconfigured "
            "deployment is loud rather than silently creating nothing."
        ),
    )
    ghl_company_id: str = Field(
        default="",
        description=(
            "GoHighLevel agency `companyId`, a required field on the create-location "
            "body. Identifies the agency under which the new sub-account is created. "
            "Empty in local dev / tests; set in the Render staging / prod env groups."
        ),
    )
    ghl_api_base_url: str = Field(
        default="https://services.leadconnectorhq.com",
        description=(
            "Base URL for the GoHighLevel REST API (LeadConnector). Overridable per "
            "environment but defaults to production."
        ),
    )
    ghl_api_version: str = Field(
        default="2021-07-28",
        description=(
            "Value sent in the GoHighLevel `Version` header, which pins the API "
            "contract. 2021-07-28 is the version the /locations/ contract was verified "
            "against (04/06/2026)."
        ),
    )
    ghl_snapshot_id: str = Field(
        default="",
        description=(
            "Optional GoHighLevel agency snapshot id cloned into each new sub-account "
            "(workflows, funnels, custom fields). Empty (the default) omits `snapshotId` "
            "from the create-location body so a bare sub-account is created. Set once "
            "Bullet confirms an onboarding snapshot id."
        ),
    )

    # ----- S1-25b Cloudflare R2 object storage (S3-compatible) -----
    r2_account_id: str = Field(
        default="",
        description=(
            "Cloudflare R2 account id. Used to build the S3 endpoint "
            "(https://<account_id>.r2.cloudflarestorage.com). Empty in local dev / tests "
            "(the FakeStorageClient is used); set in the Render staging / prod env groups."
        ),
    )
    r2_access_key_id: str = Field(
        default="",
        description=(
            "R2 S3 access key id. Empty in local dev / tests; R2StorageClient fails loudly "
            "(RuntimeError) on a real call when empty, so a mis-configured deployment is "
            "loud rather than silently storing nothing."
        ),
    )
    r2_secret_access_key: str = Field(
        default="",
        description=(
            "R2 S3 secret access key. Empty in local dev / tests; set in the Render env groups."
        ),
    )
    r2_bucket_name: str = Field(
        default="",
        description=(
            "R2 bucket the signed PDFs (and later transcripts / scraped pages) are written "
            "to. Empty in local dev / tests; set in the Render staging / prod env groups."
        ),
    )

    # ----- S1-27 Google Meet sales-call transcript capture -----
    # All empty in local dev / tests (the Fake* Google seams are used) and set
    # in the Render staging / prod env groups once Bullet provisions the Google
    # Cloud project + service account. The Http* clients fail loudly
    # (RuntimeError) on a real call when credentials are empty, so a
    # mis-configured deployment is loud rather than silently capturing nothing.
    # IMPORTANT: filling these in requires NO code change - the build is
    # complete against faked seams and swaps to the live clients purely on
    # config (see CHANGELOG 17/06/2026 S1-27).
    google_service_account_json: str = Field(
        default="",
        description=(
            "Service-account key JSON (the full credential document, or its "
            "contents) used with domain-wide delegation to read Google Meet "
            "transcripts + Calendar events. Empty in local dev / tests; set in "
            "the Render env groups. Empty -> the Google clients raise on a real "
            "call (mirrors the GHL / R2 empty-key posture)."
        ),
    )
    google_workspace_impersonate_subject: str = Field(
        default="",
        description=(
            "Workspace user the service account impersonates via domain-wide "
            "delegation (typically the meeting organiser or an admin mailbox) "
            "to fetch transcripts + calendar attendees. Empty in local dev / "
            "tests; set in the Render env groups."
        ),
    )
    google_meet_api_base_url: str = Field(
        default="https://meet.googleapis.com",
        description=(
            "Base URL for the Google Meet REST API (conferenceRecords / "
            "transcripts / participants). Overridable per environment but "
            "defaults to production."
        ),
    )
    google_calendar_api_base_url: str = Field(
        default="https://www.googleapis.com/calendar/v3",
        description=(
            "Base URL for the Google Calendar API, used to read the invite "
            "attendee emails the auto-link matches on. Overridable per "
            "environment but defaults to production."
        ),
    )
    google_pubsub_push_audience: str = Field(
        default="",
        description=(
            "Expected `aud` claim on the Google-signed OIDC token attached to "
            "the Pub/Sub push that delivers Workspace Events (the transcript "
            "webhook). Empty -> the receiver fails closed (401) because it "
            "cannot verify the push, mirroring the PandaDoc empty-secret 401. "
            "Set in the Render env groups to the push endpoint's configured "
            "audience."
        ),
    )
    google_pubsub_push_sa_email: str = Field(
        default="",
        description=(
            "Service-account email Google signs the Pub/Sub push OIDC token "
            "with (the push subscription's `oidcToken.serviceAccountEmail`). "
            "The receiver requires the verified token's `email` claim to match "
            "this. Empty -> receiver fails closed (401). Set in the Render env "
            "groups."
        ),
    )

    # ----- S1-29 Anthropic sales-call summarisation -----
    anthropic_api_key: str = Field(
        default="",
        description=(
            "Anthropic API key for the Claude sales-call summariser (S1-29). "
            "Empty in local dev / tests (the FakeSummaryClient is used); set in the "
            "Render staging / prod env groups. HttpAnthropicClient fails loudly "
            "(RuntimeError) if empty on a real call, so an unconfigured deployment "
            "is loud rather than silently producing nothing. Filling this in needs "
            "NO code change - the worker swaps Fake -> Http purely on config."
        ),
    )
    anthropic_model: str = Field(
        default="claude-opus-4-7",
        description=(
            "Claude model id for the sales-call summariser. Overridable per "
            "environment. (The current-generation id is claude-opus-4-8; this "
            "defaults to claude-opus-4-7 per the S1-29 card. Same API surface.)"
        ),
    )

    @property
    def r2_endpoint_url(self) -> str:
        """The R2 S3-compatible endpoint, derived from the account id.

        Empty when `r2_account_id` is unset (local dev / tests), which keeps the
        derived value falsy so `R2StorageClient` fails loudly rather than building
        a malformed endpoint.
        """
        if not self.r2_account_id:
            return ""
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def _strip_libpq_query_params(url: str) -> str:
    """Return `url` with any libpq-only query params removed.

    Preserves the order of remaining params and keeps blank values
    (e.g. `?application_name=`) intact so an explicit empty-string value
    still survives the round-trip.
    """
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _LIBPQ_ONLY_QUERY_PARAMS
    ]
    new_query = urlencode(kept)
    return urlunparse(parsed._replace(query=new_query))


def get_async_database_url(url: str | None = None) -> str:
    """Return the DATABASE_URL rewritten for SQLAlchemy's asyncpg driver.

    Two transformations:

    1. Scheme rewrite. `postgresql://` (or `postgres://`) becomes
       `postgresql+asyncpg://`. An already-qualified `postgresql+asyncpg://`
       URL passes through unchanged. Any other scheme is returned as-is so
       explicit overrides are respected.

    2. libpq-only query params are stripped. Neon's canonical URL ships
       with `?sslmode=require&channel_binding=require`; asyncpg rejects
       both. SSL is configured separately via `connect_args` on the
       SQLAlchemy engine (see `bullet_api.db.session`).

    The canonical `DATABASE_URL` env var is never mutated - psql,
    scripts/verify_pgvector.py, and any other libpq-speaking tool continue
    to consume the original string unchanged. Only the copy handed to
    SQLAlchemy / Alembic is rewritten.
    """
    raw = url if url is not None else get_settings().database_url
    if raw.startswith("postgresql+asyncpg://"):
        async_url = raw
    elif raw.startswith("postgresql://"):
        async_url = "postgresql+asyncpg://" + raw[len("postgresql://") :]
    elif raw.startswith("postgres://"):
        async_url = "postgresql+asyncpg://" + raw[len("postgres://") :]
    else:
        return raw
    return _strip_libpq_query_params(async_url)
