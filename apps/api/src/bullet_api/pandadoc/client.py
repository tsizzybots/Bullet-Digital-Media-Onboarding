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
        """Fetch a single document by id. Raises PandaDocNotFound on 404."""
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
        if not self._api_key:
            # Fail loudly rather than silently 404, mirroring ResendEmailClient.
            raise RuntimeError(
                "PANDADOC_API_KEY is empty; cannot fetch document. Set it on the Render env group."
            )
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(
                f"{self._base_url}/public/v1/documents/{document_id}/details",
                headers={"Authorization": f"API-Key {self._api_key}"},
            )
        if response.status_code == 404:
            raise PandaDocNotFound(document_id)
        response.raise_for_status()
        body = response.json()
        return PandaDocDocument(
            id=body["id"],
            name=body.get("name", ""),
            status=body["status"],
        )


@dataclass
class FakePandaDocClient:
    """Test double. Returns preloaded documents; raises PandaDocNotFound for
    unknown ids. Tests construct it with a {document_id: PandaDocDocument} map."""

    documents: dict[str, PandaDocDocument] = field(default_factory=dict)

    async def fetch_document(self, document_id: str) -> PandaDocDocument:
        try:
            return self.documents[document_id]
        except KeyError:
            raise PandaDocNotFound(document_id) from None


def get_pandadoc_client() -> PandaDocClient:
    """FastAPI dependency. Tests override this with a FakePandaDocClient."""
    return HttpPandaDocClient(api_key=get_settings().pandadoc_api_key)
