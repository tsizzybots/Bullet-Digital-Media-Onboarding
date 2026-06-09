"""PandaDoc REST API client abstraction.

`PandaDocClient` is a small Protocol so handlers depend on the interface
rather than on PandaDoc specifically. `HttpPandaDocClient` is the
production wiring; `FakePandaDocClient` is used by tests to return
preloaded documents and assert against them without an API call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from bullet_api.config import get_settings
from bullet_api.pandadoc.accounts import PANDADOC_ACCOUNT_UK, PandaDocAccount, api_key_for

# PandaDoc's REST base. The document-detail endpoint is
# GET {base}/public/v1/documents/{id}/details and auth is the header
# `Authorization: API-Key <key>` (NOT Bearer).
PANDADOC_API_BASE_URL = "https://api.pandadoc.com"


@dataclass(frozen=True)
class PandaDocDocument:
    """Minimal projection of a PandaDoc document we need to replay one."""

    id: str
    name: str
    status: str


class PandaDocNotFound(Exception):
    """Raised when PandaDoc returns 404 for a document id."""


class PandaDocClient(Protocol):
    async def fetch_document(self, document_id: str) -> PandaDocDocument:
        """Fetch a single document by id, projected to id/name/status.

        Used by S1-24 (manual replay) which only needs to know whether the
        document exists and whether it is completed. Raises PandaDocNotFound
        on 404.
        """
        ...

    async def fetch_document_details(self, document_id: str) -> dict:
        """Fetch the FULL document body (recipients, tokens, fields, metadata).

        Used by S1-25a (`create_client_record`) which needs the actual signed
        values - email, business name, address, HubSpot ids - to populate a
        `clients` row. Same HTTP endpoint as `fetch_document`, different
        return shape (the raw JSON body). Raises PandaDocNotFound on 404.
        """
        ...

    async def download_document(self, document_id: str) -> bytes:
        """Download the signed PDF bytes for a completed document.

        Used by S1-25b (`store_signed_pdf`). Hits the PandaDoc download
        endpoint and returns the raw PDF body. Raises PandaDocNotFound on
        404 (document deleted between webhook and fan-out) and raises on any
        other non-2xx so a transient 5xx/429 propagates for retry.
        """
        ...


class HttpPandaDocClient:
    """Production client - GETs the PandaDoc document-detail endpoint.

    Accepts an optional httpx transport so tests can inject a MockTransport
    without a network call (the only testability hook; production passes None
    and httpx uses its default transport)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = PANDADOC_API_BASE_URL,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    async def fetch_document(self, document_id: str) -> PandaDocDocument:
        body = await self.fetch_document_details(document_id)
        return PandaDocDocument(
            id=body["id"],
            name=body.get("name", ""),
            status=body["status"],
        )

    async def fetch_document_details(self, document_id: str) -> dict:
        if not self._api_key:
            # Fail loudly rather than silently 404, mirroring ResendEmailClient.
            raise RuntimeError(
                "PandaDoc API key is empty; cannot fetch document. "
                "Set PANDADOC_API_KEY_UK / PANDADOC_API_KEY_INT on the Render env group."
            )
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(
                f"{self._base_url}/public/v1/documents/{document_id}/details",
                headers={"Authorization": f"API-Key {self._api_key}"},
            )
        if response.status_code == 404:
            raise PandaDocNotFound(document_id)
        response.raise_for_status()
        return response.json()

    async def download_document(self, document_id: str) -> bytes:
        if not self._api_key:
            raise RuntimeError(
                "PandaDoc API key is empty; cannot download document. "
                "Set PANDADOC_API_KEY_UK / PANDADOC_API_KEY_INT on the Render env group."
            )
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(
                f"{self._base_url}/public/v1/documents/{document_id}/download",
                headers={"Authorization": f"API-Key {self._api_key}"},
            )
        if response.status_code == 404:
            raise PandaDocNotFound(document_id)
        # Any other non-2xx (5xx, 429, an unexpected 4xx) raises so the caller
        # records `failed` and Inngest retries the transient cases.
        response.raise_for_status()
        return response.content


@dataclass
class FakePandaDocClient:
    """Test double. Returns preloaded documents; raises PandaDocNotFound for
    unknown ids.

    `documents` is keyed by id for the S1-24 projected fetch; `details` is
    keyed by id for the S1-25a raw-body fetch; `pdfs` is keyed by id for the
    S1-25b download. A test can populate any of them depending on which
    surface it exercises. `download_error`, when set, is raised by
    `download_document` regardless of `pdfs` - used to exercise transport-level
    failures (e.g. an httpx.ReadTimeout) that are NOT `PandaDocNotFound`.
    """

    documents: dict[str, PandaDocDocument] = field(default_factory=dict)
    details: dict[str, dict] = field(default_factory=dict)
    pdfs: dict[str, bytes] = field(default_factory=dict)
    download_error: Exception | None = None

    async def fetch_document(self, document_id: str) -> PandaDocDocument:
        try:
            return self.documents[document_id]
        except KeyError:
            raise PandaDocNotFound(document_id) from None

    async def fetch_document_details(self, document_id: str) -> dict:
        try:
            return self.details[document_id]
        except KeyError:
            raise PandaDocNotFound(document_id) from None

    async def download_document(self, document_id: str) -> bytes:
        if self.download_error is not None:
            raise self.download_error
        try:
            return self.pdfs[document_id]
        except KeyError:
            raise PandaDocNotFound(document_id) from None


def get_pandadoc_client(account: PandaDocAccount = PANDADOC_ACCOUNT_UK) -> PandaDocClient:
    """FastAPI dependency / factory for a PandaDoc client bound to one account.

    When used as a FastAPI dependency (the S1-24 replay endpoint), `account` is
    read from the ``?account=`` query param (default ``uk``) and FastAPI
    validates it against the allowed values, returning 422 for anything else.
    The returned client uses that account's API key (S1-25c). Tests override
    this dependency with a FakePandaDocClient.
    """
    settings = get_settings()
    return HttpPandaDocClient(
        api_key=api_key_for(account, settings),
        base_url=settings.pandadoc_api_base_url,
    )
