"""Seed three deterministic clients for the Playwright clients-list spec (S1-31).

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
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from bullet_api.config import get_async_database_url, get_settings

# (synthetic pandadoc_document_id, business_name, email, current_step)
E2E_CLIENTS: list[tuple[str, str, str, str]] = [
    ("e2e-alpha", "E2E Alpha Gym", "alpha@e2e.example", "sales_call"),
    ("e2e-bravo", "E2E Bravo Fitness", "bravo@e2e.example", "signed"),
    ("e2e-charlie", "E2E Charlie Studio", "charlie@e2e.example", "live"),
]


async def upsert_client(
    session: AsyncSession,
    *,
    doc_id: str,
    business_name: str,
    email: str,
    current_step: str,
) -> None:
    """Upsert one client by its synthetic pandadoc_document_id. `step_entered_at`
    defaults to now() on insert and is left untouched on conflict."""
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


async def seed_e2e_clients() -> int:
    engine = create_async_engine(
        get_async_database_url(),
        poolclass=NullPool,
        future=True,
        connect_args={"ssl": get_settings().database_ssl_mode},
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            for doc_id, business_name, email, step in E2E_CLIENTS:
                await upsert_client(
                    session,
                    doc_id=doc_id,
                    business_name=business_name,
                    email=email,
                    current_step=step,
                )
            await session.commit()
    finally:
        await engine.dispose()
    return len(E2E_CLIENTS)


def main() -> None:
    count = asyncio.run(seed_e2e_clients())
    print(f"Seeded {count} E2E clients.", file=sys.stderr)


if __name__ == "__main__":
    main()
