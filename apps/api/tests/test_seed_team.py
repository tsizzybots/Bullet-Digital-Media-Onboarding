"""Tests for the S1-11 team seeder."""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.scripts.seed_team import TEAM_USERS, seed_team

# Use a separate prefix so these tests never collide with the real team
# rows that may exist in Neon staging from earlier seeder runs. Roles
# still match the user_role enum.
_TEST_USERS = tuple(
    type(u)(email=f"test_{u.email}", full_name=u.full_name, role=u.role)
    for u in TEAM_USERS
)


@pytest.mark.db
async def test_seeder_is_idempotent(async_session: AsyncSession) -> None:
    """Run twice - second pass must report all rows already present and
    leave the table count unchanged."""
    first_pass = await seed_team(session=async_session, users=_TEST_USERS)
    second_pass = await seed_team(session=async_session, users=_TEST_USERS)

    # First pass should report new temp passwords for all four rows.
    assert all(password is not None for password in first_pass.values())
    # Second pass should report None for every row (already seeded).
    assert all(password is None for password in second_pass.values())

    emails = [u.email for u in _TEST_USERS]
    result = await async_session.execute(
        text(
            "SELECT count(*) FROM users WHERE email = ANY(:emails)"
        ),
        {"emails": emails},
    )
    # Exactly 4 rows for the 4 test users; not 8.
    assert result.scalar_one() == len(_TEST_USERS)


@pytest.mark.db
async def test_seeded_passwords_verify_under_argon2id(
    async_session: AsyncSession,
) -> None:
    """The temp password returned by seed_team() must verify against the
    stored hash via argon2id.PasswordHasher.verify()."""
    generated = await seed_team(session=async_session, users=_TEST_USERS)
    hasher = PasswordHasher()
    for user in _TEST_USERS:
        password = generated[user.email]
        assert password is not None, (
            f"{user.email} expected a fresh temp password on first seed"
        )
        result = await async_session.execute(
            text("SELECT password_hash FROM users WHERE email = :e"),
            {"e": user.email},
        )
        stored_hash = result.scalar_one()
        # argon2 verify() returns True on match or raises on mismatch.
        assert hasher.verify(stored_hash, password) is True


@pytest.mark.db
async def test_seeded_users_are_email_unconfirmed(
    async_session: AsyncSession,
) -> None:
    """Seeded users start with email_confirmed=false so they cannot log
    in until the S1-14 confirmation flow flips the flag."""
    await seed_team(session=async_session, users=_TEST_USERS)
    emails = [u.email for u in _TEST_USERS]
    result = await async_session.execute(
        text(
            "SELECT email, email_confirmed FROM users "
            "WHERE email = ANY(:emails)"
        ),
        {"emails": emails},
    )
    rows = result.all()
    assert len(rows) == len(_TEST_USERS)
    for _, confirmed in rows:
        assert confirmed is False


@pytest.mark.db
async def test_seeded_roles_match_brief(
    async_session: AsyncSession,
) -> None:
    """John + Stephen are founders, Max + Luchiano are PDs - the seeder
    must persist exactly those role assignments."""
    await seed_team(session=async_session, users=_TEST_USERS)
    result = await async_session.execute(
        text(
            "SELECT email, role FROM users "
            "WHERE email = ANY(:emails) "
            "ORDER BY email"
        ),
        {"emails": [u.email for u in _TEST_USERS]},
    )
    actual = {email: role for email, role in result.all()}
    expected = {u.email: u.role for u in _TEST_USERS}
    assert actual == expected
