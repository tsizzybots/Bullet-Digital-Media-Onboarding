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
refuse one. It does NOT close the one-owner-two-sites case (corrected round 6,
and this header missed the correction until round 7): one owner with ONE
company record and two deals produces two rows identical on address as well as
postcode, so every bar clears and site 2 still auto-links - see
`test_one_company_record_two_deals_still_auto_links`, which pins that gap as
documented behaviour until `hubspot_company_id` or `Existing_Client_Identifier`
exists in the data. Address votes to REFUSE, never to accept, and its absence
abstains rather than failing closed.

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

# US ZIP+4 ("94107-1234") identifies the same delivery area as its ZIP5
# ("94107"), so one business writing each form must not split into two clients.
# The reduction FALLS THROUGH to the filler checks below rather than returning
# (review round 7): returning early let "00000-0000" and "999999999" mint the
# exact placeholder keys ("00000", "99999") the filler checks exist to reject.
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
# rejects real UK landlines - "+44 20 7700 0000" has the tail "077000000",
# distinct digits {7, 0}. Bar 2 was therefore permanently unsatisfiable for
# every client on such a number, so a genuine returning client got a duplicate
# sub-account on EVERY signing, silently. Filler is a matter of SHAPE (one
# digit, or a repeating pair) rather than of how few digits happen to appear.
#
# The old `_PLACEHOLDER_PHONE_TAILS` denylist ("123456789", "987654321",
# "012345678") is GONE (review round 7): every member is a sequential run, so
# `_is_sequential_digits` subsumed it completely and the membership check was
# dead code that could not fail a test. A guard that cannot fail is worse than
# no guard - it reads as coverage.

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

# The inward half `[0-9][A-Z]{2}` also matches an English ORDINAL, and the
# relaxed separator lets the outward half latch onto a preceding unit number, so
# a floor-number unit was read as the postcode (review round 9, P1.3). But an
# ordinal-shaped inward can ALSO be a real postcode, so `normalize_postcode`
# treats an ordinal match as a fallback rather than discarding it outright (see
# the extraction loop for the ordering rule). `fullmatch` because the inward
# group is always exactly `[0-9][A-Z]{2}` (three chars).
_ORDINAL_INWARD = re.compile(r"[0-9](?:ST|ND|RD|TH)")

