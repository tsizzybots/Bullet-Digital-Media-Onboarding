"""Property tests for the identity-key normalizers.

WHY THIS FILE EXISTS. `normalize_postcode` produced a blocking review finding in
FOUR consecutive rounds (4, 5, 6, 7), and each fix traded one axis for another:

    round 4  sort tokens            -> order fixed, SEPARATOR broken
    round 5  drop alphabetic tokens -> separator fixed, "1011 AB" == "1011 CD"
                                       COLLIDE added
    round 6  sort canonical runs    -> both fixed, "K1A 0B1" == "B1A 0K1"
                                       anagram COLLIDE added
    round 7  (this file's rewrite)  -> the trading stops, because the trade is
                                       now a THEOREM, not a judgement call

The theorem (proved mechanically, see `normalize_postcode`'s docstring):
separator-independence + order-independence + injectivity are JOINTLY
UNSATISFIABLE for alternating alpha/digit postcode shapes. Injectivity is
non-negotiable - its failure is a false MERGE - so order-independence is
deliberately open, and `TestOrderAxisIsDeliberatelyOpen` pins that as
documented behaviour. Any future change that restores it MUST reintroduce a
collision and will fail `TestPostcodeInjectivity` loudly.

THE ROUND-7 LESSON ON ORACLES. The first version of this file compared keys
against `"".join(sorted(alnum))` - the implementation's own transformation - so
it asserted the normalizer was injective UP TO SORTING, which is trivially true
of a sorting normalizer. A property test whose oracle is derived from the
implementation is a tautology. Every oracle below is the SPEC (the flat ordered
string and its two documented equivalences), never the implementation's
transform, and the generators emit alternating shapes that CAN anagram - the
round-6 generator's `{digits} {letters}` values could not.
"""

from __future__ import annotations

import random
import re

import pytest

from bullet_api.worker.identity_key import compute_identity_key, normalize_postcode

# Separator forms a human actually types between parts of a postcode.
SEPARATORS = ["", " ", "  ", "-", ",", ", ", ".", " - "]

# Real postal shapes, written WITHOUT separators so each test can re-insert
# every separator form and assert the key never moves. UK, NL, CH, MT, CA, IE,
# DE, US, AU - and the postcode-plus-town form.
POSTCODE_CORES = [
    "E81AA",
    "SW1A1AA",
    "M11AE",
    "1011AB",
    "CH8001",
    "VLT1117",
    "K1A0B1",
    "D02X285",
    "75008PARIS",
    "2000AB",
    "AB12CD",
    "10115",
    "94107",
    # Repdigit reals (round 8): their ABSENCE from this list is what hid the
    # contiguity false-reject - "1111AB" fails separator-independence on
    # `keys != {""}` the moment the rule over-rejects, and "B111AA" fails it on
    # `len(keys) == 1` if the UK/token paths ever disagree again.
    "1111AB",
    "B111AA",
]

_RUN = re.compile(r"[A-Z]+|[0-9]+")
_UK_SHAPE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{1,2}[0-9][A-Z0-9]?)[\s,.\-]*([0-9][A-Z]{2})(?![A-Z0-9])"
)


def _parts(core: str) -> list[str]:
    return _RUN.findall(core)


def _flat(value: str) -> str:
    """The INDEPENDENT oracle: uppercase alphanumerics in ORIGINAL order.

    No sorting, no dropping - deliberately shares no transformation with any
    implementation this file has ever had to catch.
    """
    return re.sub(r"[^A-Z0-9]", "", value.upper())


# Single-token cores have no separator boundary; FILTERED at collection time
# rather than skipped at run time - review round 7 noted the skips made the
# project's "0 skipped" verification bar unreachable.
MULTI_TOKEN_CORES = [c for c in POSTCODE_CORES if len(_parts(c)) >= 2]


class TestPostcodeSeparatorIndependence:
    """INVARIANT: separators carry no meaning.

    Broken in rounds 4, 5 AND 6. One business writing "1011 AB" on one document
    and "1011AB" on the next must reach ONE key, or the returning-client check
    finds no candidate and silently provisions a duplicate sub-account.
    """

    @pytest.mark.parametrize("core", MULTI_TOKEN_CORES)
    def test_every_separator_form_of_one_postcode_shares_one_key(self, core: str) -> None:
        parts = _parts(core)
        keys = {normalize_postcode(sep.join(parts)) for sep in SEPARATORS}
        assert len(keys) == 1, (
            f"{core!r} keys {len(keys)} ways across separators: "
            f"{ {sep.join(parts): normalize_postcode(sep.join(parts)) for sep in SEPARATORS} }"
        )
        assert keys != {""}


