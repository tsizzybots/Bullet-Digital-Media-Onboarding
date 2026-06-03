"""PandaDoc list-documents client for the S1-23 daily reconciliation cron.

The reconciliation cron asks PandaDoc "which documents completed since the
last successful run?" and replays any whose completion the webhook missed.
This module is the thin outbound client for that query, plus a pure parser
and a test double.

Distinct from `bullet_api.pandadoc.client` (S1-24), which fetches one
document by id for the manual replay endpoint. This module is list-oriented:
it pages through `GET /public/v1/documents` filtered to completed documents.

Design notes:

- `parse_documents` is PURE and total - it never raises on malformed input
  (None, a list, a missing/typed-wrong `results` key, non-dict results,
  missing/empty ids) and returns `[]` instead. The cron feeds it whatever a
  page returned; a single bad page must never take the reconciliation down.
- `PandaDocDocument.raw` keeps the full result object so the cron can build a
  synthetic webhook payload from a reconciled document without a second fetch.
- `HttpPandaDocClient` builds an `httpx.AsyncClient` with an injectable
  transport so tests drive it via `httpx.MockTransport` with no network call.

PandaDoc API facts (verified against developers.pandadoc.com, OpenAPI 7.29.1):

- Endpoint: GET {base_url}/public/v1/documents
- Auth: header `Authorization: API-Key <key>` (the literal word "API-Key",
  a space, then the key - NOT Bearer).
- Completed filter: `status=2` (status is an integer enum; 2 == completed)
  combined with `completed_from=<ISO 8601>` (>= date_completed).
- Pagination: `count` (page size, max 100) and `page` (1-based). Loop pages,
  stopping when a page returns fewer than `count` results (or zero).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx

# PandaDoc REST base. The list endpoint is GET {base}/public/v1/documents and
# auth is the header `Authorization: API-Key <key>` (NOT Bearer).
PANDADOC_API_BASE_URL = "https://api.pandadoc.com"

# Numeric status enum value for `document.completed` in the list-documents
# `status` query param (0=draft, 1=sent, 2=completed, ...).
PANDADOC_STATUS_COMPLETED = 2

# Page size for the list query. PandaDoc caps `count` at 100.
_PAGE_SIZE = 100

# Hard cap on pages walked per call, so a misbehaving API (or an off-by-one in
# the short-page stop condition) can never spin forever. 50 pages * 100 = 5000
# completed documents per reconciliation window, far above any realistic day.
_MAX_PAGES = 50


@dataclass(frozen=True)
class PandaDocDocument:
    """A completed PandaDoc document, projected from one list result.

    `raw` is the full result object so the cron can synthesise a webhook-shaped
    payload for a reconciled document without re-fetching it.
    """

    document_id: str
    name: str
    status: str
    date_completed: str | None
    raw: dict


class PandaDocClient(Protocol):
    async def list_completed_documents(self, completed_from: datetime) -> list[PandaDocDocument]:
        """Return completed documents with date_completed >= `completed_from`."""
        ...


def parse_documents(payload: object) -> list[PandaDocDocument]:
    """Map a PandaDoc list-documents response to `PandaDocDocument`s.

    PURE and total. Expects `{"results": [{...}, ...]}`. Emits one document per
    result that has a non-empty string `id`; every other result is skipped.
    `date_completed` is `result.get("date_completed") or None` (so missing,
    null, or empty-string all collapse to None).

    Never raises on malformed input. Anything that is not a dict with a list
    `results` (None, a bare list, `{}`, `{"results": "x"}`, etc.) yields `[]`,
    and any non-dict result inside `results` is skipped.
    """
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []

    documents: list[PandaDocDocument] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        document_id = result.get("id")
        if not isinstance(document_id, str) or not document_id:
            continue
        documents.append(
            PandaDocDocument(
                document_id=document_id,
                name=str(result.get("name") or ""),
                status=str(result.get("status") or ""),
                date_completed=result.get("date_completed") or None,
                raw=result,
            )
        )
    return documents


class HttpPandaDocClient:
    """Production list client - pages GET /public/v1/documents for completed docs.

    Accepts an optional httpx transport so tests inject a `MockTransport`
    without a network call (production passes None and httpx uses its default
    transport). The client is built with an explicit `base_url` so MockTransport
    handlers see the right request path.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = PANDADOC_API_BASE_URL,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    async def list_completed_documents(self, completed_from: datetime) -> list[PandaDocDocument]:
        if not self._api_key:
            # Fail loudly rather than silently return nothing, mirroring
            # HttpPandaDocClient.fetch_document in bullet_api.pandadoc.client.
            raise RuntimeError(
                "PANDADOC_API_KEY is empty; cannot list completed documents. "
                "Set it on the Render env group."
            )

        documents: list[PandaDocDocument] = []
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            for page in range(1, _MAX_PAGES + 1):
                response = await client.get(
                    "/public/v1/documents",
                    params={
                        "status": PANDADOC_STATUS_COMPLETED,
                        "completed_from": completed_from.isoformat(),
                        "count": _PAGE_SIZE,
                        "page": page,
                    },
                    headers={"Authorization": f"API-Key {self._api_key}"},
                )
                response.raise_for_status()
                page_results = (response.json() or {}).get("results")
                documents.extend(parse_documents(response.json()))
                # Stop on a short or empty page: there is no further page.
                if not isinstance(page_results, list) or len(page_results) < _PAGE_SIZE:
                    break
        return documents


@dataclass
class FakePandaDocClient:
    """Test double. Returns its canned `docs` and records each `completed_from`
    it was called with in `calls`, so callers can assert on the watermark."""

    docs: list[PandaDocDocument] = field(default_factory=list)
    calls: list[datetime] = field(default_factory=list)

    async def list_completed_documents(self, completed_from: datetime) -> list[PandaDocDocument]:
        self.calls.append(completed_from)
        return list(self.docs)
