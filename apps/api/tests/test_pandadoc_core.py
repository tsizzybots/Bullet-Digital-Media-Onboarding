"""Unit tests for `bullet_api.webhooks.pandadoc_core`.

These tests exercise the pure PandaDoc helpers (HMAC signature verification
and completed-document extraction) and never touch a live database, FastAPI,
or Inngest. They run with plain pytest and no `db` marker.
"""

from __future__ import annotations

import hashlib
import hmac

from bullet_api.webhooks.pandadoc_core import (
    PANDADOC_COMPLETED_STATUS,
    SignedDocument,
    extract_signed_documents,
    resolve_pandadoc_account,
    verify_pandadoc_signature,
)


def _sign(body: bytes, secret: str) -> str:
    """Compute the lowercase hex HMAC-SHA256 PandaDoc would send."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------- #
# verify_pandadoc_signature
# --------------------------------------------------------------------------- #


def test_correct_signature_returns_true() -> None:
    body = b'[{"event": "document_state_changed"}]'
    secret = "shared-key"
    assert verify_pandadoc_signature(body, _sign(body, secret), secret) is True


def test_tampered_body_with_old_signature_returns_false() -> None:
    secret = "shared-key"
    original = b'{"amount": "100"}'
    signature = _sign(original, secret)
    tampered = b'{"amount": "999"}'
    assert verify_pandadoc_signature(tampered, signature, secret) is False


def test_wrong_secret_returns_false() -> None:
    body = b'[{"event": "document_state_changed"}]'
    signature = _sign(body, "right-key")
    assert verify_pandadoc_signature(body, signature, "wrong-key") is False


def test_signature_none_returns_false() -> None:
    body = b"anything"
    assert verify_pandadoc_signature(body, None, "shared-key") is False


def test_signature_empty_returns_false() -> None:
    body = b"anything"
    assert verify_pandadoc_signature(body, "", "shared-key") is False


def test_empty_secret_fails_closed_even_with_matching_digest() -> None:
    # An attacker could compute the HMAC with an empty key; we must still
    # reject because the configured secret is empty (fail closed).
    body = b"anything"
    matching_empty_key_digest = _sign(body, "")
    assert verify_pandadoc_signature(body, matching_empty_key_digest, "") is False


def test_typoed_signature_returns_false() -> None:
    body = b'[{"event": "document_state_changed"}]'
    secret = "shared-key"
    good = _sign(body, secret)
    # Flip the last hex character to produce a same-length, wrong digest.
    typoed = good[:-1] + ("0" if good[-1] != "0" else "1")
    assert verify_pandadoc_signature(body, typoed, secret) is False


def test_case_changed_signature_returns_false() -> None:
    # PandaDoc sends a lowercase hex digest; an upper-cased copy must fail.
    body = b'[{"event": "document_state_changed"}]'
    secret = "shared-key"
    good = _sign(body, secret)
    assert verify_pandadoc_signature(body, good.upper(), secret) is False


# --------------------------------------------------------------------------- #
# extract_signed_documents
# --------------------------------------------------------------------------- #


def test_single_completed_event_in_array() -> None:
    event = {
        "event": "document_state_changed",
        "data": {"id": "doc-123", "name": "Contract", "status": PANDADOC_COMPLETED_STATUS},
    }
    result = extract_signed_documents([event])
    assert result == [SignedDocument(document_id="doc-123", event=event)]
    assert result[0].event is event


def test_single_dict_not_wrapped_in_list() -> None:
    event = {"data": {"id": "doc-abc", "status": PANDADOC_COMPLETED_STATUS}}
    result = extract_signed_documents(event)
    assert len(result) == 1
    assert result[0].document_id == "doc-abc"
    assert result[0].event is event


def test_non_completed_statuses_return_empty() -> None:
    for status in ("document.sent", "document.viewed", "document.draft"):
        payload = [{"data": {"id": "doc-1", "status": status}}]
        assert extract_signed_documents(payload) == []


def test_mixed_array_returns_only_completed() -> None:
    completed = {"data": {"id": "doc-done", "status": PANDADOC_COMPLETED_STATUS}}
    payload = [
        {"data": {"id": "doc-sent", "status": "document.sent"}},
        completed,
        {"data": {"id": "doc-viewed", "status": "document.viewed"}},
    ]
    result = extract_signed_documents(payload)
    assert len(result) == 1
    assert result[0].document_id == "doc-done"
    assert result[0].event is completed


def test_duplicate_document_id_is_deduplicated_first_wins() -> None:
    first = {"event": "a", "data": {"id": "doc-dup", "status": PANDADOC_COMPLETED_STATUS}}
    second = {"event": "b", "data": {"id": "doc-dup", "status": PANDADOC_COMPLETED_STATUS}}
    result = extract_signed_documents([first, second])
    assert len(result) == 1
    assert result[0].event is first


def test_order_is_preserved_across_multiple_documents() -> None:
    payload = [
        {"data": {"id": "doc-1", "status": PANDADOC_COMPLETED_STATUS}},
        {"data": {"id": "doc-2", "status": PANDADOC_COMPLETED_STATUS}},
        {"data": {"id": "doc-3", "status": PANDADOC_COMPLETED_STATUS}},
    ]
    ids = [d.document_id for d in extract_signed_documents(payload)]
    assert ids == ["doc-1", "doc-2", "doc-3"]


def test_malformed_inputs_never_raise_and_return_empty() -> None:
    malformed: list[object] = [
        None,
        [],
        {},
        [{"data": {}}],
        [{"data": {"status": PANDADOC_COMPLETED_STATUS}}],  # completed but no id
        "not json-ish",
        [123, None],
        42,
        [{"data": "not-a-dict"}],
        [{"data": {"id": "", "status": PANDADOC_COMPLETED_STATUS}}],  # empty id
        [{"data": {"id": 123, "status": PANDADOC_COMPLETED_STATUS}}],  # non-str id
        [{"no_data_key": True}],
    ]
    for payload in malformed:
        assert extract_signed_documents(payload) == []


# --------------------------------------------------------------------------- #
# resolve_pandadoc_account (S1-25c try-both-secrets routing)
# --------------------------------------------------------------------------- #

_ACCOUNT_SECRETS = {"uk": "uk-shared-key", "int": "int-shared-key"}


def test_resolve_account_returns_account_whose_key_verifies() -> None:
    body = b'[{"event": "document_state_changed"}]'
    uk_sig = _sign(body, _ACCOUNT_SECRETS["uk"])
    int_sig = _sign(body, _ACCOUNT_SECRETS["int"])
    assert resolve_pandadoc_account(body, uk_sig, _ACCOUNT_SECRETS) == "uk"
    assert resolve_pandadoc_account(body, int_sig, _ACCOUNT_SECRETS) == "int"


def test_resolve_account_none_when_no_key_matches() -> None:
    body = b'[{"event": "x"}]'
    foreign_sig = _sign(body, "some-other-accounts-key")
    assert resolve_pandadoc_account(body, foreign_sig, _ACCOUNT_SECRETS) is None


def test_resolve_account_fails_closed_on_missing_signature_or_empty_secrets() -> None:
    body = b'[{"event": "x"}]'
    # No signature -> None regardless of configured secrets.
    assert resolve_pandadoc_account(body, None, _ACCOUNT_SECRETS) is None
    assert resolve_pandadoc_account(body, "", _ACCOUNT_SECRETS) is None
    # No accounts configured -> every request rejected (None), even a sig that
    # would match some key.
    assert resolve_pandadoc_account(body, _sign(body, "uk-shared-key"), {}) is None


def test_resolve_account_ignores_an_empty_secret_value() -> None:
    # An account present in the map but with an empty secret must never match
    # (verify_pandadoc_signature fails closed on an empty secret).
    body = b'[{"event": "x"}]'
    secrets = {"uk": "", "int": "int-shared-key"}
    assert resolve_pandadoc_account(body, _sign(body, "int-shared-key"), secrets) == "int"
    # A signature computed with an empty key does not sneak in as "uk".
    assert resolve_pandadoc_account(body, _sign(body, ""), secrets) is None
