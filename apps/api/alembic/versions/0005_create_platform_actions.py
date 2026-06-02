"""create platform_actions table

Revision ID: 0005_create_platform_actions
Revises: 0004_create_onboarding_events
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_create_platform_actions"
down_revision: str | Sequence[str] | None = "0004_create_onboarding_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PLATFORM_VALUES: tuple[str, ...] = (
    "hubspot",
    "pandadoc",
    "ghl",
    "asana",
    "stripe",
    "xero",
    "timely",
    "slack",
    "gsheets",
    "gdocs",
    "gdrive",
    "gmail",
    "gcal",
    "meta",
    "resend",
    "firecrawl",
)

STATUS_VALUES: tuple[str, ...] = (
    "pending",
    "in_progress",
    "success",
    "failed",
    "dead_lettered",
)


def upgrade() -> None:
    platform = postgresql.ENUM(
        *PLATFORM_VALUES,
        name="platform_action_platform",
        create_type=False,
    )
    status = postgresql.ENUM(
        *STATUS_VALUES,
        name="platform_action_status",
        create_type=False,
    )
    bind = op.get_bind()
    platform.create(bind, checkfirst=False)
    status.create(bind, checkfirst=False)

    op.create_table(
        "platform_actions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # event_id is nullable per PRD: synthetic / reconciliation actions
        # can be triggered without a matching onboarding_events row.
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("onboarding_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("platform", platform, nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        # idempotency_key is the workhorse: `{client_id}:{platform}:{action}:{event_id}`.
        # UNIQUE constraint guarantees a retried Inngest run cannot fan out
        # twice; the writer code does INSERT ... ON CONFLICT DO NOTHING and
        # then reads back the existing row to resume.
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("status", status, nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("inngest_run_id", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_platform_actions_client_id", "platform_actions", ["client_id"])
    op.create_index(
        "ix_platform_actions_client_platform_action",
        "platform_actions",
        ["client_id", "platform", "action"],
    )
    op.create_index("ix_platform_actions_status", "platform_actions", ["status"])
    op.create_index(
        "ix_platform_actions_started_at_desc",
        "platform_actions",
        [sa.text("started_at DESC")],
    )
    # idempotency_key UNIQUE constraint auto-creates a backing index, so no
    # explicit `op.create_index` is needed for it.


def downgrade() -> None:
    op.drop_index("ix_platform_actions_started_at_desc", table_name="platform_actions")
    op.drop_index("ix_platform_actions_status", table_name="platform_actions")
    op.drop_index(
        "ix_platform_actions_client_platform_action",
        table_name="platform_actions",
    )
    op.drop_index("ix_platform_actions_client_id", table_name="platform_actions")
    op.drop_table("platform_actions")
    bind = op.get_bind()
    postgresql.ENUM(name="platform_action_status").drop(bind, checkfirst=False)
    postgresql.ENUM(name="platform_action_platform").drop(bind, checkfirst=False)
