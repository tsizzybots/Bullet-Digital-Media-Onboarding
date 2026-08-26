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
auto-link. Address is NOT a CORROBORATOR: `Company.Address` and `Company.Zip`
come from the same HubSpot company record, so address agrees exactly when the
key already does and corroborates nothing (review round 2, finding 2). It IS a
DISQUALIFIER (review round 5, finding 1) - `addresses_materially_diverge` - and
the two directions are not symmetric: a signal that collapses with the key can
never GRANT a link it did not already imply, but a DIFFERING address can still
refuse one, and that refusal is the only thing separating one owner's two sites
when brand, head-office postcode and phone all match. So address votes to
REFUSE, never to accept, and its absence abstains rather than failing closed.

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

# Postcode tokens: maximal runs of letters OR digits. Splitting on the
# alpha<->digit TRANSITION as well as on separators is the whole point - it is
# what makes the token set identical for "1011 AB", "1011AB" and "1011-AB", and
# therefore what makes the key separator-independent (review round 6, finding 1).
# `str.split()` could not do this: it only ever splits on whitespace, so
# "1011AB" stayed one token while "1011 AB" became two.
_POSTCODE_TOKEN = re.compile(r"[A-Z]+|[0-9]+")

# US ZIP+4 ("94107-1234") identifies the same delivery area as its ZIP5
# ("94107"), so one business writing each form must not split into two clients.
# Reduced to the ZIP5 BEFORE tokenizing, because the sort below would otherwise
# interleave the two digit groups and a positional truncation would be garbage.
_US_ZIP_PLUS_FOUR = re.compile(r"^(\d{5})-?\d{4}$")

# Phone comparison uses the TAIL of the digits so a country code / trunk prefix
# difference ("+44 7700 900123" vs "07700 900123") does not split one business.
# 9 is long enough that two genuinely different numbers colliding is not a real
# scenario, and short enough to survive every national prefix convention.
_PHONE_SIGNIFICANT_DIGITS = 9
_PHONE_MIN_DIGITS = 7

# A number made of a single repeated digit ("000000000") or an alternating
# two-digit block ("121212121") is filler, not a contact. Rejecting it stops
# two unrelated clients corroborating each other purely because both had a
# placeholder in the phone field.
#
# NARROWED (review round 5): this was "two or fewer DISTINCT digits", which
# rejects real UK landlines - "+44 20 7700 0000" has the tail "770000000",
# distinct digits {7, 0}. Bar 2 was therefore permanently unsatisfiable for
# every client on such a number, so a genuine returning client got a duplicate
# sub-account on EVERY signing, silently. Filler is a matter of SHAPE (one
# digit, or a repeating pair) rather than of how few digits happen to appear.
_PLACEHOLDER_PHONE_TAILS = frozenset({"123456789", "987654321", "012345678"})

# UK postcode: outward (area + district) then inward (sector + unit). Searched
# ANYWHERE in the string so a value that carries the town too ("London E8 1AA")
# keys identically to the bare postcode - without this, the same business keys
# two different ways depending on how the HubSpot `Company.Zip` field happened
# to be filled in.
#
# ANCHORED on both sides with negative lookaround (review round 4, one-line
# fix): without it the pattern matches a SUBSTRING of a malformed value, so
# "AB12 3CDE" -> "AB123CD" and "E81AAX" -> "E81AA" both minted a confident,
# WRONG UK key from a value that is not actually that postcode - worse than
# falling through to step 2/3 below, which is what a value neither of these
# lookarounds match now does instead.
#
# SEPARATOR relaxed from `\s*` to `[\s,.\-]*` (review round 5, finding 2): a
# postcode pasted out of an address line arrives punctuated ("E8, 1AA"), and
# with a whitespace-only separator it fell past this branch into step 3 and
# keyed differently from the same business's bare "E8 1AA" - one business, two
# keys, so no DB candidate and a duplicate sub-account with no flag.
_UK_POSTCODE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{1,2}[0-9][A-Z0-9]?)[\s,.\-]*([0-9][A-Z]{2})(?![A-Z0-9])"
)

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

# Typed into the signing-contact fields when the real signer is not known.
# Compared against the lowercased "first last" form. Without this, two
# unrelated clients whose documents both name a department rather than a person
# corroborate each other on bar 3 - the same failure the placeholder postcode
# and phone denylists exist to prevent.
_PLACEHOLDER_CONTACT_NAMES = frozenset(
    {
        "head office",
        "the office",
        "accounts department",
        "accounts payable",
        "front desk",
        "n a",
        "not applicable",
        "to be confirmed",
    }
)


