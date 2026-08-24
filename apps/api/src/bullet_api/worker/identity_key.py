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
the PHONE, present on both rows and matching. Absence is not agreement: when
only name and postcode agree, the caller flags rather than merges. The failure
direction is deliberate, since a missed link creates a spare sub-account
(visible, deletable) while a wrong link puts one client's assets inside another
client's account.

WHAT THIS DOES AND DOES NOT PREVENT - read before trusting it. The bar narrows
the franchisee case to "same brand, same head-office postcode, same signing
contact"; it does NOT eliminate it. A franchisee who signs for both studios
personally presents the same `Client.Phone` both times and will still
auto-link. Address is deliberately NOT a second signal: `Company.Address` and
`Company.Zip` come from the same HubSpot company record, so address agrees
exactly when the key already does and corroborates nothing (review round 2,
finding 2). `clients.address` is persisted for audit and for whoever resolves a
flag, but it does not vote.

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

# A number made of one or two repeated digits ("0000000000", "1111122222") is
# filler, not a contact. Rejecting it stops two unrelated clients corroborating
# each other purely because both had a placeholder in the phone field.
_PHONE_MIN_DISTINCT_DIGITS = 2
_PLACEHOLDER_PHONE_TAILS = frozenset({"123456789", "987654321", "012345678"})

# UK postcode: outward (area + district) then inward (sector + unit), with any
# amount of whitespace between. Searched ANYWHERE in the string so a value that
# carries the town too ("London E8 1AA") keys identically to the bare postcode -
# without this, the same business keys two different ways depending on how the
# HubSpot `Company.Zip` field happened to be filled in.
#
# ANCHORED on both sides with negative lookaround (review round 4, one-line
# fix): without it the pattern matches a SUBSTRING of a malformed value, so
# "AB12 3CDE" -> "AB123CD" and "E81AAX" -> "E81AA" both minted a confident,
# WRONG UK key from a value that is not actually that postcode - worse than
# falling through to step 2/3 below, which is what a value neither of these
# lookarounds match now does instead.
_UK_POSTCODE = re.compile(r"(?<![A-Z0-9])([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})(?![A-Z0-9])")

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
    3. Otherwise TOKENIZE on whitespace, strip each token to alphanumerics,
       and join the tokens back in SORTED order, so international postcodes
       stay usable ("75008", "D02 X285" -> "D02X285") AND are insensitive to
       which order the words arrived in - "75008 Paris" and "Paris 75008"
       both normalize to "75008PARIS" - now that the INT account is live.

    FIXED (review round 4, finding 3): step 3 used to join tokens in
    WHATEVER ORDER they appeared, so "75008 Paris" and "Paris 75008" - the
    same French business, entered two different ways - produced two
    different keys and silently failed to match. Sorting the tokens before
    joining is order-independent by construction: it only ever collapses a
    literal reordering of the SAME words, so it cannot create a NEW collision
    between two postcodes that merely share characters (each token's own
    internal character sequence is untouched).

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
    tokens = [_NON_ALNUM.sub("", token) for token in upper.split()]
    stripped = "".join(sorted(t for t in tokens if t))
    if len(stripped) < _MIN_POSTCODE_LEN or stripped in _PLACEHOLDER_POSTCODES:
        return ""
    # A denylist is a BLOCKLIST, so it only ever catches the placeholders
    # somebody thought of - "TBA", "99999" and "12345" all sail past one
    # (review round 2, P2). Add two shape checks that reject filler by FORM
    # rather than by membership: a value made of a single repeated character
    # ("99999", "XXXX"), and one with no digit at all ("TBA", "NONE"). Every
    # real postal format in use carries at least one digit.
    if len(set(stripped)) == 1:
        return ""
    if not any(ch.isdigit() for ch in stripped):
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


def _is_sequential_digits(digits: str) -> bool:
    """True when every adjacent pair steps +1 or every pair steps -1 (mod 10).

    Catches "123456789", "234567890", "987654321", "876543210" and every
    other rotation of the keypad-run filler pattern people type when they
    have no real number to hand - by FORM, not by enumerating each rotation
    in a denylist (same reasoning `normalize_postcode` already applies).
    """
    if len(digits) < 2:
        return False
    pairs = list(zip(digits, digits[1:], strict=False))
    ascending = all((int(a) + 1) % 10 == int(b) for a, b in pairs)
    descending = all((int(a) - 1) % 10 == int(b) for a, b in pairs)
    return ascending or descending


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
    tail = digits[-_PHONE_SIGNIFICANT_DIGITS:]
    # A placeholder number is not a signal. "0000000000" / "1234567890" pass
    # the length check, so without this two unrelated clients whose phone field
    # was filled with filler would CORROBORATE each other and auto-link - the
    # exact mis-merge this signal exists to prevent (review round 2, P2).
    #
    # FIXED (review round 4, one-line finding): "1234567890" is 10 digits, so
    # the LAST 9 are "234567890" - not in `_PLACEHOLDER_PHONE_TAILS` (which
    # only holds "123456789"), so the most common filler number in existence
    # was passing as a corroborating signal. `_is_sequential_digits` catches
    # it (and every other rotation) by SHAPE rather than growing the denylist
    # one string at a time - same principle already applied to postcodes
    # above ("a denylist only catches the placeholders somebody thought of").
    if (
        len(set(tail)) <= _PHONE_MIN_DISTINCT_DIGITS
        or tail in _PLACEHOLDER_PHONE_TAILS
        or _is_sequential_digits(tail)
    ):
        return ""
    return tail


