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
  before it, read and then discarded. It is persisted now because it is one of
  the two CORROBORATING SIGNALS (phone or address line) that a returning-client
  auto-link requires: name + postcode alone is not proof of one business, since
  `Company.Zip` is the company address rather than the studio's.
- `possible_duplicate_ghl_id` TEXT NULL - the candidate when the collision is
  against a GHL LOCATION rather than a clients row. The live GHL lookup can
  find an existing sub-account we cannot corroborate (a legacy location with
  no postcode on file); we provision this signing its own sub-account and
  record which location we suspect, so the human resolving the flag knows
  exactly what to merge into instead of hunting for it in the agency.

All additive + nullable/defaulted, so this is a safe forward-only change; no
backfill is required (the clients table is empty on staging, and pre-existing
rows simply carry NULL `identity_key` and fall through to CREATE).

WARNING for whoever changes the normalizer next: `identity_key` is DERIVED and
STORED, so it is a snapshot of `worker/identity_key.py` at the moment each row
was written. Any change to `normalize_name` / `normalize_postcode` /
`identity_name` silently splits the population - old rows keep keys the new
code can no longer produce, so a genuine returning client stops matching and
gets a duplicate sub-account. Such a change MUST ship with a recompute of every
non-NULL `identity_key`. This is free TODAY only because 0013 has never been
applied outside a local dev database, so no stored keys exist yet.
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


def downgrade() -> None:
    op.drop_index("ix_clients_identity_key", table_name="clients")
    op.drop_column("clients", "possible_duplicate_ghl_id")
    op.drop_column("clients", "possible_duplicate_of")
    op.drop_column("clients", "possible_duplicate")
    op.drop_column("clients", "identity_key")
    op.drop_column("clients", "address")
    op.drop_column("clients", "postal_code")
