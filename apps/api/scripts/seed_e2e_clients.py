"""Seed deterministic clients for the Playwright dashboard specs (S1-31 + S1-32).

Run by `e2e/global-setup.ts` after the user seed, so the `/clients` board has a
known set of rows in distinct steps. Idempotent: each client upserts on the
unique `clients.pandadoc_document_id` (a synthetic `e2e-*` id), since
`clients.email` is intentionally not unique.

Standalone dev/test script: no `apps/api/src/**` changes, no endpoints, no
OpenAPI impact. All output goes to stderr (the harness does not parse it).

Usage (from apps/api, DATABASE_URL set + migrated):
    uv run python scripts/seed_e2e_clients.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.db import AsyncSessionLocal, engine
from bullet_api.seed_safety import assert_local_seed_db

# (synthetic pandadoc_document_id, business_name, email, current_step)
E2E_CLIENTS: list[tuple[str, str, str, str]] = [
    ("e2e-alpha", "E2E Alpha Gym", "alpha@e2e.example", "sales_call"),
    ("e2e-bravo", "E2E Bravo Fitness", "bravo@e2e.example", "signed"),
    ("e2e-charlie", "E2E Charlie Studio", "charlie@e2e.example", "live"),
]

# Charlie carries a dead_lettered last action so the clients-list spec (S1-31)
# can assert the format.ts status->badge mapping (dead_lettered -> danger) live
# - the stand-in until the dashboard gets a JS unit runner (S1-36).
DEAD_LETTER_CLIENT_DOC_ID = "e2e-charlie"

# S1-32 detail-page fixtures: Bravo gets a PRD §7.1-shaped sales summary (the
# pinned S1-30 contract: one client_knowledge row per top-level field,
# source='sales_call') + one action carrying an inngest_run_id so the detail
# spec can assert the Inngest deep-link href. Alpha deliberately stays
# summary-less - it is the "no summary yet" fixture.
SUMMARY_CLIENT_DOC_ID = "e2e-bravo"
E2E_INNGEST_RUN_ID = "01E2ESEED0RUN0GHL0BRAVO00"
E2E_SUMMARY_FIELDS: dict[str, object] = {
    "business_type": "E2E test gym",
    "business_goals": ["Goal one", "Goal two"],
    "budget_range_usd": {"min": 1000, "max": 2000, "currency": "USD"},
    "pain_points": ["Pain one"],
    "red_flags": [],
    "next_steps": ["Step one"],
    "notable_quotes": [{"speaker": "Owner", "quote": "E2E quote", "timestamp_seconds": 60}],
}


async def upsert_client(
    session: AsyncSession,
    *,
    doc_id: str,
    business_name: str,
    email: str,
    current_step: str,
) -> uuid.UUID:
    """Upsert one client by its synthetic pandadoc_document_id. `step_entered_at`
    defaults to now() on insert and is left untouched on conflict."""
    result = await session.execute(
        text(
            "INSERT INTO clients "
            "  (email, legal_entity, business_name, current_step, "
            "   pandadoc_document_id) "
            "VALUES (:email, :business, :business, :step, :doc_id) "
            "ON CONFLICT (pandadoc_document_id) DO UPDATE SET "
            "  business_name = EXCLUDED.business_name, "
            "  current_step = EXCLUDED.current_step "
            "RETURNING id"
        ),
        {
            "email": email,
            "business": business_name,
            "step": current_step,
            "doc_id": doc_id,
        },
    )
    return result.scalar_one()


async def seed_dead_lettered_action(session: AsyncSession, client_id: uuid.UUID) -> None:
    """A single dead_lettered action so the list renders the danger badge."""
    await session.execute(
        text(
            "INSERT INTO platform_actions "
            "  (client_id, platform, action, idempotency_key, status, started_at) "
            "VALUES (:cid, 'ghl', 'create_subaccount', :key, 'dead_lettered', now()) "
            "ON CONFLICT (idempotency_key) DO UPDATE SET status = EXCLUDED.status"
        ),
        {"cid": client_id, "key": f"e2eseed:{client_id}:ghl:dead_lettered"},
    )


async def seed_detail_fixtures(session: AsyncSession, client_id: uuid.UUID) -> None:
    """Summary batch + Inngest-linked action for the detail-page spec.
    Delete+insert keeps it idempotent (client_knowledge has no unique key)."""
    await session.execute(
        text("DELETE FROM client_knowledge WHERE client_id = :cid AND source = 'sales_call'"),
        {"cid": client_id},
    )
    for key, value in E2E_SUMMARY_FIELDS.items():
        await session.execute(
            text(
                "INSERT INTO client_knowledge "
                "  (client_id, source, key, value, value_text, captured_at) "
                "VALUES (:cid, 'sales_call', :key, cast(:value AS jsonb), "
                "        :value_text, now())"
            ),
            {
                "cid": client_id,
                "key": key,
                "value": json.dumps(value),
                "value_text": json.dumps(value) if not isinstance(value, str) else value,
            },
        )
    await session.execute(
        text(
            "INSERT INTO platform_actions "
            "  (client_id, platform, action, idempotency_key, status, "
            "   started_at, inngest_run_id) "
            "VALUES (:cid, 'ghl', 'create_subaccount', :key, 'success', "
            "        now(), :run_id) "
            "ON CONFLICT (idempotency_key) DO UPDATE SET "
            "  inngest_run_id = EXCLUDED.inngest_run_id"
        ),
        {
            "cid": client_id,
            "key": f"e2eseed:{client_id}:ghl:create_subaccount",
            "run_id": E2E_INNGEST_RUN_ID,
        },
    )


async def seed_e2e_clients() -> int:
    try:
        async with AsyncSessionLocal() as session:
            for doc_id, business_name, email, step in E2E_CLIENTS:
                client_id = await upsert_client(
                    session,
                    doc_id=doc_id,
                    business_name=business_name,
                    email=email,
                    current_step=step,
                )
                if doc_id == DEAD_LETTER_CLIENT_DOC_ID:
                    await seed_dead_lettered_action(session, client_id)
                if doc_id == SUMMARY_CLIENT_DOC_ID:
                    await seed_detail_fixtures(session, client_id)
            await session.commit()
    finally:
        await engine.dispose()
    return len(E2E_CLIENTS)


def main() -> None:
    assert_local_seed_db()
    count = asyncio.run(seed_e2e_clients())
    print(f"Seeded {count} E2E clients.", file=sys.stderr)


if __name__ == "__main__":
    main()
