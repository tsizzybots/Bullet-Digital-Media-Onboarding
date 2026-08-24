"""Unit tests for the S1-38 phase 1 agreement-type gate (pure module).

No I/O, no DB - `pandadoc.agreement_gate` is a pure allowlist lookup, tested
directly against strings/None.
"""

from __future__ import annotations

from bullet_api.pandadoc.agreement_gate import (
    GYM_TEMPLATE_IDS,
    agreement_gate_ignore_reason,
    is_gym_agreement,
)

_UK_GYM_ID = "Kn7vBp56MLSxreXPXwpNWk"
_INT_FITNESS_ID = "rQ9jQ6f4dcP2jjmCfF3H6Y"
_INT_CONSUMER_ID = "jqJJFqN5sFnwZypr3owPRV"


def test_uk_gym_template_is_allowlisted() -> None:
    assert is_gym_agreement("uk", _UK_GYM_ID) is True


def test_both_int_gym_templates_are_allowlisted() -> None:
    assert is_gym_agreement("int", _INT_FITNESS_ID) is True
    assert is_gym_agreement("int", _INT_CONSUMER_ID) is True


def test_uk_id_under_int_account_is_rejected() -> None:
    """UK and INT template ids are disjoint sets - reusing a UK id under the
    INT account must not accidentally match (and vice versa)."""
    assert is_gym_agreement("int", _UK_GYM_ID) is False
    assert is_gym_agreement("uk", _INT_FITNESS_ID) is False


def test_ads_management_only_is_not_yet_allowlisted() -> None:
    """Owner decision (20/08): the second UK gym-shaped template is
    deliberately excluded pending confirmation it is in scope."""
    assert is_gym_agreement("uk", "XMgdYd2b9y3VGa2o7RW9Vk") is False


def test_unrecognised_template_id_is_rejected() -> None:
    assert is_gym_agreement("uk", "some-rebrand-template-id") is False


def test_missing_template_id_is_rejected() -> None:
    """Allowlist, never denylist: a missing id fails closed, same as an
    unrecognised one."""
    assert is_gym_agreement("uk", None) is False


def test_unrecognised_account_is_rejected() -> None:
    assert is_gym_agreement("some-other-account", _UK_GYM_ID) is False


def test_reason_distinguishes_missing_from_unrecognised() -> None:
    assert agreement_gate_ignore_reason(None) == "agreement_type: missing pandadoc_template_id"
    reason = agreement_gate_ignore_reason("tpl-rebrand-123")
    assert "unrecognised template" in reason
    assert "tpl-rebrand-123" in reason


def test_uk_and_int_allowlists_are_disjoint() -> None:
    assert GYM_TEMPLATE_IDS["uk"].isdisjoint(GYM_TEMPLATE_IDS["int"])
