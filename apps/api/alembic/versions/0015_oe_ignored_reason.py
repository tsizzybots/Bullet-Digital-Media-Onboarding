"""add onboarding_events.ignored_reason (S1-38 phase 1 agreement-type gate)

Revision ID: 0015_oe_ignored_reason
Revises: 0014_identity_key_rename
Create Date: 2026-08-24

Branched from `main` (`0012_platform_openai` is main's head today), NOT from
the still-unmerged `feat/s1-26bc-identity-key` branch (whose own `0013` adds
unrelated `clients` columns) - S1-38's gate has no dependency on that work.
Whichever of the two branches merges second will collide on the `0013` number
and need a routine alembic rebase (bump this or that migration to the next
free number) - expected, not a defect; the S1-26b/c plan already calls out
the same class of conflict in its own "Merge ordering" section.

The S1-38 phase 1 agreement-type gate (interim: `pandadoc_template_id`
allowlist, see `pandadoc/agreement_gate.py`) needs a place to record that a
signed document was recognised but deliberately NOT processed - no `clients`
row, no `client.created` emit, no GHL sub-account. `onboarding_events` has no
status column today (only `client_id` / `payload` / `processed_at`), so this
adds one:

- `ignored_reason` TEXT NULL - NULL means the normal path (client created or
  matched). Set means the agreement-type gate rejected the document; the text
  distinguishes "no template id at all" from "an unrecognised template id"
  (see `agreement_gate.agreement_gate_ignore_reason`). `processed_at` is still
  stamped on the ignored path (COALESCE, same as the normal path) so "was this
  event looked at" stays a single column regardless of outcome.
- Partial index on `WHERE ignored_reason IS NOT NULL` - cheap now, and saves a
  follow-up migration whenever an admin/dashboard "ignored events" view gets
  built (no such view exists yet; Slack + this column is the phase-1 audit
  trail).

Additive + nullable, so this is a safe forward-only change with no backfill:
every existing row keeps `ignored_reason IS NULL`, meaning "processed
normally" for anything written before this gate existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_oe_ignored_reason"
down_revision: str | Sequence[str] | None = "0014_identity_key_rename"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_onboarding_events_ignored_reason"


def upgrade() -> None:
    op.add_column("onboarding_events", sa.Column("ignored_reason", sa.Text(), nullable=True))
    op.create_index(
        _INDEX_NAME,
        "onboarding_events",
        ["ignored_reason"],
        postgresql_where=sa.text("ignored_reason IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="onboarding_events")
    op.drop_column("onboarding_events", "ignored_reason")
