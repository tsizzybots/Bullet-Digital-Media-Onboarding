"""Tests for migration 0013's orphaned-duplicate-flag trigger (S1-26c).

Migration `0013_clients_identity_key` ships two pieces of DB-level conditional
logic that no test reached at all until round 14:

- the FK `clients.possible_duplicate_of -> clients.id ON DELETE SET NULL`;
- the trigger `trg_clients_clear_orphaned_duplicate_flag`, which runs
  `clients_clear_orphaned_duplicate_flag()` BEFORE UPDATE OF
  `possible_duplicate_of`.

The trigger body is a four-condition branch:

    IF NEW.possible_duplicate
       AND OLD.possible_duplicate_of IS NOT NULL
       AND NEW.possible_duplicate_of IS NULL
       AND NEW.possible_duplicate_ghl_id IS NULL THEN
        NEW.possible_duplicate := false;

Its purpose (round 12, P2) is that a flag must stay ACTIONABLE. Deleting the
candidate row nulls the pointer via the FK, and without the trigger the row
would be left `possible_duplicate = true` pointing at nothing - an un-clearable
notice, because no clearing endpoint exists until S1-26e.

WHAT THESE TESTS PIN, and why each one exists. The trigger's four conditions
are `AND`-ed, so each of the last three is only observable in a scenario the
other two do not already decide - one test per condition, or dropping that
condition goes unnoticed:

- the clearing branch itself, reached the way production reaches it (delete the
  candidate) and by a direct pointer clear;
- `OLD.possible_duplicate_of IS NOT NULL` - an update that nulls an ALREADY
  null pointer must not clear a flag that was never pointed anywhere;
- `NEW.possible_duplicate_of IS NULL` - REPOINTING at another candidate is not
  orphaning, so the flag stands;
- `NEW.possible_duplicate_ghl_id IS NULL` - a GHL-location candidate is still
  something a human can act on, so losing the row candidate is not orphaning
  either.

`NEW.possible_duplicate` (the first condition) is deliberately NOT given its
own test: when it is false the assignment would write `false` over `false`, so
the condition is a short-circuit with no observable effect. The unflagged case
is covered anyway by the bare-FK test below, which pins that the delete still
nulls the pointer and does not cascade.

The `BEFORE UPDATE OF possible_duplicate_of` column scope is likewise not
independently observable: `OLD ... IS NOT NULL AND NEW ... IS NULL` cannot hold
unless that column is in the UPDATE's SET list, which is exactly when `UPDATE
OF` fires. It is a narrowing optimisation, not a behavioural guard.

These are live-DB tests: the trigger exists because `alembic upgrade head` ran,
not because any Python object declares it, so they are all `@pytest.mark.db`
and they exercise the deployed schema directly.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_client(
    session: AsyncSession,
    *,
    email: str,
    possible_duplicate: bool = False,
    possible_duplicate_of: uuid.UUID | None = None,
    possible_duplicate_ghl_id: str | None = None,
) -> uuid.UUID:
    """Insert the minimal `clients` row these trigger tests need.

    Only the three columns Postgres actually demands (`email`, `legal_entity`,
    `current_step`) plus the possible-duplicate triple the trigger reads. The
    flag defaults to `false` and both candidate pointers default to absent,
    matching the column defaults, so a test that wants a raised flag or a
    candidate has to SAY so at the call site - the value the trigger branches
    on is never supplied by the fixture.
    """
    result = await session.execute(
        text(
            "INSERT INTO clients ("
            "  email, business_name, legal_entity, current_step, step_entered_at, "
            "  possible_duplicate, possible_duplicate_of, possible_duplicate_ghl_id"
            ") VALUES ("
            "  :email, :name, :name, 'signed', now(), "
            "  :possible_duplicate, :possible_duplicate_of, :possible_duplicate_ghl_id"
            ") RETURNING id"
        ),
        {
            "email": email,
            "name": "Trigger Fixture Gym",
            "possible_duplicate": possible_duplicate,
            "possible_duplicate_of": possible_duplicate_of,
            "possible_duplicate_ghl_id": possible_duplicate_ghl_id,
        },
    )
    return result.scalar_one()


async def _read_flag(
    session: AsyncSession, client_id: uuid.UUID
) -> tuple[bool, uuid.UUID | None, str | None]:
    row = await session.execute(
        text(
            "SELECT possible_duplicate, possible_duplicate_of, possible_duplicate_ghl_id "
            "FROM clients WHERE id = :id"
        ),
        {"id": client_id},
    )
    flag, pointer, ghl_id = row.one()
    return flag, pointer, ghl_id


async def _row_exists(session: AsyncSession, client_id: uuid.UUID) -> bool:
    row = await session.execute(
        text("SELECT count(*) FROM clients WHERE id = :id"), {"id": client_id}
    )
    return row.scalar_one() == 1


# --------------------------------------------------------------------------- #
# The clearing branch: all four conditions hold.
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_deleting_the_candidate_clears_the_orphaned_flag(
    async_session: AsyncSession,
) -> None:
    """The production path end to end: FK `ON DELETE SET NULL` fires the
    `UPDATE OF possible_duplicate_of`, which fires
    `clients_clear_orphaned_duplicate_flag()`, which lowers the flag.

    Also pins the FK ACTION itself. `SET NULL`, not `CASCADE`: deleting the
    candidate must never take the flagged client with it - that client is a
    real signing, the flag is only a note about it.
    """
    candidate_id = await _seed_client(async_session, email="candidate@trg-clear.example.com")
    flagged_id = await _seed_client(
        async_session,
        email="flagged@trg-clear.example.com",
        possible_duplicate=True,
        possible_duplicate_of=candidate_id,
    )

    flag, pointer, ghl_id = await _read_flag(async_session, flagged_id)
    assert (flag, pointer, ghl_id) == (True, candidate_id, None)

    await async_session.execute(text("DELETE FROM clients WHERE id = :id"), {"id": candidate_id})

    assert await _row_exists(async_session, flagged_id) is True
    flag, pointer, ghl_id = await _read_flag(async_session, flagged_id)
    assert pointer is None  # the FK's SET NULL landed
    assert flag is False  # ... and the trigger cleared the now-orphaned flag


@pytest.mark.db
async def test_clearing_the_pointer_directly_clears_the_flag(
    async_session: AsyncSession,
) -> None:
    """The same branch reached by a plain UPDATE rather than by a delete.

    Separates the two mechanisms: this proves the CLEARING is the trigger's
    doing and not some property of the FK action, so a reader cannot conclude
    from the delete test alone that `ON DELETE SET NULL` clears flags.
    """
    candidate_id = await _seed_client(async_session, email="candidate@trg-update.example.com")
    flagged_id = await _seed_client(
        async_session,
        email="flagged@trg-update.example.com",
        possible_duplicate=True,
        possible_duplicate_of=candidate_id,
    )

    await async_session.execute(
        text("UPDATE clients SET possible_duplicate_of = NULL WHERE id = :id"),
        {"id": flagged_id},
    )

    assert await _row_exists(async_session, candidate_id) is True  # nothing was deleted
    flag, pointer, _ = await _read_flag(async_session, flagged_id)
    assert (flag, pointer) == (False, None)


# --------------------------------------------------------------------------- #
# The non-clearing branch, one test per independently observable condition.
# --------------------------------------------------------------------------- #


@pytest.mark.db
async def test_a_remaining_ghl_candidate_keeps_the_flag_raised(
    async_session: AsyncSession,
) -> None:
    """Condition 4, `NEW.possible_duplicate_ghl_id IS NULL`.

    A client can be flagged against BOTH a sibling row and a GHL location (the
    two `_flag_possible_duplicate` call sites write into one row via COALESCE).
    Deleting the sibling still leaves the human something concrete to merge
    into, so the flag is not orphaned and must survive the delete.
    """
    candidate_id = await _seed_client(async_session, email="candidate@trg-ghl.example.com")
    flagged_id = await _seed_client(
        async_session,
        email="flagged@trg-ghl.example.com",
        possible_duplicate=True,
        possible_duplicate_of=candidate_id,
        possible_duplicate_ghl_id="loc_still_actionable",
    )

    await async_session.execute(text("DELETE FROM clients WHERE id = :id"), {"id": candidate_id})

    flag, pointer, ghl_id = await _read_flag(async_session, flagged_id)
    assert pointer is None  # the FK still nulled the row pointer
    assert flag is True  # ... but the flag stays actionable
    assert ghl_id == "loc_still_actionable"


@pytest.mark.db
async def test_repointing_at_another_candidate_keeps_the_flag_raised(
    async_session: AsyncSession,
) -> None:
    """Condition 3, `NEW.possible_duplicate_of IS NULL`.

    The trigger fires on every UPDATE that touches the pointer column, not only
    on the ones that null it. Moving the flag from one candidate to another is
    a re-aim, not an orphaning, and clearing the flag there would silently
    discard a review note the moment anyone corrected which row it points at.
    """
    first_candidate_id = await _seed_client(async_session, email="cand1@trg-repoint.example.com")
    second_candidate_id = await _seed_client(async_session, email="cand2@trg-repoint.example.com")
    flagged_id = await _seed_client(
        async_session,
        email="flagged@trg-repoint.example.com",
        possible_duplicate=True,
        possible_duplicate_of=first_candidate_id,
    )

    await async_session.execute(
        text("UPDATE clients SET possible_duplicate_of = :new WHERE id = :id"),
        {"new": second_candidate_id, "id": flagged_id},
    )

    flag, pointer, _ = await _read_flag(async_session, flagged_id)
    assert (flag, pointer) == (True, second_candidate_id)


@pytest.mark.db
async def test_nulling_an_already_null_pointer_leaves_the_flag_alone(
    async_session: AsyncSession,
) -> None:
    """Condition 2, `OLD.possible_duplicate_of IS NOT NULL`.

    `UPDATE OF possible_duplicate_of` fires whenever the column is in the SET
    list, whether or not its value changes - so a write that sets an already
    NULL pointer to NULL DOES reach the trigger. The guard reads OLD precisely
    so that such a write is not treated as an orphaning: this row's flag was
    never pointed at a candidate, so nothing about it just became
    un-actionable, and lowering it here would erase a review note no one
    resolved.

    This is the only scenario that isolates the OLD condition - every other
    test here is already decided by conditions 3 or 4 - so without it, deleting
    `OLD.possible_duplicate_of IS NOT NULL` from the trigger goes unnoticed.
    """
    flagged_id = await _seed_client(
        async_session,
        email="flagged@trg-old-null.example.com",
        possible_duplicate=True,
    )

    await async_session.execute(
        text("UPDATE clients SET possible_duplicate_of = NULL WHERE id = :id"),
        {"id": flagged_id},
    )

    flag, pointer, _ = await _read_flag(async_session, flagged_id)
    assert (flag, pointer) == (True, None)


@pytest.mark.db
async def test_a_ghl_only_flag_write_survives_the_trigger(
    async_session: AsyncSession,
) -> None:
    """The migration's own claim, made falsifiable: "A deliberate app write is
    unaffected ... `_flag_possible_duplicate` only ever sets pointers via
    COALESCE."

    That claim is load-bearing and non-obvious, because `_flag_possible_
    duplicate` names `possible_duplicate_of` in its SET list on EVERY call -
    `COALESCE(:sibling_id, possible_duplicate_of)` - so the GHL-only call
    (`sibling_id=None`) raises the flag with the trigger firing in the same
    statement. If the trigger were written on NEW alone it would lower the flag
    the app just raised, and the GHL-undecidable path would produce rows that
    are silently never flagged.

    The statement below is `_flag_possible_duplicate`'s SQL verbatim in its
    GHL-only form.
    """
    flagged_id = await _seed_client(async_session, email="flagged@trg-app-write.example.com")

    await async_session.execute(
        text(
            "UPDATE clients "
            "SET possible_duplicate = true, "
            "    possible_duplicate_of = COALESCE(:sibling_id, possible_duplicate_of), "
            "    possible_duplicate_ghl_id = COALESCE(:ghl_id, possible_duplicate_ghl_id) "
            "WHERE id = :client_id"
        ),
        {"sibling_id": None, "ghl_id": "loc_undecidable", "client_id": flagged_id},
    )

    flag, pointer, ghl_id = await _read_flag(async_session, flagged_id)
    assert (flag, pointer, ghl_id) == (True, None, "loc_undecidable")


@pytest.mark.db
async def test_deleting_the_candidate_of_an_unflagged_row_only_nulls_the_pointer(
    async_session: AsyncSession,
) -> None:
    """The FK action on its own, with the trigger a no-op (condition 1 false).

    `ON DELETE SET NULL` must null the pointer and leave the referencing row
    standing; the trigger must not invent a flag change on a row that was never
    flagged.
    """
    candidate_id = await _seed_client(async_session, email="candidate@trg-unflagged.example.com")
    unflagged_id = await _seed_client(
        async_session,
        email="unflagged@trg-unflagged.example.com",
        possible_duplicate=False,
        possible_duplicate_of=candidate_id,
    )

    await async_session.execute(text("DELETE FROM clients WHERE id = :id"), {"id": candidate_id})

    assert await _row_exists(async_session, unflagged_id) is True
    flag, pointer, _ = await _read_flag(async_session, unflagged_id)
    assert (flag, pointer) == (False, None)
