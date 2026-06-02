"""Seed two deterministic users for the Playwright E2E suite (S1-18).

The Playwright global-setup runs this before every session to guarantee a
known, reproducible auth fixture. It provisions:

- a *login* user (`email_confirmed = true`) that can log in immediately, and
- a *confirm* user (`email_confirmed = false`) that must complete the
  confirmation flow first, together with a freshly minted confirmation
  token produced by the production `generate_confirmation_token` helper.

Both users share a fixed password so specs can hardcode/read it. The
password is also echoed in the emitted JSON so the harness never has to
guess it.

The script is idempotent: each user is upserted by email
(`INSERT ... ON CONFLICT (email) DO UPDATE`) so re-running converges on the
same two rows (no duplicates) and resets the password / confirmation state
back to the known fixture values. The row id is stable across runs because
the conflict target is the unique `email`; the confirm token is minted from
the returned id on every run, so the token is always fresh and valid.

Output contract: exactly ONE line of JSON on stdout, nothing else (all
human/log text goes to stderr) so the global-setup can `JSON.parse` stdout
directly:

    {"login": {"email": "...", "password": "..."},
     "confirm": {"email": "...", "password": "...", "token": "..."}}

This is a standalone script (not importable package code). It does NOT
touch `apps/api/src/**`, add or change any API endpoint, or affect the
OpenAPI schema. It only reuses existing helpers from `bullet_api`.

Usage (from apps/api, with DATABASE_URL set and migrations applied):
    uv run python scripts/seed_e2e_users.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass

from argon2 import PasswordHasher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from bullet_api.auth.confirmation import generate_confirmation_token
from bullet_api.config import get_async_database_url, get_settings

# Fixed, known fixture password shared by both users. Deterministic so the
# E2E specs can read it from the emitted JSON (or hardcode it). This is a
# test-only credential against ephemeral seed accounts on a test database.
E2E_PASSWORD = "E2ePassw0rd!seed"

# A valid `user_role` enum value (migration 0006). Founder keeps the seed
# accounts free of any role-gated UI restrictions in the dashboard.
E2E_ROLE = "founder"


@dataclass(frozen=True)
class SeedSpec:
    email: str
    full_name: str
    email_confirmed: bool


LOGIN_USER = SeedSpec(
    email="e2e-login@bulletdigitalmedia.com",
    full_name="E2E Login Fixture",
    email_confirmed=True,
)
CONFIRM_USER = SeedSpec(
    email="e2e-confirm@bulletdigitalmedia.com",
    full_name="E2E Confirm Fixture",
    email_confirmed=False,
)


async def _upsert_user(
    session: AsyncSession,
    spec: SeedSpec,
    password_hash: str,
) -> uuid.UUID:
    """Upsert one seed user by email and return its stable id.

    `email_confirmed_at` is forced to `now()` for confirmed users and
    `NULL` for unconfirmed ones, so a re-run also resets the confirmation
    state (not just the password) back to the fixture's expected shape.
    """
    result = await session.execute(
        text(
            "INSERT INTO users "
            "  (id, email, password_hash, full_name, role, "
            "   email_confirmed, email_confirmed_at) "
            "VALUES "
            "  (:id, :email, :password_hash, :full_name, :role, "
            "   :email_confirmed, "
            "   CASE WHEN :email_confirmed THEN now() ELSE NULL END) "
            "ON CONFLICT (email) DO UPDATE SET "
            "  password_hash = EXCLUDED.password_hash, "
            "  full_name = EXCLUDED.full_name, "
            "  role = EXCLUDED.role, "
            "  email_confirmed = EXCLUDED.email_confirmed, "
            "  email_confirmed_at = EXCLUDED.email_confirmed_at "
            "RETURNING id"
        ),
        {
            "id": uuid.uuid4(),
            "email": spec.email,
            "password_hash": password_hash,
            "full_name": spec.full_name,
            "role": E2E_ROLE,
            "email_confirmed": spec.email_confirmed,
        },
    )
    row = result.first()
    if row is None:  # pragma: no cover - RETURNING always yields a row here
        raise RuntimeError(f"upsert returned no id for {spec.email}")
    return row[0]


async def seed_e2e_users() -> dict[str, object]:
    """Seed both fixture users and return the JSON-serialisable payload.

    Builds its own NullPool engine so the script holds no pooled
    connections at exit; the engine is disposed in a `finally` so a partial
    failure still releases the connection cleanly.
    """
    hasher = PasswordHasher()
    password_hash = hasher.hash(E2E_PASSWORD)

    engine = create_async_engine(
        get_async_database_url(),
        poolclass=NullPool,
        future=True,
        connect_args={"ssl": get_settings().database_ssl_mode},
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        async with session_factory() as session:
            await _upsert_user(session, LOGIN_USER, password_hash)
            confirm_id = await _upsert_user(session, CONFIRM_USER, password_hash)
            await session.commit()
        # Mint the token from the *persisted* confirm-user id (stable across
        # runs because the conflict target is the unique email column).
        token = generate_confirmation_token(confirm_id)
    finally:
        await engine.dispose()

    return {
        "login": {
            "email": LOGIN_USER.email,
            "password": E2E_PASSWORD,
        },
        "confirm": {
            "email": CONFIRM_USER.email,
            "password": E2E_PASSWORD,
            "token": token,
        },
    }


def main() -> None:
    """CLI entry point. Prints exactly one JSON line to stdout."""
    print("Seeding Playwright E2E fixture users...", file=sys.stderr)
    payload = asyncio.run(seed_e2e_users())
    # stdout: the single machine-readable line the harness parses.
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()
    print(
        f"Seeded {LOGIN_USER.email} (confirmed) and "
        f"{CONFIRM_USER.email} (unconfirmed, token minted).",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