class TestTokenPathIsTheFlatString:
    """THE SPEC, asserted over generated token lists.

    Round 7's prescription verbatim: generate the token LIST - count, order,
    kind and separator all varying - rather than re-spelling one value. On the
    non-UK path the key must be exactly the flat ordered string, with two
    documented equivalences (a US ZIP+4 reduces to its ZIP5; filler shapes
    reject to ""). Any sort, drop, or reorder an implementation sneaks in
    fails this against the independent oracle immediately.
    """

    def test_generated_token_lists_key_as_their_flat_form(self) -> None:
        rng = random.Random(20260827)
        letters = "ABCEGHJKLMNPRSTVWXYZ"
        checked = 0
        for _ in range(8000):
            count = rng.randint(1, 4)
            tokens = []
            for _i in range(count):
                if rng.random() < 0.5:
                    tokens.append("".join(rng.choice(letters) for _ in range(rng.randint(1, 6))))
                else:
                    tokens.append(
                        "".join(rng.choice("0123456789") for _ in range(rng.randint(1, 5)))
                    )
            value = ""
            for i, token in enumerate(tokens):
                value += (rng.choice(SEPARATORS) if i else "") + token
            flat = _flat(value)
            # The UK extractor is a different code path with its own tests; a
            # raw/flat probe mismatch there is out of this property's scope.
            if _UK_SHAPE.search(value.upper()) or _UK_SHAPE.search(flat):
                continue
            key = normalize_postcode(value)
            digits = "".join(ch for ch in flat if ch.isdigit())
            if key == "":
                # A rejection must be one of the three documented filler
                # shapes - never a silent loss of a usable value.
                assert (
                    len(flat) < 3
                    or not any(ch.isdigit() for ch in flat)
                    or (len(set(digits)) == 1 and (flat == digits or digits.startswith("0")))
                ), f"{value!r} rejected without matching any documented filler shape"
            elif re.fullmatch(r"\d{5}-?\d{4}", flat) or re.fullmatch(r"\d{9}", flat):
                assert key == flat[:5], f"{value!r}: ZIP+4 must reduce to its ZIP5"
            else:
                assert key == flat, (
                    f"{value!r}: key {key!r} != flat {flat!r} - the token path "
                    f"reordered, dropped, or invented characters"
                )
            checked += 1
        assert checked > 6000  # the property genuinely ran, not filtered away


class TestPostcodeInjectivity:
    """INVARIANT: two different postcodes never share a key.

    The expensive direction. Round 5 broke it by dropping ("1011 AB" ==
    "1011 CD"), round 6 by sorting ("K1A 0B1" == "B1A 0K1" - 633 collisions in
    20,000 generated Canadian postcodes). A collide can put one client's assets
    inside another client's sub-account; a split only costs a spare, visible,
    deletable one.
    """

    @pytest.mark.parametrize(
        ("shape", "make"),
        [
            (
                "CA L#L #L#",
                lambda r, L: (
                    f"{r.choice(L)}{r.randrange(10)}{r.choice(L)} "
                    f"{r.randrange(10)}{r.choice(L)}{r.randrange(10)}"
                ),
            ),
            ("NL #### LL", lambda r, L: f"{r.randrange(1000, 9999)} {r.choice(L)}{r.choice(L)}"),
            (
                "IE A## A##A",
                lambda r, L: (
                    f"{r.choice(L)}{r.randrange(10)}{r.randrange(10)} "
                    f"{r.choice(L)}{r.randrange(10)}{r.randrange(10)}{r.choice(L)}"
                ),
            ),
        ],
        ids=["CA", "NL", "IE"],
    )
    def test_generated_alternating_postcodes_never_collide(self, shape: str, make) -> None:
        rng = random.Random(77)
        letters = "ABCEGHJKLMNPRSTVWXYZ"
        seen: dict[str, str] = {}
        collisions = []
        for _ in range(20000):
            value = make(rng, letters)
            key = normalize_postcode(value)
            if not key:
                continue
            oracle = _flat(value)
            if key in seen and seen[key] != oracle:
                collisions.append((value, seen[key], oracle))
            seen[key] = oracle
        assert not collisions, f"{shape}: distinct postcodes sharing a key: {collisions[:5]}"

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            # Round 7's named anagram pairs - real Canadian postcodes.
            ("K1A 0B1", "B1A 0K1"),
            ("J5B 1J2", "J5B 2J1"),
            ("H9T 4X6", "H6T 4X9"),
            # Round 5's named drop-collide pair - real Amsterdam postcodes.
            ("1011 AB", "1011 CD"),
            ("VLT 1117", "VLT 1234"),
            ("75008 Paris", "75009 Paris"),
        ],
    )
    def test_named_near_misses_stay_apart(self, a: str, b: str) -> None:
        assert normalize_postcode(a) != normalize_postcode(b)

    def test_the_anagram_pair_keys_to_its_own_flat_form(self) -> None:
        # Pins BOTH sides of the docstring's anagram example: order-preserving
        # keys are the flat strings themselves, so the anagrams cannot meet.
        assert normalize_postcode("K1A 0B1") == "K1A0B1"
        assert normalize_postcode("B1A 0K1") == "B1A0K1"


