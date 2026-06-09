"""Unit tests for the per-account PandaDoc credential resolver (S1-25c).

Pure, no DB / FastAPI / network: builds `Settings` directly and asserts the
account -> credentials mapping the whole PandaDoc path relies on.
"""

from __future__ import annotations

import pytest

from bullet_api.config import Settings
from bullet_api.pandadoc.accounts import (
    PANDADOC_ACCOUNT_INT,
    PANDADOC_ACCOUNT_UK,
    api_accounts,
    api_key_for,
    webhook_secrets,
)


def _settings(**overrides: str) -> Settings:
    base = {
        "pandadoc_api_key_uk": "",
        "pandadoc_api_key_int": "",
        "pandadoc_webhook_secret_uk": "",
        "pandadoc_webhook_secret_int": "",
    }
    base.update(overrides)
    return Settings(**base)


def test_api_key_for_selects_per_account() -> None:
    s = _settings(pandadoc_api_key_uk="UKKEY", pandadoc_api_key_int="INTKEY")
    assert api_key_for(PANDADOC_ACCOUNT_UK, s) == "UKKEY"
    assert api_key_for(PANDADOC_ACCOUNT_INT, s) == "INTKEY"


def test_api_key_for_unknown_account_raises() -> None:
    with pytest.raises(ValueError, match="unknown PandaDoc account"):
        api_key_for("eu", _settings())


def test_webhook_secrets_only_includes_configured_accounts() -> None:
    # Both configured -> both present, UK first (stable order).
    both = webhook_secrets(
        _settings(pandadoc_webhook_secret_uk="su", pandadoc_webhook_secret_int="si")
    )
    assert both == {"uk": "su", "int": "si"}
    assert list(both) == ["uk", "int"]

    # Only UK configured -> INT omitted (so it can never spuriously match).
    only_uk = webhook_secrets(_settings(pandadoc_webhook_secret_uk="su"))
    assert only_uk == {"uk": "su"}

    # Neither configured -> empty map (webhook then fails closed / 401).
    assert webhook_secrets(_settings()) == {}


def test_api_accounts_skips_accounts_without_a_key() -> None:
    # Both keys -> both accounts, UK first.
    both = api_accounts(_settings(pandadoc_api_key_uk="UK", pandadoc_api_key_int="INT"))
    assert [c.account for c in both] == ["uk", "int"]
    assert [c.api_key for c in both] == ["UK", "INT"]

    # Only INT key -> only INT looped (UK skipped cleanly).
    only_int = api_accounts(_settings(pandadoc_api_key_int="INT"))
    assert [c.account for c in only_int] == ["int"]

    # No keys -> empty (reconcile cron then logs disabled + exits 0).
    assert api_accounts(_settings()) == []
