"""create client_knowledge table

Revision ID: 0003_create_client_knowledge
Revises: 0002_create_clients
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

revision: str = "0003_create_client_knowledge"
down_revision: str | Sequence[str] | None = "0002_create_clients"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CLIENT_KNOWLEDGE_SOURCE_VALUES: tuple[str, ...] = (
    "sales_call",
    "agreement",
    "portal",
    "research",
    "kickoff",
    "manual",
)


class _Vector(UserDefinedType):
    """Minimal SQLAlchemy column type for pgvector's `vector(N)`.

    Used by Alembic migrations to emit the correct DDL without pulling
    the `pgvector` Python package into the dependency graph. Application
    reads/writes against the embedding column come later (S1-30 writes
    the AI sales-call summary embeddings; Sprint 4 vector search uses
    them); the pgvector SDK is added at that time.
    """

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **_kw: object) -> str:
        return f"vector({self.dim})"


def upgrade() -> None:
    source = postgresql.ENUM(
        *CLIENT_KNOWLEDGE_SOURCE_VALUES,
        name="client_knowledge_source",
        create_type=False,
    )
    bind = op.get_bind()
    source.create(bind, checkfirst=False)

    op.create_table(
        "client_knowledge",
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
        sa.Column("source", source, nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("embedding", _Vector(1536), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # FK to users(id) is deferred to migration 0006 (S1-10) where
        # the users table is created. Until then this is a plain nullable
        # UUID; the constraint is added at the end of 0006's upgrade().
        sa.Column("captured_by", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.create_index("ix_client_knowledge_client_id", "client_knowledge", ["client_id"])
    op.create_index(
        "ix_client_knowledge_client_id_source",
        "client_knowledge",
        ["client_id", "source"],
    )
    op.create_index(
        "ix_client_knowledge_captured_at_desc",
        "client_knowledge",
        [sa.text("captured_at DESC")],
    )
    # GIN over the jsonb `value` so `value @> '{...}'::jsonb` containment
    # lookups can use an index. `jsonb_path_ops` is more compact and
    # faster than the default GIN opclass when only `@>` is needed.
    op.execute(
        "CREATE INDEX ix_client_knowledge_value_gin "
        "ON client_knowledge USING GIN (value jsonb_path_ops)"
    )
    # ivfflat over the embedding for ANN nearest-neighbour searches. The
    # `vector_cosine_ops` opclass matches the cosine similarity we use
    # for sales-call summaries. lists=100 is a sane default for the row
    # counts we expect through Sprint 4; can be re-tuned later when the
    # table grows.
    op.execute(
        "CREATE INDEX ix_client_knowledge_embedding_ivfflat "
        "ON client_knowledge USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_client_knowledge_embedding_ivfflat")
    op.execute("DROP INDEX IF EXISTS ix_client_knowledge_value_gin")
    op.drop_index("ix_client_knowledge_captured_at_desc", table_name="client_knowledge")
    op.drop_index("ix_client_knowledge_client_id_source", table_name="client_knowledge")
    op.drop_index("ix_client_knowledge_client_id", table_name="client_knowledge")
    op.drop_table("client_knowledge")
    postgresql.ENUM(name="client_knowledge_source").drop(op.get_bind(), checkfirst=False)