class TestOrderAxisIsDeliberatelyOpen:
    """NOT a bug: token order is meaningful, by theorem.

    Separator-independence + order-independence + injectivity are jointly
    unsatisfiable (see `normalize_postcode`'s docstring for the three-line
    proof). Injectivity's failure is a false MERGE, so order-independence is
    the axis that goes - its failure is a SPLIT: the second signing finds no
    candidate and provisions a spare, visible, deletable sub-account, the
    direction this module fails toward everywhere.

    These assert the split lands SAFELY (two distinct non-empty keys, never a
    collide, never a rejection). If someone "fixes" one of these pairs into
    matching, `TestPostcodeInjectivity` is where the collide they just created
    shows up. NOTE: ("75008 Paris", "Paris-75008") was a round-6 REQUIRED
    match pair - the theorem shows the round-6 requirement set was
    unsatisfiable, which is why satisfying it produced the anagram collide.
    """

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("75008 Paris", "Paris 75008"),  # order axis
            ("75008 Paris", "Paris-75008"),  # order axis, round-6's own pair
            ("75008", "75008 Paris"),  # attached-town axis
            ("8001", "CH-8001"),  # attached-country axis
            ("1011 AB", "1011 AB Amsterdam"),  # attached-town axis
        ],
    )
    def test_the_split_is_safe_never_a_collide(self, a: str, b: str) -> None:
        ka, kb = normalize_postcode(a), normalize_postcode(b)
        assert ka != kb, "matching these requires a lossy transform - see the theorem"
        assert ka != "" and kb != "", "the open axis must split, never silently reject"