# There is deliberately NO postcode denylist any more (review round 7 found
# every membership check dead). The shape checks in `normalize_postcode`
# subsume the old `_PLACEHOLDER_POSTCODES` set completely: its alphabetic
# members ("NA", "TBC", "UNKNOWN", "XXXX", ...) carry no digit; its numeric
# members ("0" ... "000000") are either under `_MIN_POSTCODE_LEN` or a single
# repeated digit. A denylist whose every member is caught earlier cannot fail
# a test, and a guard that cannot fail reads as coverage while providing none.

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
        "accounts dept",
        "accounts payable",
        "front desk",
        "main office",
        "reception desk",
        "n a",
        "na",
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

    THE SPEC, small enough to state completely - which is the round-7 fix:

    1. A UK postcode found in the RAW string is extracted as outward+inward
       ("London E8 1AA" -> "E81AA"). Raw only: round 6's extra probe of the
       separator-stripped form went dead under this spec (a flat-form match is
       boxed in by its own lookarounds to equal the whole flat string, which is
       what step 2 returns anyway) and was deleted in round 7 when the mutation
       runner proved no test could kill it. Separator-free spellings like
       "AB12CD" still match the raw probe directly, and the exotic spelling
       "B 11 1AA" (separators INSIDE the outward half) keys through step 2 as
       the same flat string the probe would extract - no residual at all under
       the round-8 filler rule, pinned by test.
    2. Otherwise the key IS the separator-stripped uppercased string, verbatim -
       no sorting, no dropping, no reordering of any kind - with exactly one
       equivalence: a US ZIP+4 reduces to its ZIP5 ("94107-1234" -> "94107",
       one delivery area, one client), and the reduced value still runs the
       filler gauntlet below rather than returning early (review round 7:
       returning early let "00000-0000" mint the exact placeholder key "00000"
       the gauntlet exists to reject).
    3. Filler is rejected by SHAPE, never by denylist: too short
       (`_MIN_POSTCODE_LEN`), no digit at all ("TBA", "NONE", "XXXX"), or a
       contiguous run of one repeated digit ("00000", "99999", "00000 ABC").
       The contiguity requirement is what keeps real alternating postcodes
       alive: Ottawa's "K1K 1K1" has digit content "111" but no "111" run, so
       it keys normally (a latent false-reject in rounds 5-6, found while
       proving the round-7 rejection rules).

    WHY THE KEY PRESERVES ORDER - the theorem that ends the axis-trading.
    Rounds 4-7 each repaired one axis and silently broke another: round 4
    sorted (order fixed, separator broken), round 5 dropped alphabetic tokens
    (separator fixed, "1011 AB" == "1011 CD" collide added), round 6 sorted
    canonical runs (both fixed, "K1A 0B1" == "B1A 0K1" anagram collide added).
    That was not carelessness. The three properties being chased are jointly
    UNSATISFIABLE:

      S: separator-independence   f("VLT 1117") == f("VLT1117")
      O: order-independence       f("75008 Paris") == f("Paris 75008")
      I: injectivity              f("K1A 0B1") != f("B1A 0K1")

    S forces f to depend only on the separator-stripped ordered string. O then
    forces f("75008PARIS") == f("PARIS75008") - two flat strings differing only
    in run order - so f must be invariant under run reordering. But "K1A0B1"
    and "B1A0K1" share one run multiset, so run-order-invariance maps them
    together, violating I. Any future change restoring O MUST therefore
    reintroduce a collision; there is no clever tokenization that escapes this.

    One property had to go. I is non-negotiable: its failure is a false MERGE
    (one client's assets inside another client's sub-account, the unrecoverable
    direction). So O is DELIBERATELY OPEN: "75008 Paris" and "Paris 75008" key
    differently, as do "75008" and "75008 Paris" (the attached-town axis, open
    for the same reason - closing it means dropping tokens, which is round 5's
    collide). Both failures are SPLITS: the second signing finds no candidate
    and provisions a spare, visible, deletable sub-account - the direction this
    module fails toward everywhere else. `test_identity_key_properties.py` pins
    the open axes as documented behaviour so a future "fix" that closes them by
    reintroducing a collide fails loudly.

    Stored keys changed shape in round 7 (order-preserving replaces sorted).
    Free ONLY because migration 0013 has never run outside local dev - see its
    WARNING before touching this function again.
    """
    if not postcode:
        return ""
    upper = postcode.upper()
    flat = _NON_ALNUM.sub("", upper)
    # RAW only. Round 6 also probed the separator-stripped form, because its
    # SORTED token path keyed "AB 12 CD" differently from "AB12CD" whenever the
    # raw probe missed. Under the round-7 spec that probe went dead: the token
    # path returns the flat string, and a flat-form UK match is boxed in by its
    # own lookarounds to be exactly the whole flat string - the same value the
    # token path returns anyway. The mutation runner proved it: removing the
    # probe survived every test, so it was deleted rather than kept as
    # coverage-shaped dead code. Round 8 then removed the last divergence
    # class too: "B 11 1AA" (separators INSIDE the outward half) used to
    # reject to NULL under the contiguity filler rule; with that rule narrowed
    # to spare real repdigit postcodes, the token path keys it as "B111AA" -
    # exactly what the probe would have extracted. Zero divergence remains.
    # An ordinal-shaped inward half can be EITHER a unit floor-number OR a real
    # postcode - "E8 1ST" and "B33 8TH" (a GOV.UK canonical example) both end in
    # one. Skipping every ordinal match outright (round 9) discarded the ~1% of
    # real UK postcodes shaped that way and broke the town-invariant this probe
    # exists for - a value carrying the town must key the same as the bare
    # postcode (round 10, P1.3). So prefer the first NON-ordinal match, but keep
    # the first ordinal-shaped one and return it only when no non-ordinal match
    # exists: a unit number still loses to the real later postcode, and an
    # ordinal-shaped real postcode with no competitor is no longer thrown away.
    ordinal_fallback = None
    for uk_match in _UK_POSTCODE.finditer(upper):
        if _ORDINAL_INWARD.fullmatch(uk_match.group(2)):
            if ordinal_fallback is None:
                ordinal_fallback = uk_match.group(1) + uk_match.group(2)
            continue
        return uk_match.group(1) + uk_match.group(2)
    if ordinal_fallback is not None:
        return ordinal_fallback
    stripped = flat
    zip_plus_four = _US_ZIP_PLUS_FOUR.match(stripped)
    if zip_plus_four is not None:
        # Reduce, then FALL THROUGH - the reduced value must still face the
        # filler checks (review round 7, P1).
        stripped = zip_plus_four.group(1)
    if len(stripped) < _MIN_POSTCODE_LEN:
        return ""
    if not any(ch.isdigit() for ch in stripped):
        # Every real postal format in use carries at least one digit; a value
        # with none ("TBA", "NONE", "PENDING") is a placeholder by shape.
        return ""
    digits = "".join(ch for ch in stripped if ch.isdigit())
    if digits and len(set(digits)) == 1:
        # A single-repeated-digit block is filler ONLY when the digit is ZERO
        # ("00000", "0000", "00000 ABC"): no postal system issues an all-zero
        # block. A NONZERO single-repeated block is a REAL postcode, whether it
        # is purely numeric (Itegem "2222", Reykjavik "111", Arlington "22222",
        # Rottum "9999") or letter-anchored (Diemen "1111 AB", Valletta
        # "VLT 1111"). Round 8 narrowed the LETTER-bearing branch to spare those
        # but left the purely-numeric branch rejecting the LETTERLESS ones to
        # NULL, silently self-skipping the returning-client check on every
        # signing for those clients (review round 9, P1.2). The two shapes are
        # not distinct any more - all-zero is filler with or without letters,
        # nonzero repdigits are real with or without letters - so the classifier
        # is exactly `digits[0] == "0"`.
        if digits[0] == "0":
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

    CALLER CONTRACT: `digits` is the 7-9 digit tail `normalize_phone` computed
    (its length gate runs first). The old `len(digits) < 2` guard was deleted
    in round 8 - the reviewer's mutation sweep measured zero behavioural
    difference over 30,020 inputs, and a guard no input can reach reads as
    coverage. Behaviour on shorter strings is deliberately unspecified.
    """
    pairs = list(zip(digits, digits[1:], strict=False))
    ascending = all((int(a) + 1) % 10 == int(b) for a, b in pairs)
    descending = all((int(a) - 1) % 10 == int(b) for a, b in pairs)
    return ascending or descending


