"""Tests for the fail-closed seed guard (S1-31 PR review).

`assert_local_seed_db()` is the load-bearing control that stops a seed script
planting a known-credential founder login on a remote database. These tests
pin its contract: accept every allowlisted local host, refuse any remote host
(exit code 2), and - the case the first cut missed - refuse a multi-host DSN
whose first host is local but a later host is remote. No live DB is needed;
the guard only parses the resolved URL string, so these run everywhere.
"""

from __future__ import annotations

import pytest

from bullet_api import seed_safety


def _patch_url(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """Make the guard resolve `url` as the DATABASE_URL it inspects."""
    monkeypatch.setattr(seed_safety, "get_async_database_url", lambda: url)


def _local_url(host: str) -> str:
    bracketed = f"[{host}]" if ":" in host else host
    return f"postgresql+asyncpg://u:p@{bracketed}:5432/db"


def test_accepts_each_local_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # One test looping the allowlist rather than parametrizing: a param id of
    # "db" would land in the test's keywords and the conftest db-skip guard
    # (`"db" in item.keywords`) would skip that host when no live DB is present.
    for host in sorted(seed_safety._LOCAL_HOSTS):
        _patch_url(monkeypatch, _local_url(host))
        # Must not raise for any allowlisted host.
        seed_safety.assert_local_seed_db()


def test_refuses_remote_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_url(
        monkeypatch,
        "postgresql+asyncpg://u:p@ep-cool-name-123.eu-west-2.aws.neon.tech/db",
    )
    with pytest.raises(SystemExit) as exc:
        seed_safety.assert_local_seed_db()
    assert exc.value.code == 2


def test_refuses_multihost_with_remote_second(monkeypatch: pytest.MonkeyPatch) -> None:
    # The bypass the first fix missed: a local first host must not smuggle a
    # remote failover host past the guard (asyncpg would connect to it).
    _patch_url(
        monkeypatch,
        "postgresql+asyncpg://u:p@localhost,prod.neon.tech:5432/db",
    )
    with pytest.raises(SystemExit) as exc:
        seed_safety.assert_local_seed_db()
    assert exc.value.code == 2


def test_accepts_multihost_all_local(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_url(monkeypatch, "postgresql+asyncpg://u:p@localhost,127.0.0.1:5432/db")
    seed_safety.assert_local_seed_db()


def test_hostless_dsn_is_treated_as_local(monkeypatch: pytest.MonkeyPatch) -> None:
    # A unix-socket DSN has no host; it cannot address a remote server, so the
    # guard allows it (an empty host list is local by construction).
    _patch_url(monkeypatch, "postgresql+asyncpg://u:p@/db")
    seed_safety.assert_local_seed_db()


def test_dev_seed_password_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEV_SEED_PASSWORD", raising=False)
    assert seed_safety.dev_seed_password() == seed_safety._DEFAULT_DEV_PASSWORD
    monkeypatch.setenv("DEV_SEED_PASSWORD", "override-from-env")
    assert seed_safety.dev_seed_password() == "override-from-env"
