"""add 'anthropic' to the platform_action_platform enum (S1-29)

Revision ID: 0011_platform_anthropic
Revises: 0010_sales_call_transcripts
Create Date: 2026-06-17

S1-29's `summarise_sales_call` worker records its attempt in `platform_actions`
with `platform = 'anthropic'` (the Claude summariser). That value is not in the
`platform_action_platform` enum created by 0005, so it must be added.

`ALTER TYPE ... ADD VALUE` runs in an autocommit block: Postgres adds the value
non-transactionally and a freshly-added enum value cannot be USED inside the
same transaction that added it. The autocommit block sidesteps both concerns.
`IF NOT EXISTS` makes the upgrade idempotent.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_platform_anthropic"
down_revision: str | Sequence[str] | None = "0010_sales_call_transcripts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE platform_action_platform ADD VALUE IF NOT EXISTS 'anthropic'")


def downgrade() -> None:
    # PostgreSQL cannot drop a value from an enum type without recreating it.
    # 'anthropic' is harmless to leave in place, so the downgrade is a no-op.
    pass
