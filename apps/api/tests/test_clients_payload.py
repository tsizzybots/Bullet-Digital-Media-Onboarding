"""Unit tests for `bullet_api.worker.clients_payload` (S1-25a).

These tests exercise the pure extraction helper and never touch a database,
FastAPI app, or Inngest. They run with plain pytest and no `db` marker.

The fixtures here mirror the REAL shape of a Bullet PandaDoc document detail
captured 04/06/2026 via `apps/api/scripts/inspect_pandadoc_document.py`:

- Tokens use the `Client.*`, `Company.*`, `Deal.*` namespaces.
- Legal entity is a FORM FIELD titled `"Enter Your Company's Legal Trading Name"`
  (filled DURING signing, not pre-populated).
- HubSpot metadata uses DOT-notation keys (`hubspot.contact_id`,
  `hubspot.deal_id`, `hubspot.company_id`).
- The input to `extract_client_fields` is the document body itself, NOT a
  webhook envelope. The webhook envelope (`{"event": ..., "data": {...}}`)
  is irrelevant to this helper - the orchestrator fetches the body via the
  PandaDoc API and hands it in directly.
"""

from __future__ import annotations

import pytest

from bullet_api.worker.clients_payload import (
    ExtractedClientFields,
    PandaDocPayloadError,
    extract_client_fields,
)


