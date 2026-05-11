"""create vector and citext extensions

Revision ID: 0001_create_extensions
Revises:
Create Date: 2026-05-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_create_extensions"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS keeps this migration safe on Neon branches where the
    # control plane may have pre-installed an extension.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")


def downgrade() -> None:
    # Reverse order; IF EXISTS keeps the downgrade idempotent.
    op.execute("DROP EXTENSION IF EXISTS citext")
    op.execute("DROP EXTENSION IF EXISTS vector")
