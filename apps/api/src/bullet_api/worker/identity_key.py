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

import phonenumbers

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
# PUBLIC: ghl_subaccount splits the stored key on this to recover the anchor
# postcode (round 12, P2 - it previously hard-coded a bare "|" across the
# module boundary; if the separator ever changed, `partition` returned ""
# there, the anchor was judged strong, and bar 3 silently switched off on
# every keyed row).
KEY_SEPARATOR = "|"

# Tokenize on runs of WORD characters (any script), so punctuation and
# whitespace both split ("ltd." -> "ltd", "F45  Training" -> "f45",
# "training"). Applied AFTER the unicode fold below, so accented LATIN letters
# are already plain ASCII by this point - and non-Latin letters are KEPT, not
# deleted (round 12, P1.3): the previous ASCII-only class `[a-z0-9]+` deleted
# what the fold could not transliterate, so "Титан Gym" and "Атлант Gym" both
# collapsed to the stem "gym" (bar 1 structurally inert for the whole
# non-Latin cohort) while a fully non-Latin name normalized to "" and
# NULL-keyed on every signing (a systematic silent SPLIT). `[^\W_]` is the
# unicode word class minus underscore; for pure ASCII input it matches exactly
# what `[a-z0-9]+` matched, proven by the Latin-invariance tests.
_ALNUM_TOKEN = re.compile(r"[^\W_]+")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_NON_DIGIT = re.compile(r"[^0-9]")

# US ZIP+4 ("94107-1234") identifies the same delivery area as its ZIP5
# ("94107"), so one business writing each form must not split into two clients.
# The reduction FALLS THROUGH to the filler checks below rather than returning
# (review round 7): returning early let "00000-0000" and "999999999" mint the
# exact placeholder keys ("00000", "99999") the filler checks exist to reject.
_US_ZIP_PLUS_FOUR = re.compile(r"^(\d{5})-?\d{4}$")

# `normalize_phone`'s own OUTPUT is no longer what decides corroboration
# (round 13 moved that to `_phone_interpretations`, real per-country
# structure via `phonenumbers` - see its docstring for why: a tail-suffix
# comparison cannot tell "the same number in two formats" from "a
# coincidence between two different countries' numbers", and this SAME
# constant's own reasoning below - "9 is long enough... not a real
# scenario" - is precisely the class of claim the round-13 audit falsified
# by construction). This tail is now used ONLY as the FILLER pre-filter
# (`corroborating_signal_agrees` checks `normalize_phone(...)` is non-empty
# before ever attempting a real-number reading) and by `normalize_phone`'s
# own direct callers/tests. 9 is long enough that FILLER shapes (repeating
# pairs, sequential runs) are reliably distinguishable from real numbers;
# it is not, on its own, long enough to serve as an identity comparison -
# that job now belongs to `_phone_interpretations`.
_PHONE_SIGNIFICANT_DIGITS = 9
_PHONE_MIN_DIGITS = 7

# Regions tried when a phone number has no "+" and is therefore ambiguous
# about which country it belongs to (round 13, P2/P0 residual close - see
# `_phone_interpretations`). Two groups: markets this module's OWN test
# corpus already establishes as real (UK - the primary market - plus every
# INT postcode example cited elsewhere in this file: France, Belgium, the
# Netherlands, Ireland, Iceland, Malta, Sweden, Canada, the US), and the
# short-national-number cohort `postcode_is_weak_anchor`'s own docstring
# names as a target (Denmark, Norway, Singapore, Luxembourg - all countries
# with 8-digit national numbers, where the fixed 9-digit tail this module
# used to compare ate one digit of the number itself the moment a country
# code was prepended, permanently failing that cohort's own returning
# clients). Deliberately NOT a general-purpose country list: a number from a
# country outside it, written without a "+", will not corroborate - a
# missed link, the safe direction, visible as a spare sub-account rather
# than a silent wrong merge.
_PHONE_CANDIDATE_REGIONS = (
    "GB",
    "FR",
    "BE",
    "NL",
    "IE",
    "IS",
    "MT",
    "SE",
    "CA",
    "US",
    "DK",
    "NO",
    "SG",
    "LU",
)


