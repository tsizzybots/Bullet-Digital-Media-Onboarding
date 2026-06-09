"""add pandadoc_account to onboarding_events (S1-25c)

Revision ID: 0008_oe_pandadoc_account
Revises: 0007_unique_pandadoc_document_id
Create Date: 2026-06-09

(Revision id kept <=32 chars to fit alembic_version.version_num VARCHAR(32).)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_oe_pandadoc_account"
down_revision: str | Sequence[str] | None = "0007_unique_pandadoc_document_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# S1-25c: Bullet runs two PandaDoc accounts (UK + International). The webhook
# receiver resolves which account a signing came from (by which shared key
# verifies) and stamps it here, and the nightly reconcile cron stamps the
# account it listed the document from. Downstream the async workers read the
# account off the emitted event to pick the matching API key.
#
# Stored as plain TEXT (not a Postgres enum) to match `event_type` and keep the
# account set swappable without a follow-up enum migration; the application
# constrains the values to bullet_api.pandadoc.accounts.PANDADOC_ACCOUNTS.
#
# NOT NULL with server_default 'uk': every existing onboarding_events row was
# created under the single (UK) account, so 'uk' is the correct backfill. The
# default is KEPT so any insert that omits the column lands on the legacy UK
# account rather than failing; the webhook + reconcile inserts pass it
# explicitly. ADD COLUMN ... DEFAULT <const> is a metadata-only operation on
# PostgreSQL 11+ (no table rewrite), so this is safe on a populated table.


def upgrade() -> None:
    op.add_column(
        "onboarding_events",
        sa.Column(
            "pandadoc_account",
            sa.Text(),
            nullable=False,
            server_default="uk",
        ),
    )


def downgrade() -> None:
    op.drop_column("onboarding_events", "pandadoc_account")