def _is_repeating_pair(digits: str) -> bool:
    """True when the whole run is one two-digit block repeated ("121212121").

    Filler by SHAPE. Deliberately narrower than "few distinct digits", which
    also matched real landlines whose subscriber number is mostly zeroes
    ("+44 20 7700 0000" -> "077000000"). A repeating pair cannot occur in a
    real allocated number; a low digit count routinely does.

    OWNS the single-repeated-digit case too ("000000000"): a run of one digit
    trivially alternates with itself, so this returns True for it. The
    caller's separate `len(set(tail)) == 1` clause became DEAD the moment
    round 8 deleted this function's `set != 2` gate - the gate's own mutation
    runner caught that interaction (the clause's kill SURVIVED) and the clause
    was deleted per the module's bar. CALLER CONTRACT: `digits` is the 7-9
    digit tail; behaviour on shorter strings is unspecified (the old length
    gate was deleted for zero behavioural difference over 30,020 inputs).
    """
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
    # FIXED (review round 4): "1234567890" is 10 digits, so its 9-digit tail
    # "234567890" slipped past the old denylist, and the most common filler
    # number in existence passed as a corroborating signal.
    # `_is_sequential_digits` catches it (and every rotation) by SHAPE; the
    # denylist it obsoleted is deleted (round 7 - every member was sequential,
    # so the membership check was dead code that could not fail a test).
    if _is_repeating_pair(tail) or _is_sequential_digits(tail):
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

    # Fold + tokenize each part, exactly as `normalize_name` does (review
    # round 8, two findings in one): (a) the placeholder membership was a raw
    # strip+lower denylist - the pattern this module deleted everywhere else -
    # bypassed in the UNSAFE direction by trailing punctuation ("Head",
    # "Office.") CORROBORATED because "head office." missed the set; (b) no
    # unicode fold, so an accented signer whose two documents differ only by
    # an accent ("José" vs "Jose") made bar 3 permanently unsatisfiable - the
    # exact class the fold fixed for business names in round 5.
    def _part(value: str | None) -> str:
        return "".join(_ALNUM_TOKEN.findall(_fold_unicode(value or "").lower()))

    first_norm_a, last_norm_a = _part(first_a), _part(last_a)
    first_norm_b, last_norm_b = _part(first_b), _part(last_b)
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

    This closes round 5's finding 1 ONLY when the two rows carry different
    street addresses - two company records sharing a postcode. It does NOT
    close the one-owner-two-sites case (the module header and migration 0013
    were corrected in rounds 6-7; this sentence was the straggler, round 8):
    one owner with ONE company record and two deals gives two rows identical on
    address as well, every bar clears, and site 2 still auto-links - the gap
    `test_one_company_record_two_deals_still_auto_links` pins as documented
    behaviour until `hubspot_company_id` or `Existing_Client_Identifier` exists
    in the data.

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