def _phone_interpretations(phone: str) -> list[tuple[int, int]]:
    """Every plausible (country_code, national_number) reading of `phone`.

    Round 13 replaced the old tail-suffix heuristic here (round 12's
    "9-digit tail plus a zero-stripped full-string suffix check") with real
    per-country numbering-plan structure via `phonenumbers` (the Python port
    of Google's libphonenumber) - the audit that found the old heuristic's
    failure modes also showed neither is closable by tightening the SAME
    heuristic further: a number with no explicit country code is digit-for-
    digit indistinguishable from any other country's number in the same
    shape, which no digit-suffix rule can resolve; only real per-country
    metadata can.

    A "+"-prefixed number is UNAMBIGUOUS - its country code is stated, not
    guessed - and `phonenumbers.parse` uses it regardless of which region is
    passed as a hint (verified by execution: the same "+"-prefixed string
    parses to the identical country_code/national_number under every region
    tried below, so there is no separate unambiguous-parse branch here to
    maintain - a prior version of this function had one, and it was a pure
    optimisation with zero observable effect, i.e. a guard that provably
    cannot fail; removed rather than shipped unpinned). A number with no "+"
    IS ambiguous, so it is tried against every region in
    `_PHONE_CANDIDATE_REGIONS` and every STRUCTURALLY POSSIBLE reading is
    kept. `is_possible_number` (digit-count plausibility for that country),
    not the stricter `is_valid_number` (checked against actually-assigned
    ranges): a genuine client's real number must never be rejected here for
    landing in a range this library's metadata does not yet list as
    assigned - that would trade a merge-direction fix for a new split-
    direction regression. Malformed input raises `NumberParseException` for
    that one region attempt, skipped rather than treated as fatal.
    """
    readings: list[tuple[int, int]] = []
    for region in _PHONE_CANDIDATE_REGIONS:
        try:
            parsed = phonenumbers.parse(phone, region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_possible_number(parsed):
            readings.append((parsed.country_code, parsed.national_number))
    return readings


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
    r"(?<![A-Z0-9])([A-Z]{1,2}[0-9][A-Z0-9]?)[\s,.\-/]*([0-9][A-Z]{2})(?![A-Z0-9])"
)

# The inward half `[0-9][A-Z]{2}` also matches an English ORDINAL ("1ST",
# "2ND", "3RD", "8TH"), and the relaxed separator lets the outward half latch
# onto a preceding unit number, so a unit/floor number can be read as a
# postcode (review round 9, P1.3). An ordinal-shaped inward can ALSO be a real
# postcode ("B33 8TH" is a GOV.UK canonical example), so ordinal-shaped matches
# are AMBIGUOUS candidates - see the candidate rules in `normalize_postcode`.
# `fullmatch` because the inward group is always exactly `[0-9][A-Z]{2}`.
_ORDINAL_INWARD = re.compile(r"[0-9](?:ST|ND|RD|TH)")

# An ordinal immediately followed by an address-STRUCTURE word is a unit floor
# number or a street name, never a postcode. Round 11 guarded only FLOOR|FLR|FL
# and round 12 (P0.1) showed streets were the class that denylist was a shadow
# of: "3rd Avenue" / "2nd Street" survived the floor guard and hijacked the key
# across arbitrary geography. Longest alternatives first so `\b` cannot split a
# longer word ("FLOOR" before "FL", "STREET" before "ST"). Matched from the
# position AFTER the ordinal. Every entry only ever DROPS a candidate, which
# fails toward SPLIT - the module's safe direction - so the list is generous.
_STRUCTURE_AFTER = re.compile(
    r"[\s,.\-/]*(?:FLOOR|FLR|FL|LEVEL|LVL|STREET|ST|AVENUE|AVE|AV|ROAD|RD"
    r"|LANE|LN|BOULEVARD|BLVD|WAY|DRIVE|DR|CLOSE|COURT|CT|PLACE|PL"
    r"|TERRACE|TER|CRESCENT|CRES|GARDENS|GDNS|SQUARE|SQ|PARADE|ROW|WALK"
    r"|GROVE|GREEN|MEWS|HILL|PARK)\b"
)