# NFKD decomposes an accented letter into base + combining mark, but a letter
# whose glyph is not a base-plus-mark composition has nothing to decompose -
# NFKD leaves o-slash, eszett, l-stroke, ash and thorn untouched, and the
# ASCII-only tokenizer then DELETES them. So "Bjørn Fitness" keyed as
# "bjrnfitness" against "Bjorn Fitness" -> "bjornfitness": one business, two
# keys, no DB candidate, duplicate sub-account, no flag (review round 5).
# These are the letters the INT PandaDoc account actually introduces.
_TRANSLITERATIONS = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "ß": "ss",
        "ẞ": "SS",
        "ł": "l",
        "Ł": "L",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "TH",
        "ı": "i",
        "İ": "I",
    }
)


def _fold_unicode(value: str) -> str:
    """Strip accents so "Café Gym" and "Cafe Gym" normalize identically.

    Two passes, because they cover disjoint sets. The explicit transliteration
    table handles letters NFKD cannot decompose (see `_TRANSLITERATIONS`);
    NFKD then splits genuinely composed characters into base + combining mark
    and the marks are dropped. Without either, the ASCII-only tokenizer
    silently deletes the letter entirely ("café" -> "caf"), producing a
    DIFFERENT key for the same business. Both matter now that the INT PandaDoc
    account is live.
    """
    translated = value.translate(_TRANSLITERATIONS)
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", translated) if not unicodedata.combining(ch)
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
    2. Otherwise TOKENIZE on whitespace, strip each token to alphanumerics,
       and - when any token carries a digit - DISCARD the purely-alphabetic
       tokens, joining what remains in ORIGINAL order. International postcodes
       stay usable ("75008", "D02 X285" -> "D02X285") and a town name attached
       to one drops out, so "75008 Paris" and "Paris 75008" both key as
       "75008" now that the INT account is live.

    3. THEN reject placeholders ("N/A", "TBC", "00000") and anything shorter
       than `_MIN_POSTCODE_LEN`, returning "" so the key computes to None and
       the signing fails safe to CREATE. (Renumbered, review round 5: the code
       has always applied this rejection to the POST-drop joined string, but
       the docstring listed it as step 2 while insisting "Order matters" -
       pointing a reader at the wrong sequence. Observable difference:
       "00000 ABC" is rejected under the real order and accepted as "00000"
       under the documented one. The code's order is the safer of the two, so
       the fix is to the docstring.)

    THE INVARIANT, stated so it can be tested rather than assumed: one postal
    value produces one key regardless of the FORM it arrives in. Four axes
    vary independently in the wild - token order, separator character, case,
    and an attached town - and the normalization must be blind to all four.

    FIXED (review round 5, finding 2): round 4 fixed the ORDER axis by sorting
    tokens, and in doing so broke the SEPARATOR axis - the same class of bug it
    was fixing. "K1A 0B1" sorted to "0B1K1A" while "K1A0B1" stayed "K1A0B1";
    "94107 1234" -> "123494107" against "94107-1234" -> "941071234". Both
    matched BEFORE round 4. Dropping alphabetic tokens instead of sorting
    achieves order-independence for the case that actually motivated it (a town
    beside a numeric postcode) without reordering anything, so a value that
    differs only in punctuation now canonicalizes identically to one that does
    not.

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
    flat = _NON_ALNUM.sub("", upper)
    # The UK pattern is tried on the RAW string AND on the separator-stripped
    # form. With the raw string alone the UK branch is ITSELF
    # separator-sensitive: "AB12CD" matches and "AB 12 CD" does not, so one
    # postcode keys two ways. Found by the property runner, not by example -
    # every hand-picked UK case in the suite happened to match both ways.
    for probe in (upper, flat):
        uk_match = _UK_POSTCODE.search(probe)
        if uk_match is not None:
            return uk_match.group(1) + uk_match.group(2)
    zip_plus_four = _US_ZIP_PLUS_FOUR.match(flat)
    if zip_plus_four is not None:
        return zip_plus_four.group(1)
    # Tokenize on alpha/digit runs, then SORT. The two steps do different jobs
    # and both are load-bearing: the tokenizer delivers separator-independence,
    # the sort delivers order-independence ("75008 Paris" == "Paris 75008").
    # Nothing is DROPPED, which is what makes this non-lossy.
    tokens = _POSTCODE_TOKEN.findall(upper)
    stripped = "".join(sorted(tokens))
    if len(stripped) < _MIN_POSTCODE_LEN or stripped in _PLACEHOLDER_POSTCODES:
        return ""
    # A value whose DIGIT content is itself filler is filler, however many
    # letters sit beside it ("00000 ABC"). Checked on the digits alone because
    # the letters would otherwise satisfy the distinct-character test below.
    digits = "".join(token for token in tokens if token.isdigit())
    if digits and (digits in _PLACEHOLDER_POSTCODES or len(set(digits)) == 1):
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


