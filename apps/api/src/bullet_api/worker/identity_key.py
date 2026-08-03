"""Pure helpers: the returning-client identity key (S1-26c).

The returning-client check keys on a normalized identity derived from the
business name + postcode, replacing the old `email` match. Email is an
unreliable identity: franchises share similar emails but are DIFFERENT
clients, and one business signs with SEVERAL emails over time.

    identity_key = first6(normalize_name(identity_name(...))) + "|" + normalize_postcode(postcode)

The truncation to the first 6 characters is deliberately lenient (so minor
tail differences after normalization still collapse together). Because it is
lenient, two genuinely different businesses that share a 6-char name prefix
AND a postcode (e.g. "Fitness First" vs "Fitness Studio" at the same postcode)
can collide on the key. The caller therefore pairs an `identity_key` match
with `names_materially_diverge` on the FULL normalized names: a divergent one
is flagged for a human rather than auto-merged.

Matching names are still not enough on their own. `Company.Zip` is the COMPANY
postcode, not the studio's, so a franchisee running two studios who enters only
the brand plus their head-office postcode produces an identical key AND
identical names. A link therefore also requires `corroborating_signal_agrees` -
a phone or address line present on BOTH rows and matching. Absence is not
agreement: when only name and postcode agree, the caller flags rather than
merges. The failure direction is deliberate, since a missed link creates a
spare sub-account (visible, deletable) while a wrong link puts one client's
assets inside another client's account.

Fail-safe to CREATE: `compute_identity_key` returns None whenever it lacks a
usable signal (no normalized business name, or no usable postcode). A None key
makes the returning-client match self-skip, so an unidentifiable signing
becomes a fresh client rather than being merged into the wrong one. The caller
then falls back to an email-keyed DB sibling check (S1-26's original behaviour)
so a NULL key never means "no duplicate protection at all".

`LEGAL_ENTITY_PLACEHOLDER` lives here rather than in `client_record` because
it is an identity concern: the placeholder is what we write when we could not
learn the client's name, so it must never be treated as one. `client_record`
imports this module already, so this is the cycle-free home for it.

PURE: no I/O, no DB, no Inngest. Unit-tested against synthetic strings.
"""

from __future__ import annotations

import re
import unicodedata

# Written into `clients.legal_entity` (NOT NULL) when neither the signed
# legal-trading-name field nor `Company.Name` yielded anything. It is a marker,
# not a name - `identity_name` rejects it so an unidentifiable signing can never
# key on (and therefore merge with) another unidentifiable one.
LEGAL_ENTITY_PLACEHOLDER = "Unknown - needs review"

# A leading article carries no identity ("The Gym Group" == "Gym Group") and,
# left in, would eat the 6-char budget ("the gy"). Dropped as a leading word.
_LEADING_ARTICLES = frozenset({"the"})

# Trailing company-type suffixes are noise for identity ("Foobar Ltd" ==
# "Foobar Limited" == "Foobar"). Dropped as a trailing word.
_TRAILING_SUFFIXES = frozenset({"ltd", "limited"})

_NAME_PREFIX_LEN = 6
_KEY_SEPARATOR = "|"

# Tokenize on runs of alphanumerics, so punctuation and whitespace both split
# ("ltd." -> "ltd", "F45  Training" -> "f45", "training"). Applied AFTER the
# unicode fold below, so accented letters are already plain ASCII by this point.
_ALNUM_TOKEN = re.compile(r"[a-z0-9]+")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_NON_DIGIT = re.compile(r"[^0-9]")

# Phone comparison uses the TAIL of the digits so a country code / trunk prefix
# difference ("+44 7700 900123" vs "07700 900123") does not split one business.
# 9 is long enough that two genuinely different numbers colliding is not a real
# scenario, and short enough to survive every national prefix convention.
_PHONE_SIGNIFICANT_DIGITS = 9
_PHONE_MIN_DIGITS = 7

# UK postcode: outward (area + district) then inward (sector + unit), with any
# amount of whitespace between. Searched ANYWHERE in the string so a value that
# carries the town too ("London E8 1AA") keys identically to the bare postcode -
# without this, the same business keys two different ways depending on how the
# HubSpot `Company.Zip` field happened to be filled in.
_UK_POSTCODE = re.compile(r"([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})")

