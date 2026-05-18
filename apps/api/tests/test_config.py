"""Unit tests for `bullet_api.config`.

These tests exercise the pure URL-rewriting helpers and never touch a live
database. They guard against the S1-05a regression where Neon's canonical
URL (with `?sslmode=require&channel_binding=require`) crashed asyncpg at
connect time.
"""

from __future__ import annotations

from bullet_api.config import _strip_libpq_query_params, get_async_database_url


def test_neon_canonical_url_strips_libpq_only_params() -> None:
    """Neon's canonical URL must round-trip into an asyncpg-safe form."""
    canonical = (
        "postgresql://neondb_owner:secret@ep-foo-pooler.eu-west-2.aws.neon.tech"
        "/neondb?sslmode=require&channel_binding=require"
    )
    rewritten = get_async_database_url(canonical)
    assert rewritten.startswith("postgresql+asyncpg://")
    assert "sslmode" not in rewritten
    assert "channel_binding" not in rewritten
    # User, host, and database path must survive the rewrite untouched.
    assert (
        "neondb_owner:secret@ep-foo-pooler.eu-west-2.aws.neon.tech/neondb"
        in rewritten
    )


def test_postgresql_short_scheme_rewritten_to_asyncpg() -> None:
    assert (
        get_async_database_url("postgresql://u:p@h:5432/db")
        == "postgresql+asyncpg://u:p@h:5432/db"
    )


def test_postgres_short_scheme_rewritten_to_asyncpg() -> None:
    # `postgres://` is the legacy short form used by some hosts (Heroku, etc.).
    assert (
        get_async_database_url("postgres://u:p@h/db")
        == "postgresql+asyncpg://u:p@h/db"
    )


def test_already_async_scheme_is_preserved_but_query_still_cleaned() -> None:
    rewritten = get_async_database_url(
        "postgresql+asyncpg://u:p@h/db?sslmode=require&application_name=bullet"
    )
    assert rewritten.startswith("postgresql+asyncpg://")
    assert "sslmode" not in rewritten
    # Non-libpq params must survive so legitimate asyncpg knobs work.
    assert "application_name=bullet" in rewritten


def test_unknown_scheme_is_returned_unchanged() -> None:
    # SQLite, MySQL, or any other dialect should not be touched.
    assert get_async_database_url("sqlite:///foo.db") == "sqlite:///foo.db"


def test_no_query_string_is_idempotent() -> None:
    assert _strip_libpq_query_params("postgresql://u:p@h/db") == (
        "postgresql://u:p@h/db"
    )


def test_strip_is_case_insensitive_on_keys() -> None:
    # libpq accepts upper-case keys; asyncpg would still reject them.
    rewritten = get_async_database_url(
        "postgresql://u:p@h/db?SSLMode=require&Channel_Binding=require"
    )
    assert "SSLMode" not in rewritten
    assert "Channel_Binding" not in rewritten


def test_other_libpq_only_params_are_stripped() -> None:
    rewritten = get_async_database_url(
        "postgresql://u:p@h/db"
        "?sslmode=require"
        "&sslrootcert=/etc/ssl/root.pem"
        "&gssencmode=disable"
        "&application_name=bullet-api"
    )
    assert "sslmode" not in rewritten
    assert "sslrootcert" not in rewritten
    assert "gssencmode" not in rewritten
    # Non-libpq params survive.
    assert "application_name=bullet-api" in rewritten
