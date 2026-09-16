"""add clients.identity_key (+ postal_code) + possible-duplicate flag (S1-26c)

Revision ID: 0013_clients_identity_key
Revises: 0012_platform_openai
Create Date: 2026-07-29

S1-26c re-keys the returning-client check from `email` to a normalized
identity key: `first6(normalize(business_name)) + "|" + normalize(postcode)`.
This migration adds the columns that key lives in and the human-review flag
the dedup guard raises:

- `postal_code` TEXT NULL - the extracted `Company.Zip` was previously logged
  and discarded (no column existed). Persist it so `identity_key` is
  reproducible/auditable and the dedup guard can re-derive it.
- `identity_key` TEXT NULL - the match key. NULL when it cannot be computed
  (no usable business name), which makes the match self-skip (fail-safe to
  CREATE). Indexed but NOT unique: returning-client second-site rows legitimately
  share the same key (that is the whole point), mirroring the non-unique
  `ix_clients_email`.
- `possible_duplicate` BOOLEAN NOT NULL DEFAULT false - raised when an
  `identity_key` collision is found but the full normalized names diverge
  (a lenient-prefix false match, e.g. "Fitness First" vs "Fitness Studio" at
  the same postcode). The row is created as its own client and flagged for a
  human rather than auto-merged.
- `possible_duplicate_of` UUID NULL FK->clients.id ON DELETE SET NULL - the
  candidate sibling the flag points at, mirroring the `parent_client_id`
  self-FK pattern.
- `address` TEXT NULL - the extracted `Company.Address` was, like `postal_code`
  before it, read and then discarded. It is NOT a corroborating signal: review
  round 2 narrowed `identity_key.corroborating_signal_agrees` to PHONE ONLY
  because `Company.Address` and `Company.Zip` are read from the same HubSpot
  company record, so the address agrees exactly when the key already agrees and
  corroborates nothing extra.
  It IS a disqualifier (review round 5, finding 1): a signal that collapses with
  the key can never GRANT a link it did not already imply, but a DIFFERING
  address can still refuse one. So the returning-client auto-link requires name
  + postcode (the key) PLUS phone agreement, AND no actively-disagreeing
  address. Absence abstains rather than blocking - see
  `identity_key.addresses_materially_diverge`.
  CORRECTED (review round 6): the round-5 wording claimed this closes the
  one-owner-two-sites case. It does not. `Company.Address` and `Company.Zip`
  come from the SAME HubSpot company record, so one owner with one company
  record and two deals gives two rows identical on address as well as postcode -
  every bar clears and site 2 still auto-links. Bar 4 fires only when two rows
  come from DIFFERENT company records sharing a postcode. Closing the real case
  needs `hubspot_company_id`, which Bullet's documents do not currently carry -
  see `_pick_sibling`'s docstring for the full trace.
- `possible_duplicate_ghl_id` TEXT NULL - the candidate when the collision is
  against a GHL LOCATION rather than a clients row. The live GHL lookup can
  find an existing sub-account we cannot corroborate (a legacy location with
  no postcode on file); we provision this signing its own sub-account and
  record which location we suspect, so the human resolving the flag knows
  exactly what to merge into instead of hunting for it in the agency.

All columns are additive + nullable/defaulted, so UPGRADE is safe with no
backfill (the clients table is empty on staging, and pre-existing rows simply
carry NULL `identity_key` and fall through to CREATE). DOWNGRADE IS NOT
LOSSLESS and must not be described as safe (round 12, P2 - the previous
wording said "safe forward-only" while `downgrade()` silently drops
`postal_code`, `address`, and every human review decision recorded in the
possible-duplicate columns): run it only on a database whose rows you are
prepared to lose those columns from.

A trigger (`trg_clients_clear_orphaned_duplicate_flag`) keeps the flag
actionable (round 12, P2): `possible_duplicate_of` is ON DELETE SET NULL, so
deleting the candidate used to clear the POINTER while leaving
`possible_duplicate = true` - an un-actionable notice nothing could clear.
The trigger clears the flag when the pointer transitions away and no other
candidate (`possible_duplicate_ghl_id`) remains to act on.

WARNING for whoever changes the normalizer next: `identity_key` is DERIVED and
STORED, so it is a snapshot of `worker/identity_key.py` at the moment each row
was written. Any change to `normalize_name` / `normalize_postcode` /
`identity_name` silently splits the population - old rows keep keys the new
code can no longer produce, so a genuine returning client stops matching and
gets a duplicate sub-account. Such a change MUST ship with a recompute of every
non-NULL `identity_key`. THIS OBLIGATION IS NOW ENFORCED, NOT ADVISORY (round
12, P1.6): `render.yaml` ships `preDeployCommand: uv run alembic upgrade head`
in the same PR as this migration, so "0013 has never been applied outside
local dev" stops being true on the first staging deploy after merge - and
`review_gate.py`'s G7 check fails CI when `worker/identity_key.py`'s
normalization functions change with no migration touching `identity_key` in
the same diff (S1-26h owns the recompute tooling itself).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_clients_identity_key"
down_revision: str | Sequence[str] | None = "0012_platform_openai"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Fail fast instead of queueing behind an open transaction (review round
    # 5). Every ALTER TABLE below needs ACCESS EXCLUSIVE on `clients`, and
    # `create_ghl_subaccount_core` holds its transaction open ACROSS a GHL HTTP
    # call with a 10s timeout. Without a lock_timeout the migration waits for
    # that lock - and because a pending ACCESS EXCLUSIVE request blocks every
    # later reader too, the whole table stalls behind it: the dashboard's
    # `/clients` queries included. `preDeployCommand` runs this before the new
    # code serves traffic, so a timeout here aborts the deploy cleanly and
    # leaves the OLD code running, which is the outcome we want over a
    # site-wide stall. 5s is comfortably longer than any healthy statement here
    # and shorter than one in-flight GHL call.
    op.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    op.add_column("clients", sa.Column("postal_code", sa.Text(), nullable=True))
    op.add_column("clients", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("clients", sa.Column("identity_key", sa.Text(), nullable=True))
    op.add_column(
        "clients",
        sa.Column(
            "possible_duplicate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "clients",
        sa.Column(
            "possible_duplicate_of",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("clients", sa.Column("possible_duplicate_ghl_id", sa.Text(), nullable=True))
    op.create_index("ix_clients_identity_key", "clients", ["identity_key"])
    # Keep the flag actionable when its candidate disappears (round 12, P2).
    # The FK's ON DELETE SET NULL fires this UPDATE trigger; when the pointer
    # transitions away and no GHL-location candidate remains either, the flag
    # would point at nothing forever (no clearing endpoint exists until
    # S1-26e), so it clears with it. A deliberate app write is unaffected:
    # `_clear_possible_duplicate` clears the flag itself, and
    # `_flag_possible_duplicate` only ever sets pointers via COALESCE.
    op.execute(
        sa.text(
            """
            CREATE FUNCTION clients_clear_orphaned_duplicate_flag() RETURNS trigger AS $$
            BEGIN
                IF NEW.possible_duplicate
                   AND OLD.possible_duplicate_of IS NOT NULL
                   AND NEW.possible_duplicate_of IS NULL
                   AND NEW.possible_duplicate_ghl_id IS NULL THEN
                    NEW.possible_duplicate := false;
                END IF;
                RETURN NEW;
            END
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_clients_clear_orphaned_duplicate_flag "
            "BEFORE UPDATE OF possible_duplicate_of ON clients "
            "FOR EACH ROW EXECUTE FUNCTION clients_clear_orphaned_duplicate_flag()"
        )
    )
    # SET LOCAL above is TRANSACTION-scoped, and env.py runs `upgrade head` as
    # ONE transaction (no transaction_per_migration), so without this reset the
    # 5s lock_timeout would silently apply to every LATER migration in the same
    # batch (round 12, P2) - changing their failure semantics from "wait" to
    # "abort" with nothing in their own files saying so.
    op.execute(sa.text("SET LOCAL lock_timeout = DEFAULT"))


def downgrade() -> None:
    # NOT LOSSLESS (round 12, P2): drops `postal_code`, `address`, and the
    # possible-duplicate columns - including every human review decision
    # recorded in them. See the module docstring.
    op.execute(sa.text("DROP TRIGGER trg_clients_clear_orphaned_duplicate_flag ON clients"))
    op.execute(sa.text("DROP FUNCTION clients_clear_orphaned_duplicate_flag()"))
    op.drop_index("ix_clients_identity_key", table_name="clients")
    op.drop_column("clients", "possible_duplicate_ghl_id")
    op.drop_column("clients", "possible_duplicate_of")
    op.drop_column("clients", "possible_duplicate")
    op.drop_column("clients", "identity_key")
    op.drop_column("clients", "address")
    op.drop_column("clients", "postal_code")
