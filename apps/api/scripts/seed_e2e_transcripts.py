"""Seed a deterministic UNLINKED sales-call transcript for the S1-27a spec.

Run by `e2e/global-setup.ts` after the client seed, so the `/transcripts` page
has a known parked transcript to attach. Idempotent AND self-resetting: on
conflict it forces the row back to unlinked (client_id / linked_at / link_method
NULL), so a prior run that attached it does not break the next run.

Standalone dev/test script: no `apps/api/src/**` changes, no endpoints, no
OpenAPI impact. All output goes to stderr (the harness does not parse it).

Usage (from apps/api, DATABASE_URL set + migrated):
    uv run python scripts/seed_e2e_transcripts.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime

from sqlalchemy import text

from bullet_api.db import AsyncSessionLocal, engine
from bullet_api.seed_safety import assert_local_seed_db

# Deterministic transcript the spec recognises by its participant email. The
# email intentionally matches NO seeded client, so it stays unlinked (the manual
# fallback case the page exists for).
E2E_TRANSCRIPT_EXTERNAL_ID = "conferenceRecords/e2e-conf/transcripts/e2e-trans"
E2E_TRANSCRIPT_EMAILS = ["e2e-prospect@e2e.example"]
E2E_TRANSCRIPT_MEETING_START = datetime(2026, 6, 10, 14, 0, tzinfo=UTC)
E2E_TRANSCRIPT_CHARS = 1234


async def seed_e2e_transcripts() -> int:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text(
                    "INSERT INTO sales_call_transcripts "
                    "  (source, external_id, r2_key, participant_emails, "
                    "   meeting_start, transcript_chars) "
                    "VALUES ('google_meet', :eid, :r2, cast(:emails AS jsonb), "
                    "        :start, :chars) "
                    "ON CONFLICT (source, external_id) DO UPDATE SET "
                    "  client_id = NULL, linked_at = NULL, link_method = NULL, "
                    "  linked_by = NULL, participant_emails = EXCLUDED.participant_emails, "
                    "  meeting_start = EXCLUDED.meeting_start, "
                    "  transcript_chars = EXCLUDED.transcript_chars"
                ),
                {
                    "eid": E2E_TRANSCRIPT_EXTERNAL_ID,
                    "r2": f"sales-call-transcripts/{E2E_TRANSCRIPT_EXTERNAL_ID}.txt",
                    "emails": json.dumps(E2E_TRANSCRIPT_EMAILS),
                    "start": E2E_TRANSCRIPT_MEETING_START,
                    "chars": E2E_TRANSCRIPT_CHARS,
                },
            )
            await session.commit()
    finally:
        await engine.dispose()
    return 1


def main() -> None:
    assert_local_seed_db()
    count = asyncio.run(seed_e2e_transcripts())
    print(f"Seeded {count} unlinked E2E transcript.", file=sys.stderr)


if __name__ == "__main__":
    main()
