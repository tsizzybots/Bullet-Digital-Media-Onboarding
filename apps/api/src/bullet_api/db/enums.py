"""Application-facing constants for the Postgres enums defined in migrations.

The canonical enum value lists live in their alembic migration files (one
source of truth for the schema). Application code that needs to write an
enum value by name imports the corresponding constant here so a typo
fails at import / lint / test time instead of as a Postgres
`invalid_text_representation` error at runtime.

Adding a new enum value: update the migration's enum tuple AND add the
constant here in the same change.
"""

from __future__ import annotations

# `current_step` enum on `clients`. Source: 0002_create_clients.py
# `CURRENT_STEP_VALUES`. Order matches the lifecycle progression of a
# client through Bullet's onboarding pipeline.
CURRENT_STEP_SALES_CALL = "sales_call"
CURRENT_STEP_AGREEMENT = "agreement"
CURRENT_STEP_SIGNED = "signed"
CURRENT_STEP_PORTAL = "portal"
CURRENT_STEP_KICKOFF = "kickoff"
CURRENT_STEP_BUILD = "build"
CURRENT_STEP_LIVE = "live"

CURRENT_STEP_VALUES: tuple[str, ...] = (
    CURRENT_STEP_SALES_CALL,
    CURRENT_STEP_AGREEMENT,
    CURRENT_STEP_SIGNED,
    CURRENT_STEP_PORTAL,
    CURRENT_STEP_KICKOFF,
    CURRENT_STEP_BUILD,
    CURRENT_STEP_LIVE,
)

# `document_kind` enum on `documents`. Source: 0006_create_remaining_tables.py
# `DOCUMENT_KIND_VALUES`. Written by the fan-outs that persist artefacts:
# S1-25b stores the signed PDF as `pandadoc_signed_pdf`; S1-27/S1-28 store
# `transcript_text`; the research scraper stores `scraped_page`; the kick-off
# email generator stores `kickoff_followup_email_body`.
DOCUMENT_KIND_TRANSCRIPT_TEXT = "transcript_text"
DOCUMENT_KIND_SCRAPED_PAGE = "scraped_page"
DOCUMENT_KIND_KICKOFF_FOLLOWUP_EMAIL_BODY = "kickoff_followup_email_body"
DOCUMENT_KIND_PANDADOC_SIGNED_PDF = "pandadoc_signed_pdf"

DOCUMENT_KIND_VALUES: tuple[str, ...] = (
    DOCUMENT_KIND_TRANSCRIPT_TEXT,
    DOCUMENT_KIND_SCRAPED_PAGE,
    DOCUMENT_KIND_KICKOFF_FOLLOWUP_EMAIL_BODY,
    DOCUMENT_KIND_PANDADOC_SIGNED_PDF,
)

# `source` enum on `sales_call_transcripts`. Source:
# 0010_create_sales_call_transcripts.py `SALES_CALL_TRANSCRIPT_SOURCE_VALUES`.
# Google Meet is Bullet's only sales-call platform (confirmed 17/06/2026;
# Zoom dropped). Extensible: add a value here AND in the migration's tuple if a
# second provider is ever introduced.
SALES_CALL_TRANSCRIPT_SOURCE_GOOGLE_MEET = "google_meet"

SALES_CALL_TRANSCRIPT_SOURCE_VALUES: tuple[str, ...] = (SALES_CALL_TRANSCRIPT_SOURCE_GOOGLE_MEET,)

# `source` enum on `client_knowledge`. Source: 0003_create_client_knowledge.py
# `CLIENT_KNOWLEDGE_SOURCE_VALUES`. Each row's provenance: S1-30 writes the AI
# sales-call summary as `sales_call`; later fan-outs write `agreement` (signed
# doc), `portal` (GHL onboarding survey), `research` (scraper), `kickoff`
# (kick-off call), and `manual` (hand-entered). Add a value here AND in the
# migration's tuple if a new provenance is introduced.
CLIENT_KNOWLEDGE_SOURCE_SALES_CALL = "sales_call"
CLIENT_KNOWLEDGE_SOURCE_AGREEMENT = "agreement"
CLIENT_KNOWLEDGE_SOURCE_PORTAL = "portal"
CLIENT_KNOWLEDGE_SOURCE_RESEARCH = "research"
CLIENT_KNOWLEDGE_SOURCE_KICKOFF = "kickoff"
CLIENT_KNOWLEDGE_SOURCE_MANUAL = "manual"

CLIENT_KNOWLEDGE_SOURCE_VALUES: tuple[str, ...] = (
    CLIENT_KNOWLEDGE_SOURCE_SALES_CALL,
    CLIENT_KNOWLEDGE_SOURCE_AGREEMENT,
    CLIENT_KNOWLEDGE_SOURCE_PORTAL,
    CLIENT_KNOWLEDGE_SOURCE_RESEARCH,
    CLIENT_KNOWLEDGE_SOURCE_KICKOFF,
    CLIENT_KNOWLEDGE_SOURCE_MANUAL,
)


__all__ = [
    "CLIENT_KNOWLEDGE_SOURCE_AGREEMENT",
    "CLIENT_KNOWLEDGE_SOURCE_KICKOFF",
    "CLIENT_KNOWLEDGE_SOURCE_MANUAL",
    "CLIENT_KNOWLEDGE_SOURCE_PORTAL",
    "CLIENT_KNOWLEDGE_SOURCE_RESEARCH",
    "CLIENT_KNOWLEDGE_SOURCE_SALES_CALL",
    "CLIENT_KNOWLEDGE_SOURCE_VALUES",
    "CURRENT_STEP_AGREEMENT",
    "CURRENT_STEP_BUILD",
    "CURRENT_STEP_KICKOFF",
    "CURRENT_STEP_LIVE",
    "CURRENT_STEP_PORTAL",
    "CURRENT_STEP_SALES_CALL",
    "CURRENT_STEP_SIGNED",
    "CURRENT_STEP_VALUES",
    "DOCUMENT_KIND_KICKOFF_FOLLOWUP_EMAIL_BODY",
    "DOCUMENT_KIND_PANDADOC_SIGNED_PDF",
    "DOCUMENT_KIND_SCRAPED_PAGE",
    "DOCUMENT_KIND_TRANSCRIPT_TEXT",
    "DOCUMENT_KIND_VALUES",
    "SALES_CALL_TRANSCRIPT_SOURCE_GOOGLE_MEET",
    "SALES_CALL_TRANSCRIPT_SOURCE_VALUES",
]
