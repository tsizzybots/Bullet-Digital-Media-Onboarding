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


# --------------------------------------------------------------------------- #
# S1-25a: fetch_document_details + HTTP failure-mode tests
# --------------------------------------------------------------------------- #


async def test_fetch_document_details_returns_full_body() -> None:
    """The detail fetch returns the RAW JSON body, not the
    `PandaDocDocument` projection. S1-25a uses this to read recipients,
    tokens, fields, metadata."""
    doc_id = "doc_abc123"
    full_body = {
        "id": doc_id,
        "name": "Agreement",
        "status": "document.completed",
        "tokens": [{"name": "Client.Email", "value": "signer@example.com"}],
        "fields": [],
        "metadata": {"hubspot.contact_id": "1234"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/public/v1/documents/{doc_id}/details"
        return httpx.Response(200, json=full_body)

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    body = await client.fetch_document_details(doc_id)
    assert body == full_body


async def test_fetch_document_details_raises_not_found_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(404, json={"detail": "Not found"})

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    with pytest.raises(PandaDocNotFound):
        await client.fetch_document_details("doc_missing")


async def test_fetch_document_details_propagates_500() -> None:
    """F2 in the failure matrix: a transient PandaDoc 5xx must surface
    as `httpx.HTTPStatusError` so the Inngest wrapper retries (it's
    NOT translated to NonRetriable)."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(500, json={"error": "internal"})

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await client.fetch_document_details("doc_abc")
    assert exc.value.response.status_code == 500


async def test_fetch_document_details_propagates_429_rate_limited() -> None:
    """F3 in the failure matrix: rate-limit responses must surface as a
    retryable error so Inngest retries. Future improvement: honour
    Retry-After (tracked as follow-up)."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            429,
            json={"error": "rate_limited"},
            headers={"Retry-After": "30"},
        )

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await client.fetch_document_details("doc_abc")
    assert exc.value.response.status_code == 429
    assert exc.value.response.headers["Retry-After"] == "30"


async def test_fetch_document_details_propagates_timeout() -> None:
    """F4 in the failure matrix: a network timeout must surface as
    `httpx.TimeoutException` so the Inngest wrapper retries."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise httpx.TimeoutException("timed out", request=request)

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.TimeoutException):
        await client.fetch_document_details("doc_abc")


async def test_fetch_document_details_raises_runtime_error_on_empty_key() -> None:
    """F5 in the failure matrix: empty API key (mis-configured deploy) is
    NOT a transient failure - we surface it loudly. The Inngest wrapper
    translates RuntimeError into NonRetriableError so the run
    dead-letters immediately."""
    client = HttpPandaDocClient(api_key="")

    with pytest.raises(RuntimeError, match="API key is empty"):
        await client.fetch_document_details("doc_abc")


async def test_fake_client_details_preloaded_and_raises_on_unknown() -> None:
    """The Fake supports the S1-25a details endpoint with a parallel
    `details` map alongside the S1-24 `documents` projection map."""
    body = {"id": "doc_known", "tokens": [{"name": "Client.Email", "value": "x@y.com"}]}
    client = FakePandaDocClient(details={"doc_known": body})

    assert await client.fetch_document_details("doc_known") == body

    with pytest.raises(PandaDocNotFound):
        await client.fetch_document_details("doc_unknown")


# --------------------------------------------------------------------------- #
# S1-25b: download_document (signed PDF bytes)
# --------------------------------------------------------------------------- #


async def test_download_document_returns_pdf_bytes_on_200() -> None:
    doc_id = "doc_abc123"
    pdf = b"%PDF-1.7\nsigned\n%%EOF"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/public/v1/documents/{doc_id}/download"
        assert request.headers["Authorization"] == "API-Key test-key"
        return httpx.Response(200, content=pdf)

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    assert await client.download_document(doc_id) == pdf


async def test_download_document_raises_not_found_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(404, json={"detail": "Not found"})

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    with pytest.raises(PandaDocNotFound):
        await client.download_document("doc_missing")


async def test_download_document_propagates_500_for_retry() -> None:
    """A transient 5xx must surface as `httpx.HTTPStatusError` so the worker
    records `failed` and Inngest retries (NOT translated to NonRetriable)."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(502, text="bad gateway")

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await client.download_document("doc_abc")
    assert exc.value.response.status_code == 502


async def test_download_document_raises_runtime_error_on_empty_key() -> None:
    client = HttpPandaDocClient(api_key="")
    with pytest.raises(RuntimeError, match="API key is empty"):
        await client.download_document("doc_abc")


async def test_fake_client_download_preloaded_error_and_unknown() -> None:
    pdf = b"%PDF-1.7"
    client = FakePandaDocClient(pdfs={"doc_known": pdf})
    assert await client.download_document("doc_known") == pdf

    with pytest.raises(PandaDocNotFound):
        await client.download_document("doc_unknown")

    # download_error is raised regardless of pdfs (transport-error simulation).
    erroring = FakePandaDocClient(pdfs={"doc_known": pdf}, download_error=httpx.ReadTimeout("boom"))
    with pytest.raises(httpx.ReadTimeout):
        await erroring.download_document("doc_known")