class TestPostcodeRoundSevenCases:
    """The exact values review round 7 reported, pinned by name."""

    @pytest.mark.parametrize("value", ["00000-0000", "00000 ABC"])
    def test_zip_plus_four_filler_is_rejected(self, value: str) -> None:
        # The ZIP+4 reduction must FALL THROUGH to the filler checks, not return
        # early: "00000-0000" reduces to "00000", which the all-zero check must
        # still catch. (Round 9, P1.2: the old params "999999999"/"111111111"
        # reduced to REAL repdigit ZIPs "99999"/"11111", now covered by
        # test_zip_plus_four_reduces_a_real_repdigit_zip, so only the all-zero
        # cases remain filler.)
        assert normalize_postcode(value) == ""

    @pytest.mark.parametrize(
        ("value", "expected"), [("999999999", "99999"), ("111111111", "11111")]
    )
    def test_zip_plus_four_reduces_a_real_repdigit_zip(self, value: str, expected: str) -> None:
        # A ZIP+4 whose 5-digit ZIP is a NONZERO repdigit ("99999" = Ketchikan,
        # AK) reduces to a REAL postcode, not filler - it falls through the
        # filler checks and survives (round 9, P1.2).
        assert normalize_postcode(value) == expected

    def test_real_alternating_postcode_with_repeated_digits_survives(self) -> None:
        # Ottawa's K1K 1K1: a latent false-reject in rounds 5-6, found while
        # specifying the round-7 rejection rules. Its digit content really is
        # the "111" the source comment cites - computed, not asserted by prose.
        assert "".join(ch for ch in "K1K1K1" if ch.isdigit()) == "111"
        assert normalize_postcode("K1K 1K1") == "K1K1K1"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1111 AB", "1111AB"),  # Diemen, NL - round 8: was NULL-keyed
            ("2222 XX", "2222XX"),
            ("7777 DS", "7777DS"),
            ("9999 VF", "9999VF"),
            ("VLT 1111", "VLT1111"),  # Valletta, MT
        ],
    )
    def test_real_repdigit_postcodes_survive(self, value: str, expected: str) -> None:
        """Round 8: the contiguity rule rejected these REAL postcodes to NULL,
        silently self-skipping the returning-client check for those INT
        clients. Repdigit-beside-letters is filler only when the digits are all
        ZERO ("00000 ABC") - no postal system issues an all-zero block."""
        assert normalize_postcode(value) == expected

    @pytest.mark.parametrize("value", ["00000 ABC", "0000 AB", "00 XX 00"])
    def test_all_zero_digits_beside_letters_still_reject(self, value: str) -> None:
        assert normalize_postcode(value) == ""

    def test_uk_code_with_internal_separators_keys_identically_anyway(self) -> None:
        """The round-7 residual CLOSED ITSELF under the round-8 repdigit rule.

        "B 11 1AA" - separators INSIDE the outward half - misses the raw UK
        probe, but the token path returns the flat string "B111AA", which is
        exactly what the probe extracts from the standard spellings. Round 7
        pinned this as a fail-safe NULL (the old contiguity rule rejected the
        "111" digit run); round 8 narrowed that rule to spare real repdigit
        postcodes, and the narrowing removed the residual entirely: all three
        spellings now share one key with no probe needed.
        """
        assert (
            normalize_postcode("B 11 1AA")
            == normalize_postcode("B11 1AA")
            == normalize_postcode("B111AA")
            == "B111AA"
        )

    def test_zip5_and_zip_plus_four_are_one_business(self) -> None:
        assert normalize_postcode("94107") == normalize_postcode("94107-1234") == "94107"


class TestPostcodeRoundSixCasesStillClosed:
    """Round 6's SEPARATOR pairs, still matched (the order pair moved to
    `TestOrderAxisIsDeliberatelyOpen` - see the theorem note there)."""

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("1011 AB", "1011AB"),
            ("CH 8001", "CH-8001"),
            ("CH 8001", "CH8001"),
            ("VLT 1117", "VLT1117"),
            ("K1A 0B1", "K1A0B1"),
            ("94107", "94107-1234"),
            ("E8 1AA", "E8, 1AA"),
            ("E8 1AA", "E8-1AA"),
            ("E8 1AA", "Flat 2, E8, 1AA"),
            ("AB12CD", "AB 12 CD"),
        ],
    )
    def test_separator_pairs_share_one_key(self, a: str, b: str) -> None:
        assert normalize_postcode(a) == normalize_postcode(b) != ""

    @pytest.mark.parametrize("form", ["1011 AB", "1011AB", "1011-AB", "1011, AB", "1011.AB"])
    def test_every_written_form_of_one_nl_postcode_agrees(self, form: str) -> None:
        assert normalize_postcode(form) == normalize_postcode("1011 AB") != ""

    def test_zip_plus_four_does_not_concatenate_into_a_nine_digit_key(self) -> None:
        """Round 4 produced "123494107" here and round 5 "941071234" - both
        nine-digit concatenations that split a US business from its own ZIP5."""
        key = normalize_postcode("94107-1234")
        assert key == normalize_postcode("94107") == "94107"
        assert key not in {"941071234", "123494107"}


class TestIdentityKeyInheritsTheInvariants:
    """The key is what actually gates the merge, so assert at that level too."""

    @pytest.mark.parametrize("core", MULTI_TOKEN_CORES)
    def test_one_business_reaches_one_key_across_separator_forms(self, core: str) -> None:
        parts = _parts(core)
        keys = {compute_identity_key("Sample Gym", sep.join(parts)) for sep in SEPARATORS}
        assert len(keys) == 1, f"{core!r} produced {len(keys)} identity keys: {keys}"
        assert None not in keys

    def test_anagram_postcodes_produce_different_identity_keys(self) -> None:
        # The end-to-end consequence of injectivity: two same-brand sites at
        # anagram postcodes must NOT land on one key, or bar 1 passes on the
        # brand and a matching head-office phone auto-links them.
        assert compute_identity_key("Brand Gym", "K1A 0B1") != compute_identity_key(
            "Brand Gym", "B1A 0K1"
        )
