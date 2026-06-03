"""Unit tests for the PandaDoc REST API client (S1-24, Slice A).

No DB: these exercise `HttpPandaDocClient` against an injected
`httpx.MockTransport` (so no network call) plus the `FakePandaDocClient`
test double. The suite runs with `asyncio_mode = "auto"`, so async test
functions need no decorator.
"""

from __future__ import annotations

import httpx
import pytest

from bullet_api.pandadoc import (
    FakePandaDocClient,
    HttpPandaDocClient,
    PandaDocDocument,
    PandaDocNotFound,
)


async def test_fetch_document_returns_projection_on_200() -> None:
    doc_id = "doc_abc123"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/public/v1/documents/{doc_id}/details"
        assert request.headers["Authorization"] == "API-Key test-key"
        return httpx.Response(
            200,
            json={"id": doc_id, "name": "Agreement", "status": "document.completed"},
        )

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    document = await client.fetch_document(doc_id)

    assert document == PandaDocDocument(id=doc_id, name="Agreement", status="document.completed")


async def test_fetch_document_raises_not_found_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not found"})

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    with pytest.raises(PandaDocNotFound):
        await client.fetch_document("doc_missing")


async def test_fetch_document_raises_runtime_error_on_empty_key() -> None:
    client = HttpPandaDocClient(api_key="")

    with pytest.raises(RuntimeError):
        await client.fetch_document("doc_abc123")


async def test_fake_client_returns_preloaded_and_raises_on_unknown() -> None:
    preloaded = PandaDocDocument(id="doc_known", name="Agreement", status="document.completed")
    client = FakePandaDocClient(documents={"doc_known": preloaded})

    assert await client.fetch_document("doc_known") == preloaded

    with pytest.raises(PandaDocNotFound):
        await client.fetch_document("doc_unknown")