def postcode_is_weak_anchor(postcode: str | None) -> bool:
    """True when the normalized postcode is too low-entropy to anchor a keyed
    match on its own.

    Round 9 P1.2 stopped NULL-keying purely-numeric single-repeated-digit blocks
    so real letterless postcodes (Itegem "2222", Reykjavik "111") could key. But
    that same shape is also the classic data-entry filler ("11111", "99999"), and
    a non-NULL key flips `require_contact_name` off on the keyed path - the path
    whose ENTIRE safety argument is that a SHARED postcode is a strong enough
    anchor to make name + phone sufficient. When the shared postcode is a repdigit
    that anchor is fake: two different sites of one brand carrying the same filler
    would auto-MERGE (review round 10, P0.1). So a repdigit key is treated as a
    WEAK anchor and the caller keeps requiring the signer bar (bar 3) for it,
    exactly as the anchorless email fallback does - which still links the genuine
    Itegem / Reykjavik returning client (same signer clears bar 3) while splitting
    two filler-postcode sites that a different person signed for.

    Weak means: the normalized postcode is purely numeric AND every digit is the
    same ("11111", "2222", "111"). A UK postcode is never purely numeric, and any
    postcode with a second distinct digit ("1000", "75008") carries real entropy,
    so both stay STRONG anchors.
    """
    normalized = normalize_postcode(postcode)
    if not normalized:
        return False
    digits = "".join(ch for ch in normalized if ch.isdigit())
    return normalized == digits and len(set(digits)) == 1


def postcodes_materially_diverge(postcode_a: str | None, postcode_b: str | None) -> bool:
    """True ONLY when both postcodes are present and normalize differently.

    A DISQUALIFIER (review round 9, P1.1), the same posture as
    `addresses_materially_diverge`: a differing postcode can only ever REFUSE a
    link, never cause one, so reading it adds a separator with no false-match
    risk, and absence ABSTAINS.

    **Its real scope is narrow - DRIFT, not the general email fallback**
    (corrected review round 10, P1.2). Case analysis of the three sibling
    queries shows bar 5 has exactly one live path:

    - `_SIBLING_BY_IDENTITY_KEY_SQL`: candidates share the client's key, hence
      the same NORMALIZED postcode by construction - normally inert (but see the
      COALESCE caveat below).
    - `_SIBLING_BY_EMAIL_UNKEYED_SQL` (client is keyed): candidates are filtered
      to `identity_key IS NULL`, so bar 5 fires only against a LEGACY pre-0013
      row that carries a populated, divergent `postal_code` but no key (0013 does
      no backfill).
    - `_SIBLING_BY_EMAIL_SQL` (client is unkeyed): the client keys NULL only
      because its name stem is empty (bar 1 then rejects every candidate first)
      or because its own postcode is empty (bar 5 then abstains) - so bar 5 has
      no live path here.

    So the Brussels/Itegem "one shared mailbox, two keyed sites" story is NOT
    what this guards - both of those rows KEY, so they never meet on an email
    query. What bar 5 actually catches is DRIFT: a legacy NULL-keyed row with a
    real postcode, plus the `identity_key = COALESCE(clients.identity_key,
    EXCLUDED.identity_key)` upsert, which can keep an OLD `postal_code` while
    filling a key from EXCLUDED - so even a keyed candidate's stored postcode can
    disagree with its key. That is why "inert on the keyed path" is "normally",
    not "never".

    Comparison is via `normalize_postcode`, the same canonical form the identity
    key uses.
    """
    norm_a = normalize_postcode(postcode_a)
    norm_b = normalize_postcode(postcode_b)
    if not norm_a or not norm_b:
        return False
    return norm_a != norm_b


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
    "postcode_is_weak_anchor",
    "postcodes_materially_diverge",
]
