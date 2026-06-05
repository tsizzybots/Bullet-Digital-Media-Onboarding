"""Pure helper: extract `clients`-row fields from a PandaDoc DOCUMENT DETAIL body (S1-25a).

The `pandadoc.signed` webhook payload only carries `data.id` and `data.status` -
it does NOT contain the client values we want. So the S1-25a orchestrator
fetches the FULL document body via `GET /public/v1/documents/{id}/details`
(`bullet_api.pandadoc.client.PandaDocClient.fetch_document_details`) and
passes the response to this helper.

The field names below are pinned against a REAL signed document from Bullet's
PandaDoc UK account (inspected 04/06/2026 via
`apps/api/scripts/inspect_pandadoc_document.py`). Their shape:

- `tokens` - merge variables pre-populated from HubSpot at doc-send time.
  Bullet's template uses the `Client.*` / `Company.*` / `Deal.*` namespaces.
  Token names with the `Bullet.*` prefix identify Bullet's salesperson, NOT
  the client, and are ignored here.

- `fields` - form fields filled in DURING signing (so they only exist on
  completed documents). The legal-trading-name field is the only
  `clients`-relevant field on Bullet's template; it is the only reliable
  source for `legal_entity` because `Company.Name` is the marketing /
  trading name, which can differ from the legal entity.

- `metadata` - arbitrary key/value pairs set by HubSpot's PandaDoc
  integration at doc-creation time. Keys use DOT notation
  (`hubspot.contact_id`, `hubspot.deal_id`, `hubspot.company_id`), not
  underscores or camelCase.

- `recipients` is the signing roster (4 entries: 2 client signers + 2
  Bullet signers on a typical agreement). It is NOT a reliable single
  source for "the client's email": index 0 may be a Bullet signer
  depending on signing order. The `Client.Email` TOKEN is the
  authoritative source, so we read tokens here and ignore recipients.

PURE: no I/O, no DB, no Inngest. Easy to unit-test against synthetic dicts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedClientFields:
    """Fields pulled from a signed PandaDoc document detail, ready to insert
    into `clients` (plus a few we capture for the audit trail / future use).

    `email` is the only hard-required field: without it we cannot identify
    a client at all. `pandadoc_document_id` is required too but is sourced
    from the Inngest event envelope (`event.data.document_id`), not from
    the document body, so it is not validated here.

    `legal_entity` is required NOT NULL in the schema but is read from a
    form field that the client fills in DURING signing; we surface the
    extracted value here and the orchestrator decides on a fallback
    (typically: trading name, then a placeholder sentinel).

    `hubspot_deal_id`, `hubspot_company_id`, `monthly_service_fee`,
    `deal_currency`, `pandadoc_template_id` and `document_creation_source`
    have no column on `clients` yet but are captured on the dataclass for
    audit logging and a follow-up migration.
    """

    email: str
    business_name: str | None
    legal_entity: str | None
    contact_first_name: str | None
    contact_last_name: str | None
    phone: str | None
    address: str | None
    state: str | None
    postal_code: str | None
    country: str | None
    hubspot_contact_id: str | None
    hubspot_deal_id: str | None
    hubspot_company_id: str | None
    monthly_service_fee: str | None
    deal_currency: str | None
    pandadoc_template_id: str | None
    document_creation_source: str | None


class PandaDocPayloadError(ValueError):
    """Raised when the PandaDoc document detail is missing a required field.

    `field` names the missing path so the failure surfaces in
    `onboarding_events`/Inngest logs as a single, actionable string.
    """

    def __init__(self, field: str, message: str | None = None) -> None:
        self.field = field
        super().__init__(message or f"PandaDoc document is missing required field: {field}")


# Token names pinned to Bullet's UK PandaDoc template (verified 04/06/2026).
# These are the EXACT names PandaDoc returns; deviations across templates
# (e.g. the International account, if it has a different template) need a
# separate pass and either an alias-list expansion here or a per-account
# template branch in the orchestrator.
_TOKEN_CLIENT_EMAIL = "Client.Email"
_TOKEN_CLIENT_FIRST_NAME = "Client.FirstName"
_TOKEN_CLIENT_LAST_NAME = "Client.LastName"
_TOKEN_CLIENT_PHONE = "Client.Phone"
_TOKEN_COMPANY_NAME = "Company.Name"
_TOKEN_COMPANY_ADDRESS = "Company.Address"
_TOKEN_COMPANY_STATE = "Company.State"
_TOKEN_COMPANY_ZIP = "Company.Zip"
_TOKEN_COMPANY_COUNTRY = "Company.Country"
_TOKEN_DEAL_CURRENCY = "Deal.Currency"
_TOKEN_DEAL_MONTHLY_SERVICE_FEE = "Deal.MonthlyServiceFee"

# Form field titles are author-defined PandaDoc strings. The legal-entity
# field is the only `clients`-relevant form field on Bullet's UK template.
_FIELD_LEGAL_TRADING_NAME = "Enter Your Company's Legal Trading Name"

# HubSpot integration metadata. Keys use DOT notation in Bullet's account.
_META_HUBSPOT_CONTACT_ID = "hubspot.contact_id"
_META_HUBSPOT_DEAL_ID = "hubspot.deal_id"
_META_HUBSPOT_COMPANY_ID = "hubspot.company_id"
_META_DOCUMENT_CREATION_SOURCE = "document__creation_source"


def _stringy(value: object) -> str | None:
    """Return value as a non-empty trimmed string, or None.

    PandaDoc serialises everything textual; this guards against None / int /
    list slipping through, which would otherwise crash a downstream
    str.strip() or break the `clients` text columns.
    """
    if value is None or not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _find_in_named_list(items: object, name: str) -> str | None:
    """Return the value of the first dict in `items` whose `name` matches."""
    if not isinstance(items, list):
        return None
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == name:
            return _stringy(entry.get("value"))
    return None


def _token(document: dict, name: str) -> str | None:
    return _find_in_named_list(document.get("tokens"), name)


def _field(document: dict, name: str) -> str | None:
    return _find_in_named_list(document.get("fields"), name)


def _metadata_value(document: dict, key: str) -> str | None:
    """Read a metadata value, coercing the types HubSpot actually sends.

    PandaDoc metadata is freeform JSON; HubSpot's PandaDoc integration
    populates it with a mix of types per key:

    - strings (e.g. `hubspot.deal_id`, `hubspot.company_id`)
    - lists of strings (e.g. `hubspot.contact_id` - one entry per
      HubSpot contact linked to the deal; we take the first non-empty)
    - numbers (some integrations push ids as ints)

    For the single-text-column `clients` schema, we collapse all of
    these to one string. Multi-contact deals lose the additional
    contact ids - a follow-up `client_hubspot_contacts` linking table
    is the schema fix once multi-contact matters in practice.
    """
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, bool):
        # bools are a subclass of int in Python; we never want
        # "True" / "False" stored as a hubspot id or creation source.
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        for entry in value:
            stringy = _metadata_value({"metadata": {"_x": entry}}, "_x")
            if stringy is not None:
                return stringy
        return None
    return None


def _template_id(document: dict) -> str | None:
    """Read the template id from the `template` object at the top level.

    Used for UK / International routing telemetry. The exact field name
    PandaDoc uses inside `template` varies across docs - we try `id` first
    then `uuid` as a defensive fallback.
    """
    template = document.get("template")
    if not isinstance(template, dict):
        return None
    return _stringy(template.get("id")) or _stringy(template.get("uuid"))


def extract_client_fields(document: dict) -> ExtractedClientFields:
    """Map a PandaDoc document detail body to client fields.

    Argument is the body returned by
    `PandaDocClient.fetch_document_details(document_id)` (the
    `GET /public/v1/documents/{id}/details` response, NOT the webhook event
    envelope - that one wraps the body in `{"event": ..., "data": {...}}`
    and carries almost no field data).

    Raises `PandaDocPayloadError("tokens[Client.Email]")` when the
    `Client.Email` token is missing or empty. Every other field is optional
    and defaults to None; the orchestrator decides how to fill required-
    NOT-NULL columns (`legal_entity`, `business_name`) from the optional
    inputs.
    """
    if not isinstance(document, dict):
        raise PandaDocPayloadError("document", "PandaDoc document must be a dict")

    email = _token(document, _TOKEN_CLIENT_EMAIL)
    if email is None:
        raise PandaDocPayloadError(
            f"tokens[{_TOKEN_CLIENT_EMAIL}]",
            f"PandaDoc document has no {_TOKEN_CLIENT_EMAIL} token; cannot "
            "identify a client.",
        )

    return ExtractedClientFields(
        email=email,
        business_name=_token(document, _TOKEN_COMPANY_NAME),
        legal_entity=_field(document, _FIELD_LEGAL_TRADING_NAME),
        contact_first_name=_token(document, _TOKEN_CLIENT_FIRST_NAME),
        contact_last_name=_token(document, _TOKEN_CLIENT_LAST_NAME),
        phone=_token(document, _TOKEN_CLIENT_PHONE),
        address=_token(document, _TOKEN_COMPANY_ADDRESS),
        state=_token(document, _TOKEN_COMPANY_STATE),
        postal_code=_token(document, _TOKEN_COMPANY_ZIP),
        country=_token(document, _TOKEN_COMPANY_COUNTRY),
        hubspot_contact_id=_metadata_value(document, _META_HUBSPOT_CONTACT_ID),
        hubspot_deal_id=_metadata_value(document, _META_HUBSPOT_DEAL_ID),
        hubspot_company_id=_metadata_value(document, _META_HUBSPOT_COMPANY_ID),
        monthly_service_fee=_token(document, _TOKEN_DEAL_MONTHLY_SERVICE_FEE),
        deal_currency=_token(document, _TOKEN_DEAL_CURRENCY),
        pandadoc_template_id=_template_id(document),
        document_creation_source=_metadata_value(document, _META_DOCUMENT_CREATION_SOURCE),
    )


# Re-exported for tests / orchestrator that want to assert on the same
# constants. Treat these as the source of truth for "what Bullet's template
# is called"; do NOT hard-code the literal strings elsewhere.
__all__ = [
    "ExtractedClientFields",
    "PandaDocPayloadError",
    "extract_client_fields",
]