# Values people type INSTEAD of a postcode. Each is non-empty after stripping,
# so without this denylist they mint a real-looking key that every other
# placeholder-using client would share - the exact mis-merge the key exists to
# prevent. Compared against the alphanumeric-stripped, uppercased form, so
# "N/A" and "n.a." both arrive here as "NA".
_PLACEHOLDER_POSTCODES = frozenset(
    {
        "NA",
        "NONE",
        "NIL",
        "NOTAPPLICABLE",
        "TBC",
        "TBD",
        "UNKNOWN",
        "XX",
        "XXX",
        "XXXX",
        "XXXXX",
        "0",
        "00",
        "000",
        "0000",
        "00000",
        "000000",
    }
)

# Shortest postcode we will key on. UK outward+inward is 5 ("E81AA"); the
# shortest national formats in use are 4 digits. Anything under this is noise
# rather than an address.
_MIN_POSTCODE_LEN = 3


def _fold_unicode(value: str) -> str:
    """Strip accents so "Café Gym" and "Cafe Gym" normalize identically.

    NFKD splits an accented character into base + combining mark, then the
    marks are dropped. Without this the ASCII-only tokenizer silently deletes
    the accented letter entirely ("café" -> "caf"), producing a DIFFERENT key
    for the same business. Relevant now that the INT PandaDoc account is live.
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )


def identity_name(business_name: str | None, legal_entity: str | None = None) -> str | None:
    """The name that identifies this client: trading name, else legal entity.

    Mirrors `_build_location_payload`'s `business_name or legal_entity` so the
    key, the divergence guard and the name we send GHL all agree on who this
    client is. Without the fallback, a document that carries only the signed
    legal-trading-name field (no `Company.Name`) silently opts out of
    returning-client matching entirely.

    `LEGAL_ENTITY_PLACEHOLDER` is NOT a name - it is what we write when we
    learned nothing - so it is rejected here and the caller fails safe to
    CREATE rather than uniting every unidentifiable signing under one key.
    """
    if business_name and business_name.strip():
        return business_name
    if legal_entity and legal_entity.strip() and legal_entity != LEGAL_ENTITY_PLACEHOLDER:
        return legal_entity
    return None


def normalize_name(name: str | None) -> str:
    """Normalize a business name to a compact alphanumeric identity stem.

    fold accents -> lowercase -> tokenize on non-alphanumerics -> drop a
    leading article ("the") -> drop a trailing company suffix
    ("ltd"/"limited") -> join the remaining tokens with no separators. Returns
    "" when nothing usable remains (None, blank, or a name that is only an
    article/suffix).
    """
    if not name:
        return ""
    tokens = _ALNUM_TOKEN.findall(_fold_unicode(name).lower())
    if tokens and tokens[0] in _LEADING_ARTICLES:
        tokens = tokens[1:]
    if tokens and tokens[-1] in _TRAILING_SUFFIXES:
        tokens = tokens[:-1]
    return "".join(tokens)


def normalize_postcode(postcode: str | None) -> str:
    """Normalize a postcode to a canonical key fragment, or "" if unusable.

    Order matters:

    1. A UK postcode found ANYWHERE in the string wins, returned as
       outward+inward with no space ("London E8 1AA" -> "E81AA", same as
       "E8 1AA"). This is what stops one business keying two ways.
    2. Otherwise strip to alphanumerics and reject placeholders ("N/A", "TBC",
       "00000") and anything shorter than `_MIN_POSTCODE_LEN`. These return ""
       so the key computes to None and the signing fails safe to CREATE.
    3. Otherwise keep the stripped form, so international postcodes stay usable
       ("75008", "D02 X285" -> "D02X285") now that the INT account is live.

    KNOWN DEVIATION from the S1-26b/c review, recorded here so it is met stated
    rather than discovered: the review asked to "return None for anything
    malformed" after validating a UK postcode. Step 3 does not - applied
    strictly it would return None for every non-UK postcode, disabling
    returning-client matching for the whole INT PandaDoc account, which is
    live. Reverting to the strict reading is a one-line change (drop step 3);
    INT clients would then carry a NULL identity_key and fall back to the email
    sibling check. See the 03/08/2026 CHANGELOG entry.
    """
    if not postcode:
        return ""
    upper = postcode.upper()
    uk_match = _UK_POSTCODE.search(upper)
    if uk_match is not None:
        return uk_match.group(1) + uk_match.group(2)
    stripped = _NON_ALNUM.sub("", upper)
    if len(stripped) < _MIN_POSTCODE_LEN or stripped in _PLACEHOLDER_POSTCODES:
        return ""
    return stripped


def compute_identity_key(business_name: str | None, postcode: str | None) -> str | None:
    """Return `first6(normalize_name) + "|" + normalize_postcode`, or None.

    Callers pass the result of `identity_name(...)` as `business_name`.

    Returns None (match self-skips, fail-safe to CREATE) when the name yields
    no normalized stem OR the postcode is missing / a placeholder / too short -
    we do not match on a name alone, because a no-postcode name prefix is far
    too collision-prone to safely unite clients on.
    """
    name_norm = normalize_name(business_name)
    if not name_norm:
        return None
    postcode_norm = normalize_postcode(postcode)
    if not postcode_norm:
        return None
    return name_norm[:_NAME_PREFIX_LEN] + _KEY_SEPARATOR + postcode_norm


def normalize_phone(phone: str | None) -> str:
    """Reduce a phone number to a comparable stem, or "" if unusable.

    Digits only, then the LAST `_PHONE_SIGNIFICANT_DIGITS`. Comparing the tail
    rather than the whole string makes "+44 7700 900123", "07700 900123" and
    "+447700900123" agree, which they must: the same business re-typing its own
    number in a different format is not evidence of a different business, and
    treating it as such would flag genuine returning clients.

    Returns "" when there are too few digits to be meaningful, so an extension
    or a truncated field never corroborates anything.
    """
    if not phone:
        return ""
    digits = _NON_DIGIT.sub("", phone)
    if len(digits) < _PHONE_MIN_DIGITS:
        return ""
    return digits[-_PHONE_SIGNIFICANT_DIGITS:]


def normalize_address(address: str | None) -> str:
    """Normalize an address line to a comparable stem, or "" if unusable.

    Same fold/lowercase/alphanumeric-token treatment as `normalize_name`, so
    punctuation and spacing differences do not matter. Deliberately does NOT
    try to expand abbreviations ("St" vs "Street"), because the failure
    direction is safe: an address that does not match simply fails to
    corroborate, which flags for review rather than merging.
    """
    if not address:
        return ""
    return "".join(_ALNUM_TOKEN.findall(_fold_unicode(address).lower()))


def corroborating_signal_agrees(
    *,
    phone_a: str | None,
    address_a: str | None,
    phone_b: str | None,
    address_b: str | None,
) -> bool:
    """True when a SECOND identifying signal is present on both sides and agrees.

    Required before two rows sharing an identity key are auto-linked (reviewer
    finding 4). Name + postcode alone is not proof of one business:
    `Company.Zip` is the COMPANY address, not the studio's, so a franchisee
    running two studios who enters only the brand ("F45 Training", "BFT") plus
    their head-office postcode produces an identical key AND identical
    normalized names - and studio 2 would be silently linked into studio 1's
    sub-account.

    Either phone or address line satisfies it. Absence is NOT agreement: when
    neither signal is present on both rows this returns False, so "only name
    and postcode agree" flags for a human instead of merging. That is the
    conservative direction on purpose - a missed link creates a spare
    sub-account, a wrong link puts one client's assets in another's account.
    """
    phone_norm_a = normalize_phone(phone_a)
    if phone_norm_a and phone_norm_a == normalize_phone(phone_b):
        return True
    address_norm_a = normalize_address(address_a)
    return bool(address_norm_a) and address_norm_a == normalize_address(address_b)


def names_materially_diverge(name_a: str | None, name_b: str | None) -> bool:
    """True when two names normalize to DIFFERENT full stems.

    Deliberately STRICT (exact equality of the full normalized stems). The
    leniency in this module lives in the KEY - the 6-char truncation - not
    here; this function's whole job is to catch the false matches that
    leniency creates. So "BFT Hackney Gym" vs "BFT Hackney" diverges and gets
    flagged rather than merged, which is the safe side of "never auto-merge":
    a missed link creates a spare sub-account (visible, deletable), a wrong
    link puts one client's assets in another client's account.
    """
    return normalize_name(name_a) != normalize_name(name_b)


__all__ = [
    "LEGAL_ENTITY_PLACEHOLDER",
    "compute_identity_key",
    "corroborating_signal_agrees",
    "identity_name",
    "names_materially_diverge",
    "normalize_address",
    "normalize_name",
    "normalize_phone",
    "normalize_postcode",
]
