"""create onboarding_events table

Revision ID: 0004_create_onboarding_events
Revises: 0003_create_client_knowledge
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_create_onboarding_events"
down_revision: str | Sequence[str] | None = "0003_create_client_knowledge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "onboarding_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # client_id is NULLABLE: a PandaDoc-signed webhook is the trigger
        # that creates the matching clients row, so the event arrives
        # before the client exists. The orchestrator backfills client_id
        # on the same event row once the client is created.
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # `(event_type, external_id)` is the idempotency key for webhook
        # replays from PandaDoc / GHL / Stripe / etc. PostgreSQL's UNIQUE
        # treats NULL as distinct by default, so rows with a NULL
        # external_id never collide. Spelled as a partial constraint
        # so we never have to special-case the NULL path in code.
        sa.UniqueConstraint(
            "event_type", "external_id", name="uq_onboarding_events_event_external"
        ),
    )

    op.create_index(
        "ix_onboarding_events_client_id", "onboarding_events", ["client_id"]
    )
    op.create_index(
        "ix_onboarding_events_event_type", "onboarding_events", ["event_type"]
    )
    op.create_index(
        "ix_onboarding_events_occurred_at_desc",
        "onboarding_events",
        [sa.text("occurred_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_onboarding_events_occurred_at_desc", table_name="onboarding_events"
    )
    op.drop_index(
        "ix_onboarding_events_event_type", table_name="onboarding_events"
    )
    op.drop_index(
        "ix_onboarding_events_client_id", table_name="onboarding_events"
    )
    op.drop_table("onboarding_events")
