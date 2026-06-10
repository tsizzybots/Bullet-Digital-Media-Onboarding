"""Seed sample clients so the dashboard `/clients` board (S1-31) is viewable
locally with realistic data, even though no real signings exist in dev.

Inserts one confirmed founder login user plus five sample gym/fitness clients
spread across the onboarding steps, each with a couple of `platform_actions`
rows in a mix of statuses, so every step badge and every action-status colour
(success / in-flight / failed) shows up in the table.

Idempotent and re-runnable:
- the dev user upserts on the unique `users.email`;
- each client upserts on the unique `clients.pandadoc_document_id` (we mint a
  synthetic `devseed-*` id per client - `clients.email` is intentionally NOT
  unique, since returning clients share an email);
- each action upserts on the unique `platform_actions.idempotency_key`
  (`devseed:<email>:<platform>:<action>`).

`step_entered_at` is stamped relative to now so the "time in step" column shows
a spread (minutes -> days). This is a standalone dev/test script: it does not
touch `apps/api/src/**`, add endpoints, or affect the OpenAPI schema.

Usage (from apps/api, with DATABASE_URL set + migrations applied):
    uv run python scripts/seed_dev_clients.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from bullet_api.config import get_async_database_url, get_settings

# A confirmed founder so you can log straight into the dashboard in dev.
DEV_EMAIL = "dev@bulletdigitalmedia.com"
DEV_PASSWORD = "DevPassw0rd!seed"


@dataclass(frozen=True)
class ActionSpec:
    platform: str
    action: str
    status: str
    started_minutes_ago: int


@dataclass(frozen=True)
class ClientSpec:
    slug: str  # -> synthetic pandadoc_document_id (the upsert key)
    business_name: str
    contact_first_name: str
    contact_last_name: str
    email: str
    current_step: str
    step_age: timedelta
    actions: list[ActionSpec] = field(default_factory=list)


CLIENTS: list[ClientSpec] = [
    ClientSpec(
        slug="iron-forge",
        business_name="Iron Forge Gym",
        contact_first_name="Marcus",
        contact_last_name="Reed",
        email="marcus@ironforge.example",
        current_step="sales_call",
        step_age=timedelta(minutes=18),
    ),
    ClientSpec(
        slug="apex-athletics",
        business_name="Apex Athletics",
        contact_first_name="Priya",
        contact_last_name="Shah",
        email="priya@apexathletics.example",
        current_step="agreement",
        step_age=timedelta(minutes=45),
        actions=[
            ActionSpec("pandadoc", "send_agreement", "failed", 40),
        ],
    ),
    ClientSpec(
        slug="peakfit-studio",
        business_name="PeakFit Studio",
        contact_first_name="Dan",
        contact_last_name="Whitlock",
        email="dan@peakfit.example",
        current_step="signed",
        step_age=timedelta(hours=2),
        actions=[
            ActionSpec("ghl", "create_subaccount", "success", 118),
            ActionSpec("stripe", "create_customer", "in_progress", 5),
        ],
    ),
    ClientSpec(
        slug="titan-strength",
        business_name="Titan Strength Co",
        contact_first_name="Elena",
        contact_last_name="Marsh",
        email="elena@titanstrength.example",
        current_step="portal",
        step_age=timedelta(days=1, hours=3),
        actions=[
            ActionSpec("ghl", "create_subaccount", "success", 1620),
            ActionSpec("asana", "create_project", "success", 1610),
        ],
    ),
    ClientSpec(
        slug="flexzone-fitness",
        business_name="FlexZone Fitness",
        contact_first_name="Omar",
        contact_last_name="Ali",
        email="omar@flexzone.example",
        current_step="build",
        step_age=timedelta(days=4),
        actions=[
            ActionSpec("ghl", "create_subaccount", "success", 5760),
            ActionSpec("xero", "create_contact", "success", 5750),
            ActionSpec("meta", "create_ad_account", "success", 5740),
        ],
    ),
]


async def _upsert_dev_user(session: AsyncSession, password_hash: str) -> None:
    await session.execute(
        text(
            "INSERT INTO users "
            "  (id, email, password_hash, full_name, role, "
            "   email_confirmed, email_confirmed_at) "
            "VALUES (:id, :email, :ph, 'Dev Founder', 'founder', true, now()) "
            "ON CONFLICT (email) DO UPDATE SET "
            "  password_hash = EXCLUDED.password_hash, "
            "  email_confirmed = true, email_confirmed_at = now()"
        ),
        {"id": uuid.uuid4(), "email": DEV_EMAIL, "ph": password_hash},
    )


async def _upsert_client(session: AsyncSession, spec: ClientSpec, now: datetime) -> uuid.UUID:
    result = await session.execute(
        text(
            "INSERT INTO clients "
            "  (email, legal_entity, business_name, contact_first_name, "
            "   contact_last_name, current_step, step_entered_at, "
            "   pandadoc_document_id) "
            "VALUES (:email, :legal, :business, :first, :last, :step, "
            "        :step_at, :doc_id) "
            "ON CONFLICT (pandadoc_document_id) DO UPDATE SET "
            "  email = EXCLUDED.email, "
            "  business_name = EXCLUDED.business_name, "
            "  contact_first_name = EXCLUDED.contact_first_name, "
            "  contact_last_name = EXCLUDED.contact_last_name, "
            "  current_step = EXCLUDED.current_step, "
            "  step_entered_at = EXCLUDED.step_entered_at "
            "RETURNING id"
        ),
        {
            "email": spec.email,
            "legal": spec.business_name,
            "business": spec.business_name,
            "first": spec.contact_first_name,
            "last": spec.contact_last_name,
            "step": spec.current_step,
            "step_at": now - spec.step_age,
            "doc_id": f"devseed-{spec.slug}",
        },
    )
    return result.scalar_one()


async def _upsert_action(
    session: AsyncSession,
    client_id: uuid.UUID,
    spec: ClientSpec,
    action: ActionSpec,
    now: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_actions "
            "  (client_id, platform, action, idempotency_key, status, started_at) "
            "VALUES (:cid, :platform, :action, :key, :status, :started_at) "
            "ON CONFLICT (idempotency_key) DO UPDATE SET "
            "  status = EXCLUDED.status, started_at = EXCLUDED.started_at"
        ),
        {
            "cid": client_id,
            "platform": action.platform,
            "action": action.action,
            "key": f"devseed:{spec.email}:{action.platform}:{action.action}",
            "status": action.status,
            "started_at": now - timedelta(minutes=action.started_minutes_ago),
        },
    )


async def seed_dev_clients() -> int:
    hasher = PasswordHasher()
    password_hash = hasher.hash(DEV_PASSWORD)
    now = datetime.now(UTC)

    engine = create_async_engine(
        get_async_database_url(),
        poolclass=NullPool,
        future=True,
        connect_args={"ssl": get_settings().database_ssl_mode},
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await _upsert_dev_user(session, password_hash)
            for spec in CLIENTS:
                client_id = await _upsert_client(session, spec, now)
                for action in spec.actions:
                    await _upsert_action(session, client_id, spec, action, now)
            await session.commit()
    finally:
        await engine.dispose()

    return len(CLIENTS)


def main() -> None:
    count = asyncio.run(seed_dev_clients())
    print(
        f"Seeded {count} sample clients + dev user.\n"
        f"  Log in at the dashboard with:\n"
        f"    email:    {DEV_EMAIL}\n"
        f"    password: {DEV_PASSWORD}\n"
        f"  Then open /clients.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
