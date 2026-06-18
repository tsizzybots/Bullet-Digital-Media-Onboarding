"""create sales_call_transcripts (S1-27 transcript parking table)

Revision ID: 0010_sales_call_transcripts
Revises: 0009_ix_clients_step_entered_at
Create Date: 2026-06-17

S1-27 captures a Google Meet sales-call transcript that arrives BEFORE the
client exists (the call precedes agreement -> signing -> client.created in
S1-25a). `documents.client_id` is NOT NULL (migration 0006), so an unlinked
transcript has nowhere to live. This table is the "always capture" store: a
transcript is parked here the moment it arrives (matched or not), with a
NULLABLE `client_id` that is backfilled at link time - mirroring how
`onboarding_events.client_id` (migration 0004) is nullable-and-backfilled.

A `documents` row (kind `transcript_text`) is created only ONCE the transcript
is linked to a client, preserving the "every document belongs to a client"
contract the S1-32 detail view relies on. This table holds the link state, the
R2 key, and the match key (participant emails) until then.

Linking happens three ways (all set `client_id` + `linked_at` + `link_method`):
- `email_immediate`: a client already exists for a participant email at capture.
- `email_signing`: `create_client_record_core` claims unlinked transcripts
  matching the freshly-created client's email.
- `manual`: a human assigns one via `POST /transcripts/{id}/link` (the ~10% the
  email match misses; sets `linked_by`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_sales_call_transcripts"
down_revision: str | Sequence[str] | None = "0009_ix_clients_step_entered_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Provider the transcript came from. Google Meet is the only platform Bullet
# uses for sales calls (confirmed 17/06/2026; Zoom dropped). Declared as an enum
# rather than free text so a typo fails at write time, and kept extensible
# (add a value here + in db/enums.py if a second provider is ever introduced).
SALES_CALL_TRANSCRIPT_SOURCE_VALUES: tuple[str, ...] = ("google_meet",)


def upgrade() -> None:
    bind = op.get_bind()

    source_enum = postgresql.ENUM(
        *SALES_CALL_TRANSCRIPT_SOURCE_VALUES,
        name="sales_call_transcript_source",
        create_type=False,
    )
    source_enum.create(bind, checkfirst=False)

    op.create_table(
        "sales_call_transcripts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # NULLABLE: the call precedes the client record, so a freshly-captured
        # transcript usually has no client yet. Backfilled at link time. SET
        # NULL (not CASCADE) on client delete: keep the parked transcript and
        # let it be re-linked rather than silently destroying captured audio.
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", source_enum, nullable=False),
        # The provider's transcript resource id (Google Meet conferenceRecord
        # transcript name). Idempotency key together with `source`: a Pub/Sub
        # redelivery or a worker retry upserts the same row, never a duplicate.
        sa.Column("external_id", sa.Text(), nullable=False),
        # Set after the transcript text is stored in R2. NULL until then so a
        # row that failed mid-capture is visibly incomplete.
        sa.Column("r2_key", sa.Text(), nullable=True),
        # The Google Calendar event the meeting was booked from - the source of
        # the attendee emails used for matching (the invite is "always sent by
        # email"; the Meet participant list does not expose a clean email).
        sa.Column("calendar_event_id", sa.Text(), nullable=True),
        sa.Column("meeting_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meeting_end", sa.DateTime(timezone=True), nullable=True),
        # LOWERCASED emails (calendar attendees + any signed-in participants)
        # used as the auto-match key. Lowercased at write time because jsonb
        # containment (`@>`) is byte-exact and cannot use the citext semantics
        # `clients.email` enjoys; the signing-time backfill lowercases the
        # client email to match. Defaults to an empty array so the match query
        # never sees NULL.
        sa.Column(
            "participant_emails",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("transcript_chars", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        # How the link was made: 'email_immediate' | 'email_signing' | 'manual'.
        # Plain text (audit/diagnostic), not an enum - it never drives a query.
        sa.Column("link_method", sa.Text(), nullable=True),
        sa.Column(
            "linked_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # One row per (source, transcript id). The idempotency guarantee for
        # webhook redelivery + worker retry, mirroring onboarding_events'
        # (event_type, external_id) unique constraint.
        sa.UniqueConstraint(
            "source", "external_id", name="uq_sales_call_transcripts_source_external"
        ),
    )

    op.create_index(
        "ix_sales_call_transcripts_client_id",
        "sales_call_transcripts",
        ["client_id"],
    )
    # The unlinked-list endpoint (manual fallback) and the signing-time backfill
    # both filter on `client_id IS NULL`. A partial index keeps that scan tiny
    # (only the unresolved backlog) regardless of how many transcripts have been
    # linked over time.
    op.create_index(
        "ix_sales_call_transcripts_unlinked",
        "sales_call_transcripts",
        [sa.text("captured_at DESC")],
        postgresql_where=sa.text("client_id IS NULL"),
    )
    # GIN over the lowercased email array so the auto-match containment query
    # (`participant_emails @> to_jsonb(:email)`) is index-backed.
    op.create_index(
        "ix_sales_call_transcripts_participant_emails",
        "sales_call_transcripts",
        ["participant_emails"],
        postgresql_using="gin",
    )

    # Make the "one transcript_text document per (client, transcript)" guarantee
    # STRUCTURAL rather than incidental. The three link paths (immediate / signing
    # / manual) all funnel through `link_transcript_to_client`, which inserts a
    # documents row keyed on the deterministic per-transcript r2_key. Without a
    # constraint, two concurrent linkers under READ COMMITTED could each pass a
    # `WHERE NOT EXISTS` guard and both insert (the S1-25b precedent was protected
    # by an upstream platform_actions UNIQUE that does not exist pre-client here).
    # A PARTIAL unique index (transcript_text only) lets the insert use
    # `ON CONFLICT DO NOTHING` and leaves other document kinds (which may legitimately
    # repeat per client) unconstrained.
    op.create_index(
        "uq_documents_transcript_text",
        "documents",
        ["client_id", "kind", "r2_key"],
        unique=True,
        postgresql_where=sa.text("kind = 'transcript_text'"),
    )


def downgrade() -> None:
    op.drop_index("uq_documents_transcript_text", table_name="documents")
    op.drop_index(
        "ix_sales_call_transcripts_participant_emails",
        table_name="sales_call_transcripts",
    )
    op.drop_index(
        "ix_sales_call_transcripts_unlinked",
        table_name="sales_call_transcripts",
    )
    op.drop_index(
        "ix_sales_call_transcripts_client_id",
        table_name="sales_call_transcripts",
    )
    op.drop_table("sales_call_transcripts")
    bind = op.get_bind()
    postgresql.ENUM(name="sales_call_transcript_source").drop(bind, checkfirst=False)
