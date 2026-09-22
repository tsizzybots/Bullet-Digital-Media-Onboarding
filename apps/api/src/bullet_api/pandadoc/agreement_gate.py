"""S1-38 phase 1: interim agreement-type gate, keyed on `pandadoc_template_id`.

`extract_signed_documents` used to fan out on `status == "document.completed"`
alone, so ANY signed document in either PandaDoc account - a gym onboarding
agreement, a rebrand agreement, a content-production agreement, an employee
contract - created a `clients` row and provisioned a real GHL sub-account.
Three wrong documents already reached staging this way (two Rebrand, one
Content Production).

The full S1-38 design reads Bullet's `Agreement_Type` merge field and checks
it against an allowlist, but that field's exact name/type is still blocked on
Bullet's spec (S1-39). This module is the interim mechanism: `template.id` is
already extracted (`worker/clients_payload.py`) and unlike `Agreement_Type` we
already know which live template ids correspond to a real gym onboarding, so
the same allowlist-never-denylist principle applies one level down.

Template ids were confirmed live via `GET /public/v1/templates` on both
PandaDoc accounts (20/08/2026, names only, no client PII read). Two facts that
matter for anyone editing this list:

- UK and INT do NOT share template ids - each account has its own set, so the
  allowlist is keyed by account, never a flat id set.
- UK also has a second gym-shaped template ("Ads Management Only Agreement",
  `XMgdYd2b9y3VGa2o7RW9Vk`) that is deliberately NOT allowlisted yet - owner
  call, pending confirmation it is in scope. Adding it later is a one-line
  change here, not a design change.

THE FIELD SPEC HAS SINCE LANDED (Bullet, 18/09/2026), and it changes this
module's future role rather than its current behaviour. `Agreement Type` is a
mandatory single-select on BOTH gym templates, values `Gym Single | Gym Multi`.
Because it cannot be left blank, "no value = ignore" becomes safe, so phase 2
supersedes this check as the primary mechanism and the template-id allowlist
drops to belt-and-braces - the plan's own fallback, now the secondary rather
than the only guard.

Phase 2 is NOT built here and this module reads NO agreement-type field; it
gates on `pandadoc_template_id` alone. S1-39 extracts the five fields and
S1-38 phase 2 gates on them, on a separate branch. One trap recorded for
whoever takes it: three spellings are in circulation (`Agreement Type`,
`Existing Gym Client`, `Agreement_Type`), so read the real field id off a
signed document with `scripts/inspect_pandadoc_document.py` before coding
against a guessed name - a field read by the wrong name fails silently.
"""

from __future__ import annotations

# UK gym onboarding template: "NEW GYM CLIENT & Bullet Digital Media -
# Digital Marketing Partnership Agreement". Confirmed correct by name against
# the live UK template list.
_UK_GYM_TEMPLATE_IDS: frozenset[str] = frozenset({"Kn7vBp56MLSxreXPXwpNWk"})

# INT gym onboarding template: "NEW FITNESS FACILITY CLIENT & BULLET - Digital
# Marketing Partnership". Confirmed twice over - by name against the live INT
# template list, and independently by Bullet (Steve, 18/09/2026, "INT Gym
# Agreement Template ID").
#
# ONE ID, NOT TWO (S1-49, 21/09/2026). `jqJJFqN5sFnwZypr3owPRV` was also
# allowlisted here, selected by NAME alone from the live template list. Queried
# live on 21/09 it resolves to "NEW CONSUMER CLIENT & BULLET - Digital
# Marketing Partnership" - a different CATEGORY of agreement, not an
# unconfirmed gym one. It was set to PROCEED, so a signed consumer-client
# document would have provisioned the full gym fan-out: a real sub-account in a
# live agency for a document that is not a gym onboarding. That is precisely
# the unanticipated-document-type case rule 1 of this gate exists to refuse,
# sitting inside the allowlist itself.
#
# It is removed rather than left pending, because an allowlist is only
# fail-safe for ids that are ABSENT; a present-but-unverified id inherits none
# of that safety. Do NOT re-add it on the strength of the id existing - it
# exists, and it is the wrong kind. Re-add only if Bullet confirms that a
# consumer-client engagement is a gym onboarding that should fan out.
_INT_GYM_TEMPLATE_IDS: frozenset[str] = frozenset({"rQ9jQ6f4dcP2jjmCfF3H6Y"})

# Keyed by PandaDoc account label ("uk"/"int" - see `pandadoc.accounts`).
# `.get(account, frozenset())` is the fail-closed default for an unrecognised
# account label, mirroring the empty-secret / empty-key no-op pattern used
# elsewhere in the PandaDoc integration.
GYM_TEMPLATE_IDS: dict[str, frozenset[str]] = {
    "uk": _UK_GYM_TEMPLATE_IDS,
    "int": _INT_GYM_TEMPLATE_IDS,
}


def is_gym_agreement(account: str, template_id: str | None) -> bool:
    """Return True iff `template_id` is the allowlisted gym template for `account`.

    Allowlist, never denylist: an unrecognised id, an id from the WRONG
    account (UK/INT ids are disjoint - reusing one under the other account
    must not accidentally match), and a missing id (`None`) are all rejected.
    A missing id could mean a real gym document with an unusual payload shape,
    but failing open there would reopen exactly the wrong-document risk this
    gate exists to close, so it stays fail-closed like every other case here.
    """
    return template_id is not None and template_id in GYM_TEMPLATE_IDS.get(account, frozenset())


def agreement_gate_ignore_reason(template_id: str | None) -> str:
    """Build the `onboarding_events.ignored_reason` text for a gated-out signing.

    Distinguishes "no template id at all" from "a template id we don't
    recognise" - the former can indicate a PandaDoc payload-shape surprise
    worth investigating on its own, not necessarily an off-scope document.
    """
    if template_id is None:
        return "agreement_type: missing pandadoc_template_id"
    return f"agreement_type: unrecognised template {template_id!r}"


__all__ = [
    "GYM_TEMPLATE_IDS",
    "agreement_gate_ignore_reason",
    "is_gym_agreement",
]