def _document(**overrides: object) -> dict:
    """Minimal completed-document detail body with the `Client.Email` token set.

    Callers override fields by passing them as kwargs which are MERGED INTO
    the top-level document body. To override `tokens` / `fields` / `metadata`
    pass the whole list/dict explicitly.
    """
    body: dict = {
        "id": "Dxa9Jiv4hyu59Gypan2Mv6",
        "name": "Bullet Digital Media - Digital Marketing Partnership Agreement",
        "status": "document.completed",
        "tokens": [
            {"name": "Client.Email", "value": "signer@example.com"},
            {"name": "Client.FirstName", "value": "Sample"},
            {"name": "Client.LastName", "value": "Signer"},
            {"name": "Client.Phone", "value": "+447000000000"},
            {"name": "Company.Name", "value": "Sample Gym Ltd"},
            {"name": "Company.Address", "value": "123 High Street"},
            {"name": "Company.State", "value": "Greater Manchester"},
            {"name": "Company.Zip", "value": "M1 1AA"},
            {"name": "Company.Country", "value": "United Kingdom"},
            {"name": "Deal.Currency", "value": "GBP"},
            {"name": "Deal.MonthlyServiceFee", "value": "2500"},
        ],
        "fields": [
            {"name": "Enter Your Company's Legal Trading Name", "value": "Sample Gym Ltd"},
        ],
        "metadata": {
            "hubspot.contact_id": "hs-contact-1234",
            "hubspot.deal_id": "hs-deal-5678",
            "hubspot.company_id": "hs-company-9012",
            "document__creation_source": "hubspot",
        },
        "template": {"id": "tpl-uk-v3"},
        "recipients": [
            {"email": "first@example.com", "first_name": "Anyone", "last_name": "Else"},
        ],
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# Required-field validation
# --------------------------------------------------------------------------- #


def test_client_email_token_present_returns_dataclass() -> None:
    out = extract_client_fields(_document())
    assert isinstance(out, ExtractedClientFields)
    assert out.email == "signer@example.com"


def test_missing_client_email_token_raises_with_token_field_name() -> None:
    document = _document(tokens=[{"name": "Company.Name", "value": "Sample Gym Ltd"}])
    with pytest.raises(PandaDocPayloadError) as exc:
        extract_client_fields(document)
    assert exc.value.field == "tokens[Client.Email]"


def test_empty_string_email_raises() -> None:
    document = _document(tokens=[{"name": "Client.Email", "value": "   "}])
    with pytest.raises(PandaDocPayloadError):
        extract_client_fields(document)


def test_non_dict_document_raises_with_document_field_name() -> None:
    with pytest.raises(PandaDocPayloadError) as exc:
        extract_client_fields("not a dict")  # type: ignore[arg-type]
    assert exc.value.field == "document"


def test_empty_document_raises_for_client_email_token() -> None:
    with pytest.raises(PandaDocPayloadError) as exc:
        extract_client_fields({})
    assert exc.value.field == "tokens[Client.Email]"


def test_tokens_not_a_list_raises_for_client_email() -> None:
    document = _document(tokens="not-a-list")
    with pytest.raises(PandaDocPayloadError) as exc:
        extract_client_fields(document)
    assert exc.value.field == "tokens[Client.Email]"


# --------------------------------------------------------------------------- #
# Client identity tokens
# --------------------------------------------------------------------------- #


def test_client_identity_tokens_extracted() -> None:
    out = extract_client_fields(_document())
    assert out.email == "signer@example.com"
    assert out.contact_first_name == "Sample"
    assert out.contact_last_name == "Signer"
    assert out.phone == "+447000000000"


def test_recipient_email_is_NOT_used_as_client_email() -> None:
    """Recipients[] is the signing roster (includes Bullet signers). The
    `Client.Email` TOKEN is the authoritative source. We deliberately
    ignore recipients[0] even if its email differs from the token's."""
    document = _document(
        tokens=[{"name": "Client.Email", "value": "from-token@example.com"}],
        recipients=[{"email": "from-recipient@example.com", "first_name": "Other"}],
    )
    out = extract_client_fields(document)
    assert out.email == "from-token@example.com"


# --------------------------------------------------------------------------- #
# Company tokens
# --------------------------------------------------------------------------- #


def test_company_tokens_extracted() -> None:
    out = extract_client_fields(_document())
    assert out.business_name == "Sample Gym Ltd"
    assert out.address == "123 High Street"
    assert out.state == "Greater Manchester"
    assert out.postal_code == "M1 1AA"
    assert out.country == "United Kingdom"


def test_missing_company_tokens_default_to_none() -> None:
    document = _document(tokens=[{"name": "Client.Email", "value": "signer@example.com"}])
    out = extract_client_fields(document)
    assert out.business_name is None
    assert out.address is None
    assert out.state is None
    assert out.postal_code is None
    assert out.country is None


# --------------------------------------------------------------------------- #
# Legal entity FORM FIELD (not a token)
# --------------------------------------------------------------------------- #


def test_legal_entity_from_form_field() -> None:
    out = extract_client_fields(_document())
    assert out.legal_entity == "Sample Gym Ltd"


def test_missing_legal_entity_form_field_returns_none() -> None:
    document = _document(fields=[])
    out = extract_client_fields(document)
    assert out.legal_entity is None


def test_legal_entity_is_NOT_read_from_company_name_token() -> None:
    """`Company.Name` is the trading name; `legal_entity` must come from
    the dedicated form field. We assert these are read independently."""
    document = _document(
        tokens=[
            {"name": "Client.Email", "value": "signer@example.com"},
            {"name": "Company.Name", "value": "Trading Name"},
        ],
        fields=[
            {"name": "Enter Your Company's Legal Trading Name", "value": "Legal Name Ltd"},
        ],
    )
    out = extract_client_fields(document)
    assert out.business_name == "Trading Name"
    assert out.legal_entity == "Legal Name Ltd"


# --------------------------------------------------------------------------- #
# HubSpot metadata (dot-notation keys)
# --------------------------------------------------------------------------- #


def test_hubspot_ids_from_dot_notation_metadata() -> None:
    out = extract_client_fields(_document())
    assert out.hubspot_contact_id == "hs-contact-1234"
    assert out.hubspot_deal_id == "hs-deal-5678"
    assert out.hubspot_company_id == "hs-company-9012"
    assert out.document_creation_source == "hubspot"


def test_missing_metadata_returns_none() -> None:
    document = _document(metadata={})
    out = extract_client_fields(document)
    assert out.hubspot_contact_id is None
    assert out.hubspot_deal_id is None
    assert out.hubspot_company_id is None
    assert out.document_creation_source is None


def test_non_dict_metadata_does_not_raise() -> None:
    document = _document(metadata="not-a-dict")
    out = extract_client_fields(document)
    assert out.hubspot_contact_id is None


def test_metadata_value_coerces_int_to_string() -> None:
    """HubSpot's PandaDoc integration sometimes pushes ids as integers
    instead of strings. The metadata reader must coerce."""
    document = _document(metadata={"hubspot.contact_id": 1234})
    out = extract_client_fields(document)
    assert out.hubspot_contact_id == "1234"


def test_metadata_value_unwraps_list_taking_first_non_empty_entry() -> None:
    """`hubspot.contact_id` is delivered as a LIST of contact ids by
    HubSpot's PandaDoc integration (one entry per HubSpot contact
    linked to the deal). The single `clients.hubspot_contact_id` text
    column takes the first non-empty entry; a follow-up linking table
    handles multi-contact properly."""
    document = _document(metadata={"hubspot.contact_id": ["contact-id-1", "contact-id-2"]})
    out = extract_client_fields(document)
    assert out.hubspot_contact_id == "contact-id-1"


def test_metadata_value_list_skips_none_entries() -> None:
    document = _document(metadata={"hubspot.contact_id": [None, "", "contact-id"]})
    out = extract_client_fields(document)
    assert out.hubspot_contact_id == "contact-id"


def test_metadata_value_empty_list_returns_none() -> None:
    document = _document(metadata={"hubspot.contact_id": []})
    out = extract_client_fields(document)
    assert out.hubspot_contact_id is None


def test_metadata_value_bool_is_NOT_coerced() -> None:
    """bools are a subclass of int in Python; the metadata reader must
    NOT silently coerce True/False to the string 'True'/'False' (the
    keys we extract are never boolean)."""
    document = _document(metadata={"hubspot.contact_id": True})
    out = extract_client_fields(document)
    assert out.hubspot_contact_id is None


# --------------------------------------------------------------------------- #
# Deal tokens
# --------------------------------------------------------------------------- #


def test_deal_currency_and_monthly_fee_extracted() -> None:
    out = extract_client_fields(_document())
    assert out.deal_currency == "GBP"
    assert out.monthly_service_fee == "2500"


def test_missing_deal_tokens_default_to_none() -> None:
    document = _document(tokens=[{"name": "Client.Email", "value": "signer@example.com"}])
    out = extract_client_fields(document)
    assert out.deal_currency is None
    assert out.monthly_service_fee is None


# --------------------------------------------------------------------------- #
# Template id (UK / International routing telemetry)
# --------------------------------------------------------------------------- #


def test_template_id_from_template_id_field() -> None:
    out = extract_client_fields(_document())
    assert out.pandadoc_template_id == "tpl-uk-v3"


def test_template_uuid_fallback() -> None:
    document = _document(template={"uuid": "uuid-fallback"})
    out = extract_client_fields(document)
    assert out.pandadoc_template_id == "uuid-fallback"


def test_missing_template_object_returns_none() -> None:
    document = _document(template=None)
    out = extract_client_fields(document)
    assert out.pandadoc_template_id is None


# --------------------------------------------------------------------------- #
# Defensive parsing - malformed input must not raise on optional fields
# --------------------------------------------------------------------------- #


def test_token_with_non_string_value_falls_through() -> None:
    """A token whose name matches but whose value is a number / None must
    NOT crash extraction or be returned as a non-string."""
    document = _document(
        tokens=[
            {"name": "Client.Email", "value": "signer@example.com"},
            {"name": "Company.Name", "value": 12345},
        ]
    )
    out = extract_client_fields(document)
    assert out.business_name is None


def test_whitespace_only_token_value_collapses_to_none() -> None:
    document = _document(
        tokens=[
            {"name": "Client.Email", "value": "signer@example.com"},
            {"name": "Company.Name", "value": "   "},
        ]
    )
    out = extract_client_fields(document)
    assert out.business_name is None


def test_first_matching_token_wins() -> None:
    """If the same token name appears twice (PandaDoc has been known to do
    this when a template merges duplicate placeholders), we take the FIRST
    occurrence. Pins down behaviour rather than guessing."""
    document = _document(
        tokens=[
            {"name": "Client.Email", "value": "first@example.com"},
            {"name": "Client.Email", "value": "second@example.com"},
        ]
    )
    out = extract_client_fields(document)
    assert out.email == "first@example.com"


# --------------------------------------------------------------------------- #
# HubSpot linked_objects (real signed-document shape, S1-26b)
# --------------------------------------------------------------------------- #


def _linked(provider: str, entity_type: str, entity_id: str) -> dict:
    return {"provider": provider, "entity_type": entity_type, "entity_id": entity_id}


def test_hubspot_deal_id_from_linked_objects() -> None:
    """A REAL completed document carries the HubSpot deal in `linked_objects`
    (only the deal - no company, no contact) and near-empty metadata."""
    document = _document(
        metadata={"document__creation_source": "template"},
        linked_objects=[_linked("hubspot", "deal", "11111111111")],
    )
    out = extract_client_fields(document)
    assert out.hubspot_deal_id == "11111111111"
    assert out.hubspot_company_id is None
    assert out.hubspot_contact_id is None
    assert out.document_creation_source == "template"


def test_linked_objects_take_precedence_over_metadata() -> None:
    """When both are present, `linked_objects` (the authoritative source) wins."""
    document = _document(
        metadata={"hubspot.deal_id": "stale-metadata-deal"},
        linked_objects=[_linked("hubspot", "deal", "linked-deal")],
    )
    out = extract_client_fields(document)
    assert out.hubspot_deal_id == "linked-deal"


def test_linked_objects_falls_back_to_metadata_when_absent() -> None:
    """No `linked_objects` -> the dot-notation metadata fallback still populates
    (older / other-template documents)."""
    document = _document(metadata={"hubspot.deal_id": "meta-deal"})
    document.pop("linked_objects", None)
    out = extract_client_fields(document)
    assert out.hubspot_deal_id == "meta-deal"


def test_linked_objects_ignores_non_hubspot_provider() -> None:
    document = _document(
        metadata={},
        linked_objects=[_linked("salesforce", "deal", "sf-deal")],
    )
    out = extract_client_fields(document)
    assert out.hubspot_deal_id is None


def test_linked_objects_first_match_wins_with_many() -> None:
    document = _document(
        metadata={},
        linked_objects=[
            _linked("hubspot", "deal", "deal-1"),
            _linked("hubspot", "deal", "deal-2"),
        ],
    )
    out = extract_client_fields(document)
    assert out.hubspot_deal_id == "deal-1"


def test_linked_objects_matches_case_insensitively() -> None:
    document = _document(
        metadata={},
        linked_objects=[_linked("HubSpot", "Deal", "mixed-case-deal")],
    )
    out = extract_client_fields(document)
    assert out.hubspot_deal_id == "mixed-case-deal"


def test_linked_objects_reads_company_and_contact_when_present() -> None:
    """Defensive: real Bullet docs link only the deal, but if a company/contact
    is ever linked we read it (precedence over metadata)."""
    document = _document(
        metadata={},
        linked_objects=[
            _linked("hubspot", "deal", "d1"),
            _linked("hubspot", "company", "c1"),
            _linked("hubspot", "contact", "p1"),
        ],
    )
    out = extract_client_fields(document)
    assert out.hubspot_deal_id == "d1"
    assert out.hubspot_company_id == "c1"
    assert out.hubspot_contact_id == "p1"


def test_non_list_linked_objects_falls_back_to_metadata() -> None:
    document = _document(
        metadata={"hubspot.deal_id": "meta-deal"},
        linked_objects="not-a-list",
    )
    out = extract_client_fields(document)
    assert out.hubspot_deal_id == "meta-deal"


def test_linked_objects_empty_list_yields_none_deal() -> None:
    document = _document(metadata={}, linked_objects=[])
    out = extract_client_fields(document)
    assert out.hubspot_deal_id is None
