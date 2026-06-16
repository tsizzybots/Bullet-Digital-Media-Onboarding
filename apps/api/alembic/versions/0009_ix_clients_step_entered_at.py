"""index clients(step_entered_at DESC, id) for the dashboard list (S1-31 PR review)

Revision ID: 0009_ix_clients_step_entered_at
Revises: 0008_oe_pandadoc_account
Create Date: 2026-06-12

The S1-31 `GET /clients` list runs `ORDER BY c.step_entered_at DESC, c.id` on
every poll x every open dashboard tab. At ~100 clients today that is a cheap
in-memory sort, but the read frequency only grows; this composite index makes
the sort index-only so it stays cheap as the client count rises. Column order
matches the query (`step_entered_at` DESC, `id` ASC tiebreak).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_ix_clients_step_entered_at"
down_revision: str | Sequence[str] | None = "0008_oe_pandadoc_account"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_clients_step_entered_at_desc_id"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "clients",
        [sa.text("step_entered_at DESC"), sa.text("id")],
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="clients")
