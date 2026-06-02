"""create clients table

Revision ID: 0002_create_clients
Revises: 0001_create_extensions
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_create_clients"
down_revision: str | Sequence[str] | None = "0001_create_extensions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CAMPAIGN_FLOW_TYPE_VALUES: tuple[str, ...] = (
    "low_ticket_checkout",
    "high_ticket_consultation",
)

CURRENT_STEP_VALUES: tuple[str, ...] = (
    "sales_call",
    "agreement",
    "signed",
    "portal",
    "kickoff",
    "build",
    "live",
)


def upgrade() -> None:
    # The same ENUM instance is reused for both the explicit `.create()`
    # call and the column type below. SQLAlchemy tracks which ENUMs have
    # already been emitted on a given bind; reusing the object prevents
    # `create_table()` from issuing a duplicate CREATE TYPE statement.
    campaign_flow_type = postgresql.ENUM(
        *CAMPAIGN_FLOW_TYPE_VALUES,
        name="campaign_flow_type",
        create_type=False,
    )
    current_step = postgresql.ENUM(
        *CURRENT_STEP_VALUES,
        name="current_step",
        create_type=False,
    )
    bind = op.get_bind()
    campaign_flow_type.create(bind, checkfirst=False)
    current_step.create(bind, checkfirst=False)

    op.create_table(
        "clients",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # email is citext so case-insensitive lookups Just Work for "X@y.com"
        # vs "x@Y.COM". Not UNIQUE: returning-client second-site rows share the
        # same email, linked via parent_client_id.
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("business_name", sa.Text(), nullable=True),
        sa.Column("legal_entity", sa.Text(), nullable=False),
        sa.Column("contact_first_name", sa.Text(), nullable=True),
        sa.Column("contact_last_name", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("service_tier", sa.Text(), nullable=True),
        sa.Column("monthly_fee_usd", sa.Numeric(12, 2), nullable=True),
        sa.Column("campaign_flow_type", campaign_flow_type, nullable=True),
        sa.Column("current_step", current_step, nullable=False),
        sa.Column(
            "step_entered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "parent_client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Platform-ID columns. Each NULL until the corresponding fan-out step
        # writes the resource id back. Keeping them as plain TEXT (rather than
        # per-platform enums or constraints) preserves the option to swap a
        # platform out later without a schema migration.
        sa.Column("hubspot_contact_id", sa.Text(), nullable=True),
        sa.Column("pandadoc_document_id", sa.Text(), nullable=True),
        sa.Column("ghl_contact_id", sa.Text(), nullable=True),
        sa.Column("ghl_subaccount_id", sa.Text(), nullable=True),
        sa.Column("asana_project_id", sa.Text(), nullable=True),
        sa.Column("asana_finance_task_id", sa.Text(), nullable=True),
        sa.Column("stripe_customer_id", sa.Text(), nullable=True),
        sa.Column("stripe_subscription_id", sa.Text(), nullable=True),
        sa.Column("xero_contact_id", sa.Text(), nullable=True),
        sa.Column("timely_client_id", sa.Text(), nullable=True),
        sa.Column("timely_project_id", sa.Text(), nullable=True),
        sa.Column("meta_ad_account_id", sa.Text(), nullable=True),
        sa.Column("drive_folder_id", sa.Text(), nullable=True),
        sa.Column("sheet_row_id", sa.Text(), nullable=True),
        sa.Column("slack_thread_ts", sa.Text(), nullable=True),
    )

    op.create_index("ix_clients_email", "clients", ["email"])
    op.create_index("ix_clients_current_step", "clients", ["current_step"])
    op.create_index(
        "ix_clients_created_at_desc",
        "clients",
        [sa.text("created_at DESC")],
    )
    op.create_index("ix_clients_parent_client_id", "clients", ["parent_client_id"])


def downgrade() -> None:
    op.drop_index("ix_clients_parent_client_id", table_name="clients")
    op.drop_index("ix_clients_created_at_desc", table_name="clients")
    op.drop_index("ix_clients_current_step", table_name="clients")
    op.drop_index("ix_clients_email", table_name="clients")
    op.drop_table("clients")
    bind = op.get_bind()
    postgresql.ENUM(name="current_step").drop(bind, checkfirst=False)
    postgresql.ENUM(name="campaign_flow_type").drop(bind, checkfirst=False)
