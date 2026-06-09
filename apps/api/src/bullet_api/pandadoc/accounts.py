"""Per-account PandaDoc credential resolution (S1-25c).

Bullet runs TWO independent PandaDoc accounts - UK and International - each
with its own webhook shared key (for HMAC verification) and its own REST API
key (account-scoped: a key only works for its own account's documents). This
module is the SINGLE source of truth for the account label constants and for
mapping an account to its credentials, so every part of the PandaDoc path
agrees on the same names and selection:

- the webhook receiver (tries each account's secret, routes by which verifies),
- the client-record + signed-PDF Inngest workers (pick the API key by the
  `account` carried on the event),
- the nightly reconciliation cron (loops the accounts that have a key),
- the manual replay endpoint (selects the key by an `account` parameter).

Empty-key / empty-secret semantics are unchanged from the single-account
design: an empty value means "this account is not configured here", which is a
safe no-op for the cron and a loud `RuntimeError` at call time for the workers
(mirroring `HttpPandaDocClient`). Keeping only-configured accounts in the
lookups lets local dev run with a single key.
"""

from __future__ import annotations

from dataclasses import dataclass

from bullet_api.config import Settings

PANDADOC_ACCOUNT_UK = "uk"
PANDADOC_ACCOUNT_INT = "int"

# Stable order: UK first. Used by the webhook (try-both) and the reconcile loop.
PANDADOC_ACCOUNTS: tuple[str, ...] = (PANDADOC_ACCOUNT_UK, PANDADOC_ACCOUNT_INT)


@dataclass(frozen=True)
class PandaDocCreds:
    """One account's resolved credentials."""

    account: str
    api_key: str
    webhook_secret: str


def _creds_for(account: str, settings: Settings) -> PandaDocCreds:
    if account == PANDADOC_ACCOUNT_UK:
        return PandaDocCreds(
            account=PANDADOC_ACCOUNT_UK,
            api_key=settings.pandadoc_api_key_uk,
            webhook_secret=settings.pandadoc_webhook_secret_uk,
        )
    if account == PANDADOC_ACCOUNT_INT:
        return PandaDocCreds(
            account=PANDADOC_ACCOUNT_INT,
            api_key=settings.pandadoc_api_key_int,
            webhook_secret=settings.pandadoc_webhook_secret_int,
        )
    raise ValueError(f"unknown PandaDoc account: {account!r}")


def api_key_for(account: str, settings: Settings) -> str:
    """Return the REST API key for `account` (may be empty if unconfigured).

    Raises ValueError for an unknown account label so a typo / corrupted event
    fails loudly rather than silently selecting the wrong (or no) key. An empty
    return is intentional for an unconfigured account: `HttpPandaDocClient`
    raises `RuntimeError` when called with an empty key, which the worker
    wrapper translates into a NonRetriable dead-letter (a mis-configured deploy
    is loud, not silently wrong).
    """
    return _creds_for(account, settings).api_key


def webhook_secrets(settings: Settings) -> dict[str, str]:
    """Return `{account: webhook_secret}` for accounts with a non-empty secret.

    The webhook receiver tries each of these to verify an incoming signature
    and routes by whichever matches. Accounts with an empty secret are omitted
    so they can never spuriously match and so local dev (no secrets) fails
    closed (an empty map means every request is rejected 401).
    """
    out: dict[str, str] = {}
    for account in PANDADOC_ACCOUNTS:
        creds = _creds_for(account, settings)
        if creds.webhook_secret:
            out[account] = creds.webhook_secret
    return out


def api_accounts(settings: Settings) -> list[PandaDocCreds]:
    """Return the accounts that have a non-empty API key, in stable order.

    The reconciliation cron loops these so an unconfigured account is skipped
    cleanly (no PandaDoc call, no error).
    """
    return [
        creds for account in PANDADOC_ACCOUNTS if (creds := _creds_for(account, settings)).api_key
    ]


__all__ = [
    "PANDADOC_ACCOUNTS",
    "PANDADOC_ACCOUNT_INT",
    "PANDADOC_ACCOUNT_UK",
    "PandaDocCreds",
    "api_accounts",
    "api_key_for",
    "webhook_secrets",
]
