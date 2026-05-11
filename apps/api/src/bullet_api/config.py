"""Application settings loaded from environment variables.

`Settings` is the typed entrypoint for every env-driven knob in the API.
Future tasks (HubSpot, PandaDoc, Resend, Stripe, GHL, etc.) add fields here;
the goal is that no other module reads `os.environ` directly.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
            "SQLAlchemy async + Alembic."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def get_async_database_url(url: str | None = None) -> str:
    """Return the DATABASE_URL rewritten for SQLAlchemy's asyncpg driver.

    Accepts either the canonical `postgresql://` form (preferred in .env) or
    an already-qualified `postgresql+asyncpg://` form. Anything else is
    returned unchanged so explicit overrides are respected.
    """
    raw = url if url is not None else get_settings().database_url
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw[len("postgresql://") :]
    if raw.startswith("postgres://"):
        return "postgresql+asyncpg://" + raw[len("postgres://") :]
    return raw
