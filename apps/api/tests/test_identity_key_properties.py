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

import json
import pathlib
import random
import re

import pytest

from bullet_api.worker.identity_key import (
    _ORDINAL_INWARD,
    PostcodeConfidence,
    _digits_are_low_entropy,
    _is_recognised_format,
    classify_postcode,
    compute_identity_key,
    identity_name,
    normalize_postcode,
    postcode_is_weak_anchor,
)

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
                # shapes - never a silent loss of a usable value. The old
                # `flat == digits` disjunct is GONE (round 10): P1.2 stopped
                # rejecting purely-numeric non-zero repdigits, so the only
                # single-distinct-digit rejection left is an ALL-ZERO block.
                assert (
                    len(flat) < 3
                    or not any(ch.isdigit() for ch in flat)
                    or (len(set(digits)) == 1 and digits.startswith("0"))
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


class TestStrongIsAnAllowlist:
    """Round 14: the property that ends rounds 9-13, stated as an IMPLICATION.

    Every previous round asserted EXAMPLES of what must be weak, so each fix
    was only as good as the examples whoever wrote it happened to think of -
    and the reviewer then supplied the mirror the author had not thought of.
    Five rounds, five mirrors.

    This asserts the DIRECTION instead: STRONG implies positively-recognised.
    Any future path that yields a strong anchor without a format match fails
    here, whatever input shape reaches it, whether or not anybody thought of
    that shape in advance. That is the difference between a test that pins the
    last bug and a test that closes the class.

    The converse is deliberately NOT asserted: a REAL value may still be weak
    (an ordinal-shaped code, or filler digit content that fullmatches ZIP5),
    which is the safe direction.
    """

    def _corpus(self) -> list[str]:
        """Postcode-shaped and NOT-postcode-shaped inputs, deterministically.

        The not-postcode half is the point. Round 13's P0.2 lived entirely in
        values no postcode generator would ever emit ("Unit 3", "1st Floor",
        "TBC 1"), because the old classifier only asked "does this look like
        filler", never "is this a postcode at all".
        """
        rng = random.Random(20260907)
        corpus: list[str] = []
        for core in POSTCODE_CORES:
            for separator in SEPARATORS:
                parts = _parts(core)
                corpus.append(separator.join(parts))
        unit_words = [
            "Unit",
            "Suite",
            "Studio",
            "Gym",
            "Bay",
            "Pod",
            "Kiosk",
            "Cabin",
            "Stall",
            "Pitch",
            "Annexe",
            "Wing",
            "Zone",
            "Container",
            "Berth",
            "Arena",
            "Court",
            "Gate",
            "Block",
            "Floor",
            "Room",
            "Shop",
            "Office",
            "Lot",
        ]
        structure_words = ["Floor", "Street", "Avenue", "Road", "Lane", "Way", "Rise", "Park"]
        ordinals = ["1st", "2nd", "3rd", "4th", "8th", "21st"]
        outwards = ["B2", "E8", "C1", "C3", "N1", "EC1V", "SW1A", "B33", "Q9", "I4"]
        inwards = ["1AA", "9BE", "2AM", "2PM", "1ST", "8TH", "0KZ", "3CV"]
        placeholders = [
            "Unit 3",
            "1st Floor",
            "Suite 100",
            "Floor 2",
            "PO Box 1",
            "TBC 1",
            "n/a 1",
            "Head Office 1",
            "EC1V",
            "TBA",
            "N/A",
            "Unknown",
            "11111",
            "00000",
            "99999",
            "-",
            "0",
            "1",
            "12",
            "123",
            "1234",
            "12345",
            "123456",
            "1234567",
        ]
        # Round 15, P0: bare digit runs at every length the removed
        # `[0-9]{3,6}` wildcard used to admit, plus the lengths either side of
        # it, plus the "#"-prefixed and separator-bearing forms a HubSpot
        # `Company.Zip` actually arrives in, plus the reviewer's own exhibits.
        # These are the inputs that certified themselves as a published format
        # while being a street number, a year or a dialling code.
        numeric_shapes = [
            "1",
            "12",
            "182",
            "2026",
            "0161",
            "75008",
            "606010",
            "1234567",
            "12345678",
            "123456789",
            "1234567890",
            "#182",
            "# 182",
            "No. 182",
            "182-184",
            "182 ",
            " 182",
        ]
        placeholders.extend(numeric_shapes)
        corpus.extend(placeholders)
        for _ in range(6000):
            shape = rng.randrange(6)
            if shape == 0:
                corpus.append(
                    f"{rng.choice(unit_words)} {rng.choice(outwards)}, {rng.choice(ordinals)}"
                )
            elif shape == 1:
                corpus.append(
                    f"{rng.choice(outwards)} {rng.choice(ordinals)} {rng.choice(structure_words)}"
                )
            elif shape == 2:
                corpus.append(f"{rng.choice(outwards)} {rng.choice(inwards)}")
            elif shape == 3:
                corpus.append(
                    f"{rng.choice(unit_words)} {rng.choice(outwards)} {rng.choice(inwards)}"
                )
            elif shape == 4:
                corpus.append(str(rng.randrange(0, 10 ** rng.randrange(1, 7))))
            else:
                corpus.append(
                    f"{rng.choice(outwards)} {rng.choice(inwards)}, "
                    f"{rng.choice(outwards)} {rng.choice(inwards)}"
                )
        return corpus

    def test_strong_implies_a_positively_recognised_format(self) -> None:
        offenders: list[tuple[str, str]] = []
        for raw in self._corpus():
            result = classify_postcode(raw)
            if not result.value:
                # An EMPTY value is not a weak anchor and not a strong one - it
                # is not an anchor at all, and the question does not apply. That
                # is not a loophole: see
                # `test_an_empty_value_can_never_anchor_anything` below, which
                # proves an empty value always NULLs the key, so such a row
                # takes the unkeyed path (bar 3 required in its own right) and
                # the GHL leg routes it through its own `unknown` branch to
                # UNDECIDABLE. Filtering here without that proof would be
                # exactly the "loosen the test until it passes" move this file
                # exists to prevent.
                continue
            if postcode_is_weak_anchor(raw):
                continue
            if result.confidence is not PostcodeConfidence.REAL or not _is_recognised_format(
                result.value
            ):
                offenders.append((raw, result.value))
        assert offenders == [], f"STRONG without a recognised format: {offenders[:10]}"

    def test_a_strong_anchor_is_never_ordinal_shaped_or_low_entropy(self) -> None:
        offenders: list[tuple[str, str]] = []
        for raw in self._corpus():
            if postcode_is_weak_anchor(raw):
                continue
            value = classify_postcode(raw).value
            digits = "".join(ch for ch in value if ch.isdigit())
            if _ORDINAL_INWARD.fullmatch(value[-3:]) is not None:
                offenders.append((raw, value))
            elif len(digits) >= 3 and _digits_are_low_entropy(digits):
                offenders.append((raw, value))
        assert offenders == [], f"STRONG but ordinal/filler-shaped: {offenders[:10]}"

    def test_an_empty_value_can_never_anchor_anything(self) -> None:
        """The proof that justifies the empty-value filter above.

        `postcode_is_weak_anchor("")` is False, which in isolation reads like
        "strong". It is safe only because an empty normalization NULLs the
        identity key, and a NULL-keyed row never reaches the keyed path whose
        bar 3 this function gates. Asserting that here means the filter above
        rests on a tested property rather than on my reading of the caller.
        """
        for raw in self._corpus():
            if classify_postcode(raw).value:
                continue
            assert compute_identity_key("Brand Gym", raw) is None

    def test_the_value_never_depends_on_the_confidence(self) -> None:
        # REPLACED THE TAUTOLOGY IT USED TO BE (round 15). This asserted
        # `normalize_postcode(raw) == classify_postcode(raw).value`, and
        # `normalize_postcode` is literally `return classify_postcode(x).value`
        # - so it compared a value to itself and would have held no matter what
        # the normalizer did. The real invariance claim is now carried by the
        # GOLDEN FILE (see `TestGoldenFile`), which is a committed artifact CI
        # re-measures on every run rather than a sentence someone must remember
        # to re-earn.
        #
        # What remains here is the narrow structural fact that is NOT a
        # tautology: the accessor must keep delegating, so no caller can be
        # handed a value the classifier did not produce.
        import inspect

        source = inspect.getsource(normalize_postcode)
        assert "classify_postcode(postcode).value" in source, (
            "normalize_postcode must remain the value half of classify_postcode; "
            "a second implementation is how the two halves drift apart"
        )

    def test_an_unenumerated_unit_word_is_never_safer_than_an_enumerated_one(self) -> None:
        # The P0.1 INVERSION, pinned across the whole vocabulary rather than
        # the two words the review happened to name. At head the ENUMERATED
        # words rated STRONG and the unenumerated ones WEAK, so every entry
        # added to `_UNIT_BEFORE` upgraded that word's anchor from weak to
        # strong - the precise opposite of what its own comment claimed.
        enumerated = ["Unit", "Suite", "Studio", "Gym", "Bay", "Pod", "Kiosk", "Cabin", "Stall"]
        unenumerated = ["Pitch", "Annexe", "Wing", "Zone", "Container", "Berth", "Arena"]
        for word in enumerated + unenumerated:
            assert postcode_is_weak_anchor(f"{word} B2, 1st") is True
            assert postcode_is_weak_anchor(f"{word} C1 2PM") is True


class TestGoldenFile:
    """The invariance claim as a committed artifact, not a sentence.

    WHY THIS EXISTS, stated plainly because the reason is a near-miss rather
    than a theory. Round 15 quoted "proven by the value-invariance sweep" in a
    docstring BEFORE running the sweep that round - the harness had been lost
    with the previous session's scratchpad. The claim happened to be true, which
    is luck, not process: nothing in the suite, the gate or the manifest checks
    whether a quoted figure was ever measured. The reviewer asked for this file
    for exactly that reason, in their words, because the real evidence "exists
    nowhere in the suite and cannot be re-run".

    So this is not a snapshot of expected values. It is the mechanism that makes
    the invariance claim SELF-VERIFYING: the claim can never again be quoted
    without being simultaneously re-earned, because CI re-measures it here on
    every run.

    TWO COLUMNS, TWO DIFFERENT INVARIANTS:
      `value`      pins the never-changes-without-a-migration property. A diff
                   here means a stored identity_key is orphaned and a recompute
                   migration is owed - it must fail loudly and force that
                   conversation, never be regenerated to make CI green.
      `confidence` pins THIS round's classification so future drift is visible.
                   A diff here is a behaviour change that may be legitimate, but
                   it must be a decision, not a surprise.

    The `identity_keys` section covers business names whose key depends on
    `_PLACEHOLDER_NAME_STEMS` - "Capacity" is the measured case, where adding
    "capacity" to the stems flips a live key to null. Stem drift therefore fails
    this file AND G7: two independent mechanisms, both already built.

    UPDATE PROCEDURE: regenerate ONLY with a recorded reason and, for any
    `value` or `identity_keys` change, a migration. Regenerating to silence a
    failure defeats the entire point of the file.
    """

    GOLDEN = pathlib.Path(__file__).parent / "golden" / "identity_key_golden.json"

    def _golden(self) -> dict:
        return json.loads(self.GOLDEN.read_text())

    def test_every_postcode_value_and_confidence_matches_the_golden_file(self) -> None:
        golden = self._golden()["postcodes"]
        drift = []
        for raw, value, confidence in golden:
            result = classify_postcode(raw)
            if result.value != value or result.confidence.value != confidence:
                drift.append((raw, (value, confidence), (result.value, result.confidence.value)))
        assert drift == [], (
            f"{len(drift)} record(s) drifted from the golden file; first 5: {drift[:5]}. "
            "A VALUE diff means a stored identity_key is orphaned and owes a migration."
        )

    def test_every_identity_key_matches_the_golden_file(self) -> None:
        drift = []
        for name, postcode, key in self._golden()["identity_keys"]:
            live = compute_identity_key(identity_name(name, None), postcode)
            if live != key:
                drift.append((name, key, live))
        assert drift == [], f"identity keys drifted from the golden file: {drift}"

    def test_the_golden_file_covers_a_stem_dependent_key(self) -> None:
        # The belt described in the class docstring. Without a record whose key
        # depends on `_PLACEHOLDER_NAME_STEMS`, stem drift would be caught by G7
        # alone; with it, two independent mechanisms fail.
        records = {name: key for name, _, key in self._golden()["identity_keys"]}
        assert records.get("Capacity") == "capaci|E81AA", (
            "the golden file must contain a business name whose key depends on "
            "the placeholder stems, or stem drift is only caught by G7"
        )
