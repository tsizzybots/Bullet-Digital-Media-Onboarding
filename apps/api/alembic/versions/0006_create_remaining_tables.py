"""create remaining tables (documents, research_results, client_assets,
users, sessions, audit_log) and add the deferred client_knowledge FK

Revision ID: 0006_create_remaining_tables
Revises: 0005_create_platform_actions
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_create_remaining_tables"
down_revision: str | Sequence[str] | None = "0005_create_platform_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DOCUMENT_KIND_VALUES: tuple[str, ...] = (
    "drive_folder",
    "gdoc_export",
    "gsheet_row",
    "transcript_audio",
    "transcript_text",
    "scraped_page",
    "kickoff_followup_email_body",
    "pandadoc_signed_pdf",
)

RESEARCH_RESULT_KIND_VALUES: tuple[str, ...] = (
    "website_summary",
    "competitors",
    "audience_size",
    "offer_suggestions",
)

CLIENT_ASSET_TYPE_VALUES: tuple[str, ...] = (
    "ad_account_access",
    "regulatory_docs",
    "headshot",
    "brand_guidelines",
    "face_to_camera_video",
    "logo_files",
    "font_files",
    "images",
    "body_scan_assets",
)

CLIENT_ASSET_STATUS_VALUES: tuple[str, ...] = (
    "required",
    "requested",
    "received",
    "approved",
    "waived",
)

USER_ROLE_VALUES: tuple[str, ...] = (
    "founder",
    "performance_director",
    "account_manager",
    "izzyagents_engineer",
)


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # ENUM types - all declared with create_type=False then created once.
    # ------------------------------------------------------------------
    document_kind = postgresql.ENUM(
        *DOCUMENT_KIND_VALUES, name="document_kind", create_type=False
    )
    research_result_kind = postgresql.ENUM(
        *RESEARCH_RESULT_KIND_VALUES,
        name="research_result_kind",
        create_type=False,
    )
    client_asset_type = postgresql.ENUM(
        *CLIENT_ASSET_TYPE_VALUES,
        name="client_asset_type",
        create_type=False,
    )
    client_asset_status = postgresql.ENUM(
        *CLIENT_ASSET_STATUS_VALUES,
        name="client_asset_status",
        create_type=False,
    )
    user_role = postgresql.ENUM(
        *USER_ROLE_VALUES, name="user_role", create_type=False
    )
    document_kind.create(bind, checkfirst=False)
    research_result_kind.create(bind, checkfirst=False)
    client_asset_type.create(bind, checkfirst=False)
    client_asset_status.create(bind, checkfirst=False)
    user_role.create(bind, checkfirst=False)

    # ------------------------------------------------------------------
    # users - created first because sessions, audit_log, and the deferred
    # client_knowledge.captured_by FK all point at it.
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "email", postgresql.CITEXT(), nullable=False, unique=True
        ),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column(
            "email_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "email_confirmed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "last_login_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "token_hash", sa.Text(), nullable=False, unique=True
        ),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index(
        "ix_sessions_expires_at", "sessions", ["expires_at"]
    )

    # ------------------------------------------------------------------
    # audit_log
    # ------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_audit_log_actor_user_id", "audit_log", ["actor_user_id"]
    )
    op.create_index(
        "ix_audit_log_entity_type_entity_id",
        "audit_log",
        ["entity_type", "entity_id"],
    )
    op.create_index(
        "ix_audit_log_occurred_at_desc",
        "audit_log",
        [sa.text("occurred_at DESC")],
    )

    # ------------------------------------------------------------------
    # documents
    # ------------------------------------------------------------------
    op.create_table(
        "documents",
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
        sa.Column("kind", document_kind, nullable=False),
        sa.Column("external_url", sa.Text(), nullable=True),
        sa.Column("r2_key", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_documents_client_id", "documents", ["client_id"])

    # ------------------------------------------------------------------
    # research_results
    # ------------------------------------------------------------------
    op.create_table(
        "research_results",
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
        sa.Column("kind", research_result_kind, nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_research_results_client_id", "research_results", ["client_id"]
    )

    # ------------------------------------------------------------------
    # client_assets
    # ------------------------------------------------------------------
    op.create_table(
        "client_assets",
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
        sa.Column("asset_type", client_asset_type, nullable=False),
        sa.Column("status", client_asset_status, nullable=False),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "fulfilled_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_client_assets_client_id_status",
        "client_assets",
        ["client_id", "status"],
    )

    # ------------------------------------------------------------------
    # Deferred FK: client_knowledge.captured_by -> users(id). Declared
    # in 0003 as a plain nullable UUID because users did not yet exist.
    # ------------------------------------------------------------------
    op.create_foreign_key(
        "fk_client_knowledge_captured_by_users",
        source_table="client_knowledge",
        referent_table="users",
        local_cols=["captured_by"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_client_knowledge_captured_by_users",
        "client_knowledge",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_client_assets_client_id_status", table_name="client_assets"
    )
    op.drop_table("client_assets")

    op.drop_index(
        "ix_research_results_client_id", table_name="research_results"
    )
    op.drop_table("research_results")

    op.drop_index("ix_documents_client_id", table_name="documents")
    op.drop_table("documents")

    op.drop_index("ix_audit_log_occurred_at_desc", table_name="audit_log")
    op.drop_index(
        "ix_audit_log_entity_type_entity_id", table_name="audit_log"
    )
    op.drop_index("ix_audit_log_actor_user_id", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")

    op.drop_table("users")

    bind = op.get_bind()
    postgresql.ENUM(name="user_role").drop(bind, checkfirst=False)
    postgresql.ENUM(name="client_asset_status").drop(bind, checkfirst=False)
    postgresql.ENUM(name="client_asset_type").drop(bind, checkfirst=False)
    postgresql.ENUM(name="research_result_kind").drop(bind, checkfirst=False)
    postgresql.ENUM(name="document_kind").drop(bind, checkfirst=False)
