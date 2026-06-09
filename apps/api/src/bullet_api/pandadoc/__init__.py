"""Outbound PandaDoc REST primitives.

S1-24 lands the PandaDoc API client behind the manual replay endpoint. The
abstraction is a thin `PandaDocClient` protocol so tests can substitute a
`FakePandaDocClient` that returns preloaded documents, and the production
`HttpPandaDocClient` can be swapped without touching the call sites.
"""

from __future__ import annotations

from bullet_api.pandadoc.accounts import (
    PANDADOC_ACCOUNT_INT,
    PANDADOC_ACCOUNT_UK,
    PANDADOC_ACCOUNTS,
    PandaDocAccount,
    PandaDocCreds,
    api_accounts,
    api_key_for,
    webhook_secrets,
)
from bullet_api.pandadoc.client import (
    PANDADOC_API_BASE_URL,
    FakePandaDocClient,
    HttpPandaDocClient,
    PandaDocClient,
    PandaDocDocument,
    PandaDocNotFound,
    get_pandadoc_client,
)

__all__ = [
    "PANDADOC_ACCOUNTS",
    "PANDADOC_ACCOUNT_INT",
    "PANDADOC_ACCOUNT_UK",
    "PANDADOC_API_BASE_URL",
    "FakePandaDocClient",
    "HttpPandaDocClient",
    "PandaDocAccount",
    "PandaDocClient",
    "PandaDocCreds",
    "PandaDocDocument",
    "PandaDocNotFound",
    "api_accounts",
    "api_key_for",
    "get_pandadoc_client",
    "webhook_secrets",
]
