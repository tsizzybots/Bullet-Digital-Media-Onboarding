"""Unit tests for the PandaDoc list-documents client (S1-23).

Covers the integrations PandaDoc client used by the daily reconciliation
cron: the pure `parse_documents` mapper, the `FakePandaDocClient` test
double, and `HttpPandaDocClient` driven against an injected
`httpx.MockTransport` (no network, no DB). The suite runs with
`asyncio_mode = "auto"`, so async test functions need no decorator.

Filed under `test_pandadoc_integrations_client.py` rather than
`test_pandadoc_client.py` (the name in the slice contract) because that name
is already taken by the parallel S1-24 single-document-fetch tests; reusing it
would have clobbered another agent's suite.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from bullet_api.integrations.pandadoc_client import (
    FakePandaDocClient,
    HttpPandaDocClient,
    PandaDocDocument,
    parse_documents,
)

# ---------------------------------------------------------------------------
# parse_documents - pure mapping
# ---------------------------------------------------------------------------


def test_parse_documents_maps_well_formed_results() -> None:
    payload = {
        "results": [
            {
                "id": "doc_1",
                "name": "Agreement One",
                "status": "document.completed",
                "date_completed": "2026-06-01T10:00:00.000000Z",
                "extra": "kept-in-raw",
            },
            {
                "id": "doc_2",
                "name": "Agreement Two",
                "status": "document.completed",
                "date_completed": "2026-06-02T11:30:00.000000Z",
            },
        ]
    }

    documents = parse_documents(payload)

    assert documents == [
        PandaDocDocument(
            document_id="doc_1",
            name="Agreement One",
            status="document.completed",
            date_completed="2026-06-01T10:00:00.000000Z",
            raw=payload["results"][0],
        ),
        PandaDocDocument(
            document_id="doc_2",
            name="Agreement Two",
            status="document.completed",
            date_completed="2026-06-02T11:30:00.000000Z",
            raw=payload["results"][1],
        ),
    ]
    # raw preserves the entire result object, including unmodelled fields.
    assert documents[0].raw["extra"] == "kept-in-raw"


def test_parse_documents_skips_results_with_missing_or_empty_id() -> None:
    payload = {
        "results": [
            {"name": "No id here", "status": "document.completed"},
            {"id": "", "name": "Empty id", "status": "document.completed"},
            {"id": None, "name": "Null id", "status": "document.completed"},
            {"id": 123, "name": "Non-string id", "status": "document.completed"},
            {"id": "doc_ok", "name": "Good", "status": "document.completed"},
        ]
    }

    documents = parse_documents(payload)

    assert [doc.document_id for doc in documents] == ["doc_ok"]


def test_parse_documents_missing_date_completed_is_none() -> None:
    payload = {
        "results": [
            {"id": "doc_no_date", "name": "No date", "status": "document.completed"},
            {
                "id": "doc_null_date",
                "name": "Null date",
                "status": "document.completed",
                "date_completed": None,
            },
            {
                "id": "doc_empty_date",
                "name": "Empty date",
                "status": "document.completed",
                "date_completed": "",
            },
        ]
    }

    documents = parse_documents(payload)

    assert [doc.date_completed for doc in documents] == [None, None, None]


def test_parse_documents_empty_results_is_empty_list() -> None:
    assert parse_documents({"results": []}) == []


def test_parse_documents_malformed_inputs_return_empty_list() -> None:
    # Total over garbage: never raises, always [].
    assert parse_documents(None) == []
    assert parse_documents([]) == []
    assert parse_documents(["a", "b"]) == []
    assert parse_documents({}) == []
    assert parse_documents({"results": "x"}) == []
    assert parse_documents({"results": None}) == []
    assert parse_documents({"results": {"id": "doc"}}) == []
    assert parse_documents("not a dict") == []
    assert parse_documents(42) == []


def test_parse_documents_skips_non_dict_results() -> None:
    payload = {"results": ["str", 1, None, {"id": "doc_ok", "name": "OK"}]}

    documents = parse_documents(payload)

    assert [doc.document_id for doc in documents] == ["doc_ok"]


# ---------------------------------------------------------------------------
# FakePandaDocClient - test double
# ---------------------------------------------------------------------------


async def test_fake_client_returns_canned_docs_and_records_calls() -> None:
    canned = [
        PandaDocDocument(
            document_id="doc_1",
            name="Agreement",
            status="document.completed",
            date_completed="2026-06-01T10:00:00Z",
            raw={"id": "doc_1"},
        )
    ]
    client = FakePandaDocClient(docs=canned)
    watermark = datetime(2026, 6, 1, tzinfo=UTC)

    returned = await client.list_completed_documents(watermark)

    assert returned == canned
    # A copy is returned, not the internal list, so callers cannot mutate it.
    assert returned is not client.docs
    assert client.calls == [watermark]


async def test_fake_client_accumulates_multiple_call_watermarks() -> None:
    client = FakePandaDocClient()
    first = datetime(2026, 6, 1, tzinfo=UTC)
    second = datetime(2026, 6, 2, tzinfo=UTC)

    assert await client.list_completed_documents(first) == []
    assert await client.list_completed_documents(second) == []

    assert client.calls == [first, second]


# ---------------------------------------------------------------------------
# HttpPandaDocClient - MockTransport, no network
# ---------------------------------------------------------------------------


async def test_http_client_request_shape_path_header_and_params() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["Authorization"]
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "doc_1",
                        "name": "Agreement",
                        "status": "document.completed",
                        "date_completed": "2026-06-01T10:00:00Z",
                    }
                ]
            },
        )

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    documents = await client.list_completed_documents(datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC))

    assert [doc.document_id for doc in documents] == ["doc_1"]
    assert seen["path"] == "/public/v1/documents"
    assert seen["authorization"] == "API-Key test-key"
    params = seen["params"]
    assert params["status"] == "2"
    assert "completed_from" in params
    assert params["completed_from"] == "2026-06-01T09:00:00+00:00"


async def test_http_client_paginates_until_short_page() -> None:
    # Page 1 returns a full page (count=100); page 2 is short, so the loop
    # stops after page 2. Both pages' documents must come back.
    full_page = {
        "results": [
            {
                "id": f"doc_p1_{i}",
                "name": f"Doc {i}",
                "status": "document.completed",
                "date_completed": "2026-06-01T10:00:00Z",
            }
            for i in range(100)
        ]
    }
    short_page = {
        "results": [
            {
                "id": "doc_p2_0",
                "name": "Doc last",
                "status": "document.completed",
                "date_completed": "2026-06-02T10:00:00Z",
            }
        ]
    }
    requested_pages: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        requested_pages.append(page)
        if page == "1":
            return httpx.Response(200, json=full_page)
        return httpx.Response(200, json=short_page)

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    documents = await client.list_completed_documents(datetime(2026, 6, 1, tzinfo=UTC))

    # 100 from page 1 + 1 from page 2 = 101, and exactly two pages fetched.
    assert len(documents) == 101
    assert requested_pages == ["1", "2"]
    assert documents[0].document_id == "doc_p1_0"
    assert documents[-1].document_id == "doc_p2_0"


async def test_http_client_stops_on_empty_first_page() -> None:
    requested_pages: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_pages.append(request.url.params.get("page"))
        return httpx.Response(200, json={"results": []})

    client = HttpPandaDocClient(api_key="test-key", transport=httpx.MockTransport(handler))

    documents = await client.list_completed_documents(datetime(2026, 6, 1, tzinfo=UTC))

    assert documents == []
    assert requested_pages == ["1"]


async def test_http_client_raises_on_empty_api_key() -> None:
    client = HttpPandaDocClient(api_key="")

    try:
        await client.list_completed_documents(datetime(2026, 6, 1, tzinfo=UTC))
    except RuntimeError:
        pass
    else:  # pragma: no cover - guard
        raise AssertionError("expected RuntimeError on empty api_key")