def _is_repeating_pair(digits: str) -> bool:
    """True when the whole run is one two-digit block repeated ("121212121").

    Filler by SHAPE. Deliberately narrower than "few distinct digits", which
    also matched real landlines whose subscriber number is mostly zeroes
    ("+44 20 7700 0000" -> "770000000"). A repeating pair cannot occur in a
    real allocated number; a low digit count routinely does.
    """
    if len(digits) < 4 or len(set(digits)) != 2:
        return False
    return all(ch == digits[index % 2] for index, ch in enumerate(digits))


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
        len(set(tail)) == 1
        or _is_repeating_pair(tail)
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
    audit, for a human resolving a flag, and - as a DISQUALIFIER only - by
    `addresses_materially_diverge`. It never votes to ACCEPT a link.

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

    BOTH PARTS REQUIRED (review round 5, finding 4). Concatenate-and-strip made
    a blank `Client.LastName` on both rows collapse this bar to a FIRST-NAME
    match: `contact_name_agrees("Sarah", None, "Sarah", None)` returned True.
    Two different Sarahs at two sites, sharing an `ops@` mailbox and a
    head-office line, both with a blank `Company.Zip`, linked with no flag -
    reopening round 2's finding 5 through a different door. A surname is also
    exactly the field most likely to be absent: `clients_payload` sources it
    from a merge token pinned to the UK template, so an INT template lacking it
    yields None on EVERY row.

    Consequence, recorded rather than discovered later: where the surname is
    never populated, bar 3 is permanently unsatisfiable and the unkeyed path
    never auto-links there. That is the safe direction - a spare sub-account is
    visible and deletable, a wrong link puts one client's assets inside
    another's - and it is the same posture this module takes everywhere else.

    A PLACEHOLDER is not a name either. "Head Office" / "Front Desk" /
    "Accounts Department" are what gets typed when the real signer is not
    known, so two unrelated clients would corroborate each other on it - the
    same reasoning `normalize_postcode` and `normalize_phone` already apply to
    their own filler values. Only two-word placeholders need listing: a
    single-word one ("Accounts", "Admin") leaves the surname blank and is
    already refused by the both-parts rule above.
    """
    first_norm_a, last_norm_a = (first_a or "").strip().lower(), (last_a or "").strip().lower()
    first_norm_b, last_norm_b = (first_b or "").strip().lower(), (last_b or "").strip().lower()
    if not (first_norm_a and last_norm_a and first_norm_b and last_norm_b):
        return False
    if f"{first_norm_a} {last_norm_a}" in _PLACEHOLDER_CONTACT_NAMES:
        return False
    return (first_norm_a, last_norm_a) == (first_norm_b, last_norm_b)


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


def addresses_materially_diverge(address_a: str | None, address_b: str | None) -> bool:
    """True ONLY when both addresses are present and normalize differently.

    A DISQUALIFIER, not a corroborator - and the distinction is the whole
    point. Round 2 (finding 2) correctly established that address cannot
    CORROBORATE an identity-key match: `Company.Address` and `Company.Zip` come
    from the same HubSpot company record, so the address agrees exactly when
    the key already does, including in the franchisee case the bar exists to
    block. That argument does not transfer to disqualification. A DIFFERING
    address can only ever prevent a merge, never cause one, so reading it in
    this direction adds a separator without adding a false-match risk.

    This is what closes round 5's finding 1: one owner, two sites, the same
    brand in `Company.Name`, the same head-office `Company.Zip` and the same
    `Client.Phone` on both documents clears every other bar, and site 2 links
    into site 1's sub-account with no flag. The street addresses differ, and
    that is the only signal on the row that does.

    **Note the deliberate ASYMMETRY with `names_materially_diverge`**, where an
    absent name IS divergence. These read opposite because they do opposite
    jobs: an absent NAME must not be allowed to satisfy a corroborating bar, so
    absence fails closed; an absent ADDRESS must not be allowed to veto an
    otherwise-corroborated link, so absence abstains. Absence is never treated
    as agreement in either - it simply cannot vote here.

    Comparison is deliberately STRICT (normalized equality): "1 Mare St" and
    "1 Mare Street" diverge and the link is refused and flagged. That is the
    safe direction - a refusal costs a spare sub-account a human can merge via
    S1-26e, a wrong merge puts one client's assets in another's account.
    """
    stem_a = normalize_name(address_a)
    stem_b = normalize_name(address_b)
    if not stem_a or not stem_b:
        return False
    return stem_a != stem_b


__all__ = [
    "LEGAL_ENTITY_PLACEHOLDER",
    "addresses_materially_diverge",
    "compute_identity_key",
    "contact_name_agrees",
    "corroborating_signal_agrees",
    "identity_name",
    "names_materially_diverge",
    "normalize_name",
    "normalize_phone",
    "normalize_postcode",
]