# An ordinal-shaped candidate whose OUTWARD half is immediately preceded by a
# unit-designator word is a unit number with a trailing ordinal ("Unit B2 1st"),
# not a postcode (review round 12, P0.2 - the trailing mirror of the floor
# case: round 11 flipped first-to-last, which fixed the prefix shape and opened
# the suffix shape, because a positional rule always has a positional mirror).
# Anchored at the END of the text preceding the candidate.
# Round 13 (pure-logic execution audit): the original list was office-generic
# and missed the words THIS agency's own data is full of - "Studio B2, 1st"
# minted the genuine Birmingham client's key. STUDIO/GYM/BAY/POD/KIOSK/CABIN/
# STALL added as defense in depth; `postcode_is_weak_anchor`'s ordinal-shape
# clause is the real, class-level fix (a word we still failed to enumerate
# here is caught there instead), so this list is belt-and-braces, not the
# only line of defence.
_UNIT_BEFORE = re.compile(
    r"\b(?:UNIT|SUITE|STE|APT|APARTMENT|FLAT|ROOM|RM|SHOP|BLOCK|BLDG"
    r"|BUILDING|OFFICE|LOT|NO|NUMBER|STUDIO|GYM|BAY|POD|KIOSK|CABIN|STALL)[\s,.\-/#]*$"
)

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

# Role/department nouns that mark a signing-contact "name" as a JOB TITLE, not
# a person (round 12, P1.1). The previous 12-entry exactly-two-word denylist was
# the enumeration antipattern this module deleted everywhere else - and it was
# bypassed in the UNSAFE direction: ("Club", "Manager") corroborated because
# nobody had enumerated it, and ("Head Office", "Manager") walked past a set
# keyed on exactly two words. A role noun appearing in EITHER part refuses by
# SHAPE, so the bypass class is closed rather than chased entry by entry. A
# real person surnamed one of these is vanishingly rare and the cost of a false
# refusal is a SPLIT (flag, not merge) - the module's safe direction.
_ROLE_NOUNS = frozenset(
    {
        "manager",
        "director",
        "owner",
        "office",
        "desk",
        "admin",
        "administrator",
        "secretary",
        "accounts",
        "account",
        "reception",
        "receptionist",
        "assistant",
        "supervisor",
        "coordinator",
        "department",
        "dept",
        "team",
        "staff",
        "officer",
        "principal",
        "proprietor",
        "founder",
        "partner",
        "chairman",
        "chairwoman",
        "chairperson",
        "president",
        "ceo",
        "cfo",
        "coo",
        "md",
        "gm",
        "hr",
        "payroll",
        # Round 13 (pure-logic execution audit): the original set was
        # office-generic and missed the titles a FITNESS business's own
        # signer fields are full of - ("Head","Coach") corroborated because
        # neither word was enumerated. Same open-class trade as the rest of
        # this set: a title we still fail to enumerate remains a gap, the
        # same documented residual as every other shape-by-enumeration guard
        # in this module.
        "coach",
        "trainer",
        "instructor",
        "lead",
        "rep",
        "sales",
        "duty",
        "membership",
        "pt",
        "personal",
        "fitness",
        "studio",
    }
)

