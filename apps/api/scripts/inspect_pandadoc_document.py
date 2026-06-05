"""Inspect a real PandaDoc document's shape to pin S1-25a field names.

Fetches `GET /public/v1/documents/{id}/details` against the API key in
`.env` and prints field names + types only (no values). Used once to
verify Bullet's UK template token/field names; re-run against the INT key
to confirm the shapes match. Auto-picks the most recent completed doc if
no id is passed.

Run from the repo root:

    uv run python apps/api/scripts/inspect_pandadoc_document.py
    uv run python apps/api/scripts/inspect_pandadoc_document.py <doc-id>
    uv run python apps/api/scripts/inspect_pandadoc_document.py --with-raw
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from bullet_api.config import get_settings
from bullet_api.integrations.pandadoc_client import HttpPandaDocClient


def _mask(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return f"<{type(value).__name__}>"
    if isinstance(value, str):
        return f"<str len={len(value)}>"
    if isinstance(value, list):
        return [_mask(v) for v in value]
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items()}
    return f"<{type(value).__name__}>"


def _summary(body: dict) -> dict:
    out: dict = {"top_level_keys": sorted(body.keys())}

    for key in ("tokens", "fields"):
        items = body.get(key)
        if isinstance(items, list):
            out[f"{key}_names"] = sorted(
                {x.get("name") for x in items if isinstance(x, dict) and x.get("name")}
            )

    recipients = body.get("recipients")
    if isinstance(recipients, list) and recipients:
        if isinstance(recipients[0], dict):
            out["recipients[0]_keys"] = sorted(recipients[0].keys())
        out["recipients_count"] = len(recipients)

    metadata = body.get("metadata")
    out["metadata_keys"] = (
        sorted(metadata.keys()) if isinstance(metadata, dict) else f"{type(metadata).__name__}"
    )
    return out


async def _fetch(api_key: str, base_url: str, document_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{base_url.rstrip('/')}/public/v1/documents/{document_id}/details",
            headers={"Authorization": f"API-Key {api_key}"},
        )
    response.raise_for_status()
    return response.json()


async def _pick_recent(api_key: str, base_url: str) -> str | None:
    client = HttpPandaDocClient(api_key=api_key, base_url=base_url)
    docs = await client.list_completed_documents(datetime.now(UTC) - timedelta(days=365))
    if not docs:
        return None
    return max(docs, key=lambda d: d.date_completed or "").document_id


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    write_raw = "--with-raw" in sys.argv[1:]

    settings = get_settings()
    if not settings.pandadoc_api_key:
        print("PANDADOC_API_KEY is empty; cannot inspect.", file=sys.stderr)
        return 1

    document_id = args[0] if args else None
    if document_id is None:
        document_id = await _pick_recent(settings.pandadoc_api_key, settings.pandadoc_api_base_url)
        if document_id is None:
            print("No completed documents found in the last year.", file=sys.stderr)
            return 1
        print(f"Auto-picked most-recent completed document id: {document_id}\n")

    body = await _fetch(settings.pandadoc_api_key, settings.pandadoc_api_base_url, document_id)

    print("=== SHAPE SUMMARY (safe to share) ===")
    print(json.dumps(_summary(body), indent=2, sort_keys=True))

    out_dir = Path(__file__).resolve().parents[1] / ".tmp"
    out_dir.mkdir(exist_ok=True)
    masked_path = out_dir / f"pandadoc_detail_{document_id}_masked.json"
    masked_path.write_text(json.dumps(_mask(body), indent=2, sort_keys=True))
    print(f"\nMasked body written to: {masked_path}")

    if write_raw:
        raw_path = out_dir / f"pandadoc_detail_{document_id}_raw.json"
        raw_path.write_text(json.dumps(body, indent=2, sort_keys=True))
        print(f"Raw body (LOCAL ONLY, has PII) written to: {raw_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
