"""Idempotent seeder for the Bullet Digital Media team users (S1-11).

Per the brief, John Limber and Stephen Taylor are founders; Max and
Luchiano are the performance directors. Each gets a row in `users` with
an argon2id-hashed temporary password and `email_confirmed=false` so
they must complete the email-confirmation flow (S1-14) before they can
log in.

The seeder is idempotent: `INSERT ... ON CONFLICT (email) DO NOTHING`
means running twice produces the same state, no duplicate rows.

Usage:
    DATABASE_URL=... uv run python -m bullet_api.scripts.seed_team
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from collections.abc import Iterable
from dataclasses import dataclass

from argon2 import PasswordHasher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.db import AsyncSessionLocal


@dataclass(frozen=True)
class SeedUser:
    email: str
    full_name: str
    role: str  # must match the user_role enum from migration 0006


# The named team. Roles are the strings of the user_role enum.
TEAM_USERS: tuple[SeedUser, ...] = (
    SeedUser(
        email="john@bulletdigitalmedia.com",
        full_name="John Limber",
        role="founder",
    ),
    SeedUser(
        email="stephen@bulletdigitalmedia.com",
        full_name="Stephen Taylor",
        role="founder",
    ),
    SeedUser(
        email="max@bulletdigitalmedia.com",
        full_name="Max",
        role="performance_director",
    ),
    SeedUser(
        email="luchiano@bulletdigitalmedia.com",
        full_name="Luchiano",
        role="performance_director",
    ),
)


def _generate_temp_password() -> str:
    """Return ~16 chars of url-safe entropy (~96 bits)."""
    return secrets.token_urlsafe(12)


async def _seed_into(session: AsyncSession, users: Iterable[SeedUser]) -> dict[str, str | None]:
    """Insert any missing rows into the live session.

    Returns a `{email: temp_password|None}` map. `None` means the row
    already existed and was not touched, so no new password was generated.
    The caller decides whether to commit or roll back the session.
    """
    hasher = PasswordHasher()
    generated: dict[str, str | None] = {}
    for user in users:
        temp_password = _generate_temp_password()
        password_hash = hasher.hash(temp_password)
        result = await session.execute(
            text(
                "INSERT INTO users "
                "  (email, password_hash, full_name, role, email_confirmed) "
                "VALUES "
                "  (:e, :h, :n, :r, false) "
                "ON CONFLICT (email) DO NOTHING "
                "RETURNING id"
            ),
            {
                "e": user.email,
                "h": password_hash,
                "n": user.full_name,
                "r": user.role,
            },
        )
        # ON CONFLICT DO NOTHING returns zero rows if the email already
        # existed; only emit a temp password in the inserted case.
        if result.first() is None:
            generated[user.email] = None
        else:
            generated[user.email] = temp_password
    return generated


async def seed_team(
    session: AsyncSession | None = None,
    users: Iterable[SeedUser] = TEAM_USERS,
) -> dict[str, str | None]:
    """Seed the named team. When called without arguments, manages its
    own session and commits at the end (the production CLI entry point).
    When called with `session=`, the caller owns the transaction - used
    by tests to roll the inserts back."""
    if session is not None:
        return await _seed_into(session, users)
    async with AsyncSessionLocal() as managed_session:
        result = await _seed_into(managed_session, users)
        await managed_session.commit()
        return result


def main() -> None:
    """CLI entry point. Prints temp passwords for operator handoff."""
    results = asyncio.run(seed_team())
    print("Bullet Digital Media team seeder:")
    for email, password in results.items():
        if password is None:
            print(f"  {email}: already seeded (no change)")
        else:
            print(f"  {email}: TEMP PASSWORD = {password}")
    if all(value is None for value in results.values()):
        print("  All team users already exist; no new passwords generated.")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
