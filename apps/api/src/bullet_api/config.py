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

    # ----- S1-22 PandaDoc webhook -----
    pandadoc_webhook_secret: str = Field(
        default="",
        description=(
            "Shared key for verifying PandaDoc webhook HMAC-SHA256 signatures "
            "(POST /webhooks/pandadoc). Empty in local dev / tests; set in the "
            "Render staging / prod env groups. The webhook handler fails closed "
            "(rejects every request with 401) when this is empty, so a "
            "mis-configured deployment is loud rather than silently insecure."
        ),
    )

    # ----- S1-24 PandaDoc API (manual replay) -----
    pandadoc_api_key: str = Field(
        default="",
        description=(
            "PandaDoc REST API key (header `Authorization: API-Key <key>`) used "
            "by the manual replay endpoint (POST /admin/pandadoc/replay/{id}) and "
            "the nightly reconciliation cron (bullet_api.crons.reconcile_pandadoc) "
            "to fetch / list documents. Empty in local dev / tests (the "
            "FakePandaDocClient is used); set in the Render staging / prod env "
            "groups. HttpPandaDocClient fails loudly (RuntimeError) if this is "
            "empty on a real call; the reconciliation cron logs 'disabled' and "
            "exits 0 when empty, so an unconfigured deployment is a safe no-op."
        ),
    )

    # ----- S1-23 PandaDoc reconciliation cron -----
    # NOTE: pandadoc_api_key is shared with the S1-24 replay endpoint above and
    # so is not redeclared here (a second field would silently shadow the first).
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
