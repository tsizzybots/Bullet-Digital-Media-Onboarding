"""add 'openai' to the platform_action_platform enum (S1-30)

Revision ID: 0012_platform_openai
Revises: 0011_platform_anthropic
Create Date: 2026-07-02

S1-30's `store_sales_knowledge` worker records its attempt in `platform_actions`
with `platform = 'openai'` (the embedding provider - Anthropic has no embeddings
API). That value is not in the `platform_action_platform` enum, so it must be
added.

`ALTER TYPE ... ADD VALUE` runs in an autocommit block: Postgres adds the value
non-transactionally and a freshly-added enum value cannot be USED inside the
same transaction that added it. The autocommit block sidesteps both concerns.
`IF NOT EXISTS` makes the upgrade idempotent.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_platform_openai"
down_revision: str | Sequence[str] | None = "0011_platform_anthropic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE platform_action_platform ADD VALUE IF NOT EXISTS 'openai'")


def downgrade() -> None:
    # PostgreSQL cannot drop a value from an enum type without recreating it.
    # 'openai' is harmless to leave in place, so the downgrade is a no-op.
    pass