# Placeholder first/last PAIRS that no shape rule can recognise - the
# ("john", "doe") pair is two perfectly name-shaped tokens. Small and closed
# by nature - these are the
# canonical dummy names, not an open class - unlike the role-title set above,
# where the open class is handled by shape.
_PLACEHOLDER_NAME_PAIRS = frozenset(
    {
        ("john", "doe"),
        ("jane", "doe"),
        ("n", "a"),
        ("not", "applicable"),
        ("first", "last"),
        ("firstname", "lastname"),
        ("full", "name"),
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


# Normalized STEMS that mark a "name" as a placeholder, whatever field it
# arrived in and however it was cased or spaced (round 12, P2): the
# unidentifiable-can-never-merge invariant used to be an exact-string compare
# against the one `LEGAL_ENTITY_PLACEHOLDER` constant, so "N/A" keyed as
# `na|...`, "TBC" as `tbc|...`, and a case or trailing-space variant of the
# placeholder itself keyed normally - uniting unidentifiable signings on
# exactly the non-identity the constant exists to refuse. Stems, not raw
# strings, so "n/a", "N.A." and " tbc " all resolve to one member. The
# normalized form of `LEGAL_ENTITY_PLACEHOLDER` is a member for the same
# reason.
_PLACEHOLDER_NAME_STEMS = frozenset(
    {
        "na",
        "tbc",
        "tbd",
        "tba",
        "unknown",
        "none",
        "notapplicable",
        "pending",
        "test",
        "xxx",
        "unknownneedsreview",
    }
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
    for candidate in (business_name, legal_entity):
        if not candidate or not candidate.strip():
            continue
        if candidate == LEGAL_ENTITY_PLACEHOLDER:
            continue
        # Placeholder by SHAPE, not just by the one constant (round 12, P2):
        # a business_name of "N/A" falls through to the legal entity, and a
        # placeholder-shaped legal entity yields None -> fail-safe CREATE.
        if normalize_name(candidate) in _PLACEHOLDER_NAME_STEMS:
            continue
        return candidate
    return None


def normalize_name(name: str | None) -> str:
    """Normalize a business name to a compact alphanumeric identity stem.

    fold accents -> casefold -> tokenize on non-word characters (any script:
    non-Latin letters are part of the identity, not noise - round 12, P1.3) ->
    drop a leading article ("the") -> drop a trailing company suffix
    ("ltd"/"limited") -> join the remaining tokens with no separators. Returns
    "" when nothing usable remains (None, blank, or a name that is only an
    article/suffix).
    """
    if not name:
        return ""
    tokens = _ALNUM_TOKEN.findall(_fold_unicode(name).casefold())
    if tokens and tokens[0] in _LEADING_ARTICLES:
        tokens = tokens[1:]
    if tokens and tokens[-1] in _TRAILING_SUFFIXES:
        tokens = tokens[:-1]
    return "".join(tokens)


def normalize_postcode(postcode: str | None) -> str:
    """Normalize a postcode to a canonical key fragment, or "" if unusable.

    THE SPEC, small enough to state completely, and restated in round 12 to
    match the code exactly (round 12 P1.7 found the previous version of this
    docstring describing a contiguity rule the module no longer had and listing
    "99999" as rejected when it keys - a maintainer "fixing" the code to match
    the stale spec would have re-broken round 9's P1.2; every example below is
    pinned by a test so the doc cannot drift again):

    1. CANDIDATE DISCOVERY: every UK-shaped match (outward+inward) in the RAW
       string is a candidate. A candidate whose inward half is an English
       ordinal ("1ST"/"2ND"/"3RD"/"8TH"...) is AMBIGUOUS - it can be a real
       postcode ("B33 8TH") or a unit/floor/street number - and is DROPPED
       outright when the text around it says which: an address-structure word
       after it ("1st Floor", "3rd Avenue", "2nd Street") or a unit designator
       before it ("Unit B2 1st"). A non-ordinal candidate is REAL.
    2. CANDIDATE SELECTION, failing toward SPLIT whenever position would have
       to guess (round 12 P0.1/P0.2 - the ordinal rule was wrong three ways in
       three rounds precisely because skip-all/keep-first/keep-last are all
       positional guesses with positional mirrors):
         exactly one REAL        -> that candidate ("Unit B2, 1st Floor,
                                    E8 1AA" -> "E81AA"; a REAL candidate also
                                    beats any surviving ordinal, so
                                    "B33 8TH, Head Office N1 4AB" -> "N14AB",
                                    the S1-26f residual, unchanged)
         two or more REAL        -> "" (two genuine postcodes, no way to pick:
                                    "E8 1AA / N1 4AB" -> "")
         zero REAL, one AMBIGUOUS -> that candidate ("B33 8TH" and
                                    "London B33 8TH" -> "B338TH";
                                    "B33 8TH, Unit A1 1st" -> "B338TH")
         zero REAL, two or more  -> "" ("E8 1ST B33 8TH" -> "": position
                                    provably cannot decide between ordinals)
         zero candidates         -> step 3.
    3. Otherwise the key IS the separator-stripped uppercased string, verbatim -
       no sorting, no dropping, no reordering of any kind - with exactly one
       equivalence: a US ZIP+4 reduces to its ZIP5 ("94107-1234" -> "94107",
       one delivery area, one client), and the reduced value still runs the
       filler gauntlet below rather than returning early (review round 7:
       returning early let "00000-0000" mint the exact placeholder key "00000"
       the gauntlet exists to reject).
    4. Filler is rejected by SHAPE, never by denylist: too short
       (`_MIN_POSTCODE_LEN`), no digit at all ("TBA", "NONE", "XXXX"), or a
       single-repeated-digit block whose digit is ZERO ("00000", "0000",
       "00000 ABC") - no postal system issues an all-zero block. A NONZERO
       single-repeated block is a REAL postcode (Itegem "2222", Reykjavik
       "111", Diemen "1111 AB") and KEYS - "99999" keys too; what contains the
       filler risk is `postcode_is_weak_anchor`, which keeps the signer bar
       required for low-entropy digit content rather than NULL-keying real
       codes (rounds 9-12 traded these off explicitly; see that function).

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
    # CANDIDATE RULES (round 12, P0.1/P0.2 - see THE SPEC above). The three
    # previous positional rules (skip-all in round 9, keep-first in round 10,
    # keep-last-with-a-floor-denylist in round 11) each fixed the review's
    # example and broke its mirror, because address grammar puts ordinals both
    # BEFORE the postcode (units, floors) and AFTER it (streets, trailing unit
    # ordinals). So position never decides ordinal-vs-ordinal here: contextual
    # evidence drops what it can prove is not a postcode, a REAL candidate
    # outranks what remains, and any residual tie fails toward SPLIT ("" ->
    # NULL key -> fail-safe CREATE), never toward a guess.
    real_candidates: list[str] = []
    ordinal_candidates: list[str] = []
    for uk_match in _UK_POSTCODE.finditer(upper):
        candidate = uk_match.group(1) + uk_match.group(2)
        if _ORDINAL_INWARD.fullmatch(uk_match.group(2)):
            if _STRUCTURE_AFTER.match(upper, uk_match.end()):
                continue
            if _UNIT_BEFORE.search(upper, 0, uk_match.start()):
                continue
            ordinal_candidates.append(candidate)
        else:
            real_candidates.append(candidate)
    distinct_real = set(real_candidates)
    if len(distinct_real) == 1:
        return real_candidates[0]
    if len(distinct_real) >= 2:
        return ""
    distinct_ordinal = set(ordinal_candidates)
    if len(distinct_ordinal) == 1:
        return ordinal_candidates[0]
    if len(distinct_ordinal) >= 2:
        return ""
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
    # No `digits and` guard (round 12, P3): the no-digit check above already
    # returned, so `digits` cannot be empty here - a condition no input can
    # falsify reads as coverage.
    if len(set(digits)) == 1:
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
    return name_norm[:_NAME_PREFIX_LEN] + KEY_SEPARATOR + postcode_norm


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

    REBUILT ON REAL PHONE-NUMBER STRUCTURE (round 13, closing both residuals
    the pure-logic audit found in the round-12 tail-suffix heuristic). That
    heuristic compared a fixed 9-digit tail, then a zero-stripped full-string
    suffix - both purely digit-shape rules with two failure modes:

    - **Merge-direction**: Malta "+356 2912 3456" vs US "+1 (562) 912-3456"
      shared the 9-digit tail (round 12's own fix: require the fuller
      strings not to CONFLICT); a further construction survived even that -
      Spain "+34 655 512 345" vs Italy "06 5551 2345" (no "+") share a
      digit-identical national significant number, and the length gap left
      by stripping "34" is INDISTINGUISHABLE, by digits alone, from a
      genuine national-prefix relationship. No digit-suffix rule can tell
      these apart, because a number written with no explicit country code
      is digit-for-digit ambiguous about which country it belongs to.
    - **Split-direction**: short-national-number countries (8-digit NSNs -
      Denmark, Norway, Singapore, Luxembourg among them) permanently failed
      to corroborate their own number across a country-code-present vs
      -absent pairing, because the fixed 9-digit tail ate one digit of the
      NSN the moment a country code was prepended, leaving the two forms'
      tails different in both length and content.

    Both are closed the same way: real per-country numbering-plan structure
    via `phonenumbers` (`_phone_interpretations`), not a digit-shape guess.
    A "+"-prefixed number states its own country code, so it is read
    unambiguously; a number with none is tried against a bounded, disclosed
    region list (`_PHONE_CANDIDATE_REGIONS`) and treated as corroborating
    only when the two sides share an EXACT (country_code, national_number)
    reading. This closes Spain/Italy (Italy is not in the region list, so a
    non-"+" Italian-shaped number produces no candidate to coincide with)
    while fixing Denmark/Norway/Singapore/Luxembourg (which are).

    REMAINING, disclosed rather than claimed closed: a country OUTSIDE the
    region list, written with no "+", still fails to corroborate against
    itself - a missed link (safe direction, a spare sub-account). And a
    coincidental agreement between two DIFFERENT real numbers, both possible
    readings under the SAME candidate region, remains theoretically possible
    - inherent to any bar 2 built on a single field with no independent
    verification, the same residual class the original 9-digit tail already
    accepted (two genuinely different numbers colliding is not a real
    scenario) - now true of a specific (country_code, national_number) pair
    rather than of an arbitrary 9-digit run, which is meaningfully narrower,
    not zero.
    """
    if not normalize_phone(phone_a) or not normalize_phone(phone_b):
        # Filler / too-short / absent, exactly as `normalize_phone` already
        # decides (round 4-8's shape checks kept as the pre-filter - a
        # placeholder number must never reach real-number interpretation at
        # all, whatever some country's numbering plan happens to make of it).
        return False
    readings_a = _phone_interpretations(phone_a)
    readings_b = _phone_interpretations(phone_b)
    return any(reading in readings_b for reading in readings_a)


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

    A NON-NAME is refused by SHAPE, not enumeration (round 12, P1.1 - the
    previous two-word denylist was bypassed by ("Club", "Manager"), re-opening
    round 2's franchise conflation through the very field introduced to close
    it). Four shapes refuse, all failing toward SPLIT (a refused bar flags for
    a human rather than merging):

    - a ROLE NOUN in either part ("Club Manager", "Managing Director",
      "Business Owner", "Front Desk") - a job title is what gets typed when
      the signer is a function, not a person, and two sites sharing a function
      title is no evidence they share a signer;
    - first part == last part ("N/A N/A", "Unknown Unknown", "TBC TBC") - a
      real person's given and family name coinciding is possible but rare, and
      the echo shape is overwhelmingly placeholder;
    - a part carrying two or more tokens ("Head Office" | "Manager") - a
      department-shaped part is not a personal name part. The accepted cost:
      a double-barrelled given name ("Mary Jane") also refuses, which only
      keeps the bar unsatisfied and splits - the safe direction;
    - a known placeholder PAIR (john/doe and friends) that is name-shaped
      by construction and only recognisable by membership.
    """

    # Fold + tokenize each part, exactly as `normalize_name` does (review
    # round 8, two findings in one): (a) the placeholder membership was a raw
    # strip+lower denylist - the pattern this module deleted everywhere else -
    # bypassed in the UNSAFE direction by trailing punctuation ("Head",
    # "Office.") CORROBORATED because "head office." missed the set; (b) no
    # unicode fold, so an accented signer whose two documents differ only by
    # an accent ("José" vs "Jose") made bar 3 permanently unsatisfiable - the
    # exact class the fold fixed for business names in round 5.
    def _part_tokens(value: str | None) -> list[str]:
        return _ALNUM_TOKEN.findall(_fold_unicode(value or "").casefold())

    first_tokens_a, last_tokens_a = _part_tokens(first_a), _part_tokens(last_a)
    first_tokens_b, last_tokens_b = _part_tokens(first_b), _part_tokens(last_b)
    if not (first_tokens_a and last_tokens_a and first_tokens_b and last_tokens_b):
        return False
    # The shape checks run on BOTH sides (round 13: side-A-only was a real
    # bug, not just an inefficiency). The final equality check compares the
    # JOINED (no-separator) forms, so two sides that TOKENIZE differently can
    # still join to the same string: "ClubManager" fused on side A is ONE
    # token ("clubmanager") that matches neither the role-noun set nor the
    # multi-token rule, while "Club Manager" on side B is caught by both -
    # yet both join to "clubmanager" and the old side-A-only check let the
    # pair through. Checking both sides also fixes the ARGUMENT-ORDER
    # dependence that bug produced (agrees(a, a, b, b) != agrees(b, b, a, a)
    # for exactly this shape), which a symmetric corroboration signal must
    # never exhibit.
    both_sides = ((first_tokens_a, last_tokens_a), (first_tokens_b, last_tokens_b))
    for first_tokens, last_tokens in both_sides:
        if any(token in _ROLE_NOUNS for token in first_tokens + last_tokens):
            return False
        if len(first_tokens) >= 2 or len(last_tokens) >= 2:
            return False
    first_norm_a, last_norm_a = "".join(first_tokens_a), "".join(last_tokens_a)
    if first_norm_a == last_norm_a:
        return False
    if (first_norm_a, last_norm_a) in _PLACEHOLDER_NAME_PAIRS:
        return False
    first_norm_b, last_norm_b = "".join(first_tokens_b), "".join(last_tokens_b)
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


def _normalize_address(value: str | None) -> str:
    """Address normal form: folded, casefolded tokens joined by ONE SPACE.

    NOT `normalize_name` (round 12, P2): the name form concatenates tokens
    with no separator, so boundary shifts erased real differences -
    "Unit 1, 23 Mill Road" and "Unit 12, 3 Mill Road" both gave
    "unit123millroad" and bar 4 abstained; chained with an ordinal-hijacked
    key that removed the LAST surviving bar. Keeping one space per boundary
    makes those two distinct while "12 Mare Street,  London" still equals
    "12 Mare Street, London". The name form's article/suffix drops do not
    apply - "the"/"ltd" are name noise, not address noise.
    """
    if not value:
        return ""
    return " ".join(_ALNUM_TOKEN.findall(_fold_unicode(value).casefold()))


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
    stem_a = _normalize_address(address_a)
    stem_b = _normalize_address(address_b)
    if not stem_a or not stem_b:
        return False
    return stem_a != stem_b


def _digits_are_low_entropy(digits: str) -> bool:
    """True when a digit block has the shape of data-entry filler.

    Filler is a PATTERN property of the digit content, judged as a union of
    shapes rather than one regex (round 12, P1.2: the previous leading-run
    regex modelled exactly one shape and 23 of the reviewer's 28 obvious
    placeholder ZIPs classified STRONG): a constant step covers repdigits
    ("11111"), keypad runs in both directions ("12345", "98765") and strided
    runs ("13579"); a repeating pair covers "1212"; a run of three-plus of one
    digit covers "10000" / "11112" / "00001"; two or fewer distinct digits over
    four-plus positions covers "1122"-style filler; doubled-run structure
    covers "11223"; a palindrome of four-plus covers "12321". Real codes have
    none of these shapes ("75008", "60601", "D02X285"). Over-matching is the
    SAFE direction - the only consumer keeps bar 3 REQUIRED for a weak anchor.
    """
    diffs = {(int(b) - int(a)) % 10 for a, b in zip(digits, digits[1:], strict=False)}
    if len(diffs) == 1:
        return True
    if _is_repeating_pair(digits):
        return True
    if len(digits) >= 4 and len(set(digits)) <= 2:
        return True
    if re.search(r"(\d)\1\1", digits):
        return True
    if len(digits) >= 4 and digits == digits[::-1]:
        return True
    # Doubled-run structure ("11223" -> runs 11,22,3). Four-plus digits only:
    # at three digits a single doubled digit plus a tail is ordinary real-code
    # content ("B33 8TH" -> "338"), not a filler pattern.
    if len(digits) >= 4:
        runs = [len(run.group(0)) for run in re.finditer(r"(\d)\1*", digits)]
        if len(runs) >= 2 and all(length >= 2 for length in runs[:-1]):
            return True
    return False


def postcode_is_weak_anchor(postcode: str | None) -> bool:
    """True when the normalized postcode is too low-entropy to anchor a keyed
    match on its own.

    Round 9 P1.2 stopped NULL-keying purely-numeric single-repeated-digit blocks
    so real letterless postcodes (Itegem "2222", Reykjavik "111") could key. But
    that same shape is also classic data-entry filler ("11111", "99999"), and
    a non-NULL key flips `require_contact_name` off on the keyed path - the path
    whose ENTIRE safety argument is that a SHARED postcode is a strong enough
    anchor to make name + phone sufficient. When the shared postcode is filler
    that anchor is fake: two different sites of one brand carrying the same
    filler would auto-MERGE (review round 10, P0.1). So a low-entropy key is a
    WEAK anchor and the caller keeps requiring the signer bar (bar 3) for it.

    Weak means: the DIGIT CONTENT of the normalized value is filler-shaped
    (`_digits_are_low_entropy`) and at least three digits long. Classifying on
    digit content - the reviewer's actual round-11 suggestion, which round 11
    implemented as a weaker leading-anchor regex instead (round 12, P1.2) -
    makes the gate position-independent: "11111", "11111 USA", "USA 11111",
    "PO Box 11111" and "Head Office 99999" are all weak, because WHERE the
    filler sits in the field says nothing about whether it is filler. Real
    postcodes stay strong two ways: interleaved alpha-digit structure keeps the
    digit block under three ("E1 1EE" -> "11", "SW1A 1AA" -> "11"), and real
    numeric codes have high-entropy digit content ("75008", "60601"). Letterless
    real codes that share filler's shape (Itegem "2222", Reykjavik "111",
    Brussels "1000", Stockholm "111 11") classify weak in the SAFE direction -
    it only keeps bar 3 ON for them.

    OR (round 13, closing the class the adversarial-execution audit found):
    the value is an ORDINAL-SHAPED extraction. `normalize_postcode`'s own
    candidate rules (`_UNIT_BEFORE`/`_STRUCTURE_AFTER`) can only drop a unit
    or floor word we thought to enumerate - "Studio B2, 1st" mints the exact
    key of the genuine Birmingham client at "B2 1ST" with a STRONG anchor,
    because STUDIO was never on the office-word list `_UNIT_BEFORE` carries.
    Chasing every gym-industry unit word one at a time repeats round 9-12's
    own mistake (patching the instance, not the class). The class fix: a key
    whose UK-postcode shape has an ORDINAL inward half ("...1ST", "...8TH")
    is, by this module's OWN candidate-selection rules, an AMBIGUOUS
    extraction - it survived only because nothing recognisable disqualified
    it, not because it was confirmed to be a postcode. An ambiguous anchor
    cannot certify a merge on its own, so it is WEAK unconditionally, whatever
    its digit content. The cost is real but bounded and documented: genuine
    UK postcodes shaped like an ordinal ("B33 8TH", ~1% of the format) now
    also require the signer bar - the same "keeps a real client's key weak in
    the safe direction" trade every other clause here makes.

    CAVEAT the caller must honour: keeping bar 3 on assumes bar 3 is SATISFIABLE,
    i.e. the signing contact's first AND last name are populated. Those come from
    the UK PandaDoc template tokens (`clients_payload._TOKEN_CLIENT_FIRST_NAME`
    /`_LAST_NAME`); the International template's token names are NOT confirmed
    (`clients_payload.py` says so). The letterless-repdigit cohort (Itegem,
    Reykjavik, Rottum) IS the non-UK cohort, so if the INT template omits those
    tokens, a returning client here would split-and-flag every time rather than
    link. Fail-safe (SPLIT, not MERGE), but worth confirming against a real INT
    document before relying on the returning-client link for that cohort.

    THIS CAVEAT IS BIGGER THAN THE REPDIGIT COHORT (round 13, pure-logic
    execution audit) - `_digits_are_low_entropy`'s constant-step/doubled-run
    rules also classify weak a wide band of ordinary big-city INT postal
    codes (FR 75000/13000/69000, IT 00100, AU 2000/3000, BE 1000, the
    `dNNN00`/`d0100` city-generic shapes generally). That is NOT a bug in
    the classifier - a data-entry-filler-shaped code is exactly what a large
    fraction of a country's biggest cities happen to have - but it means
    this satisfiability caveat is load-bearing for a much larger share of
    the INT cohort than "the letterless-repdigit clients" suggests. BLOCKS
    ON THE SAME OUTSTANDING CLIENT ASK: confirm the INT template's signer-
    name tokens against a real signed document before treating the
    returning-client link as reliable for ANY INT client, not just the
    repdigit-postcode ones. Until then the fail-safe direction (split, flag,
    never merge) holds regardless.
    """
    normalized = normalize_postcode(postcode)
    if not normalized:
        return False
    uk_match = _UK_POSTCODE.fullmatch(normalized)
    if uk_match is not None and _ORDINAL_INWARD.fullmatch(uk_match.group(2)):
        return True
    digits = "".join(ch for ch in normalized if ch.isdigit())
    if len(digits) < 3:
        return False
    return _digits_are_low_entropy(digits)


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
    "KEY_SEPARATOR",
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