def corroborating_signal_agrees(*, phone_a: str | None, phone_b: str | None) -> bool:
    """True when the PHONE - a signal independent of the company record - agrees.

    Required before two rows sharing an identity key are auto-linked. Name +
    postcode is not proof of one business: `Company.Zip` is the COMPANY
    postcode, not the studio's, so a franchisee running two studios who enters
    only the brand ("F45 Training") plus their head-office postcode produces an
    identical key AND identical normalized names.

    **Why phone and NOT address (review round 2, finding 2).** The first
    implementation accepted `Company.Address` as an alternative signal. That is
    worthless as corroboration: address and postcode are read from the SAME
    HubSpot company record (`Company.Address` / `Company.Zip`), so they agree
    exactly when the key already agrees - including in the franchisee case the
    bar exists to block. `Client.Phone` is the signing CONTACT, sourced
    independently of the company record, so it is the only second signal we
    hold that can actually disagree. `clients.address` is still persisted, for
    audit and for a human resolving a flag, but it does not vote.

    **This does not make the franchisee case impossible**, and the docstrings
    must not claim it does: a franchisee who signs both studios personally
    presents the same contact number both times and will still auto-link. It
    narrows the case to "same brand, same head-office postcode, same signing
    contact", which is a materially smaller target than before.

    Absence is NOT agreement: a phone missing on either side returns False, so
    "only name and postcode agree" flags for a human instead of merging. That
    is the conservative direction on purpose - a missed link creates a spare
    sub-account, a wrong link puts one client's assets in another's account.
    """
    phone_norm_a = normalize_phone(phone_a)
    return bool(phone_norm_a) and phone_norm_a == normalize_phone(phone_b)


def contact_name_agrees(
    first_a: str | None, last_a: str | None, first_b: str | None, last_b: str | None
) -> bool:
    """True when the SIGNING CONTACT's full name agrees, case/whitespace-insensitive.

    A second corroborating signal, used ALONGSIDE phone (not instead of it) on
    the NULL-identity-key email fallback (S1-26c review round 4, finding 4):
    that path has no postcode to anchor the match the way the identity-key
    path does (`Company.Zip` narrows a shared-brand collision to "same
    head-office postcode too"), so requiring phone alone there reopens
    exactly the franchise-merge case round 2 closed (F5) - two sites sharing
    one `ops@` mailbox and one head-office number, with no postcode present
    to tell them apart.

    `Client.FirstName`/`Client.LastName` is the person who signed, sourced
    independently of both the company record (name/postcode/address) and the
    contact channel (email/phone), so it does not collapse alongside them the
    way address collapses alongside postcode (see `corroborating_signal_agrees`
    docstring). Two DIFFERENT franchise sites under one shared mailbox and one
    shared head-office line are still expected to have DIFFERENT individuals
    signing for their own site, per the client's "franchisees are separate
    clients with no shared access" answer - a genuine returning client re-
    signing without a postcode this time is far more likely to be signed by
    the SAME person twice.

    Absence is NOT agreement: a name missing (or blank) on either side returns
    False, same posture as `corroborating_signal_agrees` - a missing signal
    must never be read as a match.
    """
    name_a = f"{(first_a or '').strip()} {(last_a or '').strip()}".strip().lower()
    name_b = f"{(first_b or '').strip()} {(last_b or '').strip()}".strip().lower()
    return bool(name_a) and name_a == name_b


def names_materially_diverge(name_a: str | None, name_b: str | None) -> bool:
    """True when two names normalize to different stems, OR either is unusable.

    Deliberately STRICT (exact equality of the full normalized stems). The
    leniency in this module lives in the KEY - the 6-char truncation - not
    here; this function's whole job is to catch the false matches that
    leniency creates. So "Brand Gym Hackney Gym" vs "Brand Gym Hackney"
    diverges and gets flagged rather than merged, which is the safe side of
    "never auto-merge":
    a missed link creates a spare sub-account (visible, deletable), a wrong
    link puts one client's assets in another client's account.

    An EMPTY stem on either side is divergence, not agreement (review round 2,
    finding 6). Two unidentifiable signings both normalize to "", so plain
    equality called them a match - directly contradicting `identity_name`'s
    guarantee that an unidentifiable signing can never merge with another.
    Absence is not agreement here either, consistent with how a missing
    postcode is treated when classifying a GHL hit.
    """
    stem_a = normalize_name(name_a)
    stem_b = normalize_name(name_b)
    if not stem_a or not stem_b:
        return True
    return stem_a != stem_b


__all__ = [
    "LEGAL_ENTITY_PLACEHOLDER",
    "compute_identity_key",
    "corroborating_signal_agrees",
    "identity_name",
    "names_materially_diverge",
    "normalize_name",
    "normalize_phone",
    "normalize_postcode",
]
