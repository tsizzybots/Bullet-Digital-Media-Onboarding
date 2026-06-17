"""Insert/upsert a single client - used by the clients-list spec (S1-31) to add
a client "server-side" mid-test and prove the dashboard's 10s poll surfaces it.

Upserts on the unique `clients.pandadoc_document_id` so a re-run is a no-op
duplicate. Standalone dev/test script; no `apps/api/src/**` or OpenAPI impact.

Usage (from apps/api, DATABASE_URL set + migrated):
    uv run python scripts/seed_one_client.py --doc-id e2e-delta \\
        --business "E2E Delta Strength" --email delta@e2e.example --step portal
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from bullet_api.db import AsyncSessionLocal, engine
from bullet_api.db.enums import CURRENT_STEP_VALUES
from bullet_api.seed_safety import assert_local_seed_db


async def upsert_one(*, doc_id: str, business_name: str, email: str, current_step: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text(
                    "INSERT INTO clients "
                    "  (email, legal_entity, business_name, current_step, "
                    "   pandadoc_document_id) "
                    "VALUES (:email, :business, :business, :step, :doc_id) "
                    "ON CONFLICT (pandadoc_document_id) DO UPDATE SET "
                    "  business_name = EXCLUDED.business_name, "
                    "  current_step = EXCLUDED.current_step"
                ),
                {
                    "email": email,
                    "business": business_name,
                    "step": current_step,
                    "doc_id": doc_id,
                },
            )
            await session.commit()
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Insert one client row.")
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--business", required=True)
    parser.add_argument("--email", required=True)
    # Validate against the enum here so a typo aborts with a clear message
    # instead of a Postgres 22P02 that global-setup.ts mislabels as a
    # DATABASE_URL / migrations problem.
    parser.add_argument("--step", required=True, choices=CURRENT_STEP_VALUES)
    args = parser.parse_args()

    assert_local_seed_db()
    asyncio.run(
        upsert_one(
            doc_id=args.doc_id,
            business_name=args.business,
            email=args.email,
            current_step=args.step,
        )
    )
    print(f"Inserted client {args.business} ({args.step}).", file=sys.stderr)


if __name__ == "__main__":
    main()
