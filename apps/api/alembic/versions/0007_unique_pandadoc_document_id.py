"""unique index on clients.pandadoc_document_id (S1-25a)

Revision ID: 0007_unique_pandadoc_document_id
Revises: 0006_create_remaining_tables
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_unique_pandadoc_document_id"
down_revision: str | Sequence[str] | None = "0006_create_remaining_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Each PandaDoc signing is 1:1 with a clients row: the `create_client_record`
# orchestrator (S1-25a) upserts the row keyed on `pandadoc_document_id` so a
# replayed `pandadoc.signed` event never creates a second client. The unique
# constraint enforces that idempotency at the DB layer rather than relying on
# application-level guards. Postgres treats NULL as distinct in a UNIQUE index
# by default, so the many existing rows where pandadoc_document_id IS NULL
# (the column has been NULLable since 0002_create_clients and has never been
# written by application code prior to S1-25a) remain valid.
#
# email stays NON-unique on purpose: a returning client (same person, second
# site / second franchise) signs a new PandaDoc and gets a new clients row
# linked via parent_client_id - both rows share the same email.
#
# Index built CONCURRENTLY so the migration never holds an
# AccessExclusiveLock on the `clients` table - safe to run on a populated
# production database. CONCURRENTLY cannot run inside a transaction block;
# Alembic wraps every `upgrade()` in a transaction by default, so we exit
# the wrapping transaction with `COMMIT`, run the concurrent build, then
# `BEGIN` again to let Alembic close cleanly. Failure mode: if the
# concurrent build is interrupted, Postgres leaves the index in `INVALID`
# state and the next attempt needs `DROP INDEX uq_clients_pandadoc_document_id`
# first (the `DROP INDEX IF EXISTS` in downgrade handles this).


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        "CREATE UNIQUE INDEX CONCURRENTLY uq_clients_pandadoc_document_id "
        "ON clients (pandadoc_document_id)"
    )
    op.execute("BEGIN")


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_clients_pandadoc_document_id")
    op.execute("BEGIN")
