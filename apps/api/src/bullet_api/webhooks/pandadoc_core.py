"""Pure, dependency-free PandaDoc webhook helpers.

This module holds the correctness-critical logic for the PandaDoc webhook
ingest: HMAC signature verification and extraction of completed documents
from the posted event payload. It deliberately depends on the standard
library only (`hmac`, `hashlib`) so it can be unit-tested in isolation,
with no database, FastAPI, or Inngest involvement.

PandaDoc webhook facts (confirmed against developers.pandadoc.com):

- Signature: HMAC-SHA256 of the raw HTTP request body (UTF-8 bytes),
  keyed with the subscription "Shared Key", delivered as the `signature`
  URL query parameter, compared as a lowercase hex digest.
- Payload: a JSON array of event objects. Each event object carries a
  `data` object; the document id lives at `data.id` and the fully
  signed/executed state is `data.status == "document.completed"`.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

# The PandaDoc document status meaning "fully signed / executed".
PANDADOC_COMPLETED_STATUS = "document.completed"


def verify_pandadoc_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    """Return True iff `signature` is the hex HMAC-SHA256 of `raw_body`.

    The HMAC is keyed with `secret`. Comparison is constant-time via
    `hmac.compare_digest`.

    Fails closed: returns False when `signature` is None/empty or when
    `secret` is empty, regardless of any digest that might otherwise match.
    """
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@dataclass(frozen=True)
class SignedDocument:
    """A single PandaDoc document that reached the completed state."""

    document_id: str
    event: dict  # the individual PandaDoc event object this came from


def extract_signed_documents(payload: object) -> list[SignedDocument]:
    """Pull the documents that reached the signed/completed state.

    PandaDoc posts a JSON array of event objects; a single dict is also
    accepted defensively. An event counts as signed when its document
    status equals `PANDADOC_COMPLETED_STATUS` and it carries a non-empty
    document id.

    Returns one `SignedDocument` per matching event, de-duplicated by
    `document_id` (first occurrence wins) while preserving order.

    Never raises on malformed input (missing keys, wrong types, None,
    non-list/non-dict): returns an empty list instead.
    """
    if isinstance(payload, dict):
        events: list[object] = [payload]
    elif isinstance(payload, list):
        events = payload
    else:
        return []

    result: list[SignedDocument] = []
    seen: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        if data.get("status") != PANDADOC_COMPLETED_STATUS:
            continue
        document_id = data.get("id")
        if not isinstance(document_id, str) or not document_id:
            continue
        if document_id in seen:
            continue
        seen.add(document_id)
        result.append(SignedDocument(document_id=document_id, event=event))
    return result
