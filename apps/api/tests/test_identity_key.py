"""Unit tests for the returning-client identity key (S1-26c).

Pure functions, no DB - these pin the normalization rules the whole
returning-client match depends on.
"""

from __future__ import annotations

import pytest

from bullet_api.worker.identity_key import (
    _ORDINAL_INWARD,
    LEGAL_ENTITY_PLACEHOLDER,
    _digits_are_low_entropy,
    addresses_materially_diverge,
    compute_identity_key,
    contact_name_agrees,
    corroborating_signal_agrees,
    identity_name,
    names_materially_diverge,
    normalize_name,
    normalize_phone,
    normalize_postcode,
    postcode_is_weak_anchor,
    postcodes_materially_diverge,
)


class TestNormalizeName:
    def test_lowercases_and_strips_punctuation_whitespace(self) -> None:
        assert normalize_name("Brand Gym  Hackney!") == "brandgymhackney"

    def test_drops_leading_the(self) -> None:
        # Otherwise "the gy" would eat the whole 6-char budget. Pins both
        # spellings the constant's comment cites.
        assert normalize_name("The Gym Group") == normalize_name("Gym Group") == "gymgroup"

    def test_drops_trailing_ltd(self) -> None:
        assert normalize_name("Foobar Ltd") == "foobar"

    def test_drops_trailing_limited(self) -> None:
        assert normalize_name("Foobar Limited") == "foobar"

    def test_ltd_and_limited_collapse_to_same_stem(self) -> None:
        assert normalize_name("Foobar Ltd") == normalize_name("Foobar Limited") == "foobar"

    def test_ltd_with_period(self) -> None:
        # "ltd." tokenizes to "ltd" (punctuation splits), so it is dropped.
        assert normalize_name("Foobar Ltd.") == "foobar"

    def test_only_article_or_suffix_yields_empty(self) -> None:
        assert normalize_name("The") == ""
        assert normalize_name("Ltd") == ""

    @pytest.mark.parametrize("value", [None, "", "   ", "!!!", "-"])
    def test_no_usable_tokens_yields_empty(self, value: str | None) -> None:
        assert normalize_name(value) == ""

    def test_leading_the_not_stripped_mid_word(self) -> None:
        # "Theatre" is one token starting with "the" but is not the article.
        assert normalize_name("Theatre Fitness") == "theatrefitness"

    def test_folds_accents(self) -> None:
        # Without the unicode fold the ASCII tokenizer DELETES the accented
        # letter ("café" -> "caf"), so one business would key two ways.
        assert normalize_name("Café Gym") == normalize_name("Cafe Gym") == "cafegym"

    def test_folds_accents_mid_word(self) -> None:
        assert normalize_name("Zürich Fitness") == "zurichfitness"


class TestNormalizePostcode:
    def test_uppercases_and_strips_space(self) -> None:
        assert normalize_postcode("e8 1aa") == "E81AA"

    def test_strips_all_non_alnum(self) -> None:
        assert normalize_postcode(" e8-1aa ") == "E81AA"

    @pytest.mark.parametrize("value", [None, "", "  ", "--"])
    def test_blank_yields_empty(self, value: str | None) -> None:
        assert normalize_postcode(value) == ""

    def test_extracts_uk_postcode_from_a_longer_string(self) -> None:
        # The whole point: "London E8 1AA" must key IDENTICALLY to "E8 1AA".
        # Stripping to alphanumerics alone gives LONDONE81AA, so the same
        # business keys two ways depending on how HubSpot's Zip was filled in.
        assert normalize_postcode("London E8 1AA") == normalize_postcode("E8 1AA") == "E81AA"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("SW1A 1AA", "SW1A1AA"),
            ("m1 1ae", "M11AE"),
            ("CR0 1AA, United Kingdom", "CR01AA"),
            ("Unit 4, EC1A 1BB", "EC1A1BB"),
        ],
    )
    def test_uk_formats(self, value: str, expected: str) -> None:
        assert normalize_postcode(value) == expected

    @pytest.mark.parametrize(
        "value",
        ["N/A", "n/a", "NA", "none", "NIL", "TBC", "tbd", "Unknown", "XXX", "00000", "0"],
    )
    def test_placeholders_rejected(self, value: str) -> None:
        # Each of these is non-empty after stripping, so without the denylist
        # they mint a real-looking key that every placeholder-using client
        # would SHARE - the exact mis-merge the key exists to prevent.
        assert normalize_postcode(value) == ""

    @pytest.mark.parametrize("value", ["A", "12", "x"])
    def test_too_short_rejected(self, value: str) -> None:
        assert normalize_postcode(value) == ""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("75008", "75008"), ("D02 X285", "D02X285"), ("10115", "10115"), ("2000", "2000")],
    )
    def test_international_postcodes_survive(self, value: str, expected: str) -> None:
        # The INT PandaDoc account is live, so a UK-only rule would silently
        # disable returning-client matching for every international client.
        assert normalize_postcode(value) == expected


class TestIdentityName:
    def test_prefers_business_name(self) -> None:
        assert identity_name("Trading Name", "Legal Entity Ltd") == "Trading Name"

    def test_falls_back_to_legal_entity(self) -> None:
        # A document carrying only the signed legal-trading-name field would
        # otherwise opt out of returning-client matching entirely.
        assert identity_name(None, "Legal Entity Ltd") == "Legal Entity Ltd"
        assert identity_name("   ", "Legal Entity Ltd") == "Legal Entity Ltd"

    def test_rejects_the_placeholder(self) -> None:
        # The placeholder is what we write when we learned NOTHING. Keying on
        # it would unite every unidentifiable signing under one identity.
        assert identity_name(None, LEGAL_ENTITY_PLACEHOLDER) is None

    def test_none_when_nothing_usable(self) -> None:
        assert identity_name(None, None) is None
        assert identity_name("", "") is None

    def test_placeholder_yields_no_key(self) -> None:
        assert compute_identity_key(identity_name(None, LEGAL_ENTITY_PLACEHOLDER), "E8 1AA") is None


class TestComputeIdentityKey:
    def test_first6_of_name_plus_postcode(self) -> None:
        assert compute_identity_key("Fitness First", "E8 1AA") == "fitnes|E81AA"

    def test_short_name_not_padded(self) -> None:
        assert compute_identity_key("Flex", "E8 1AA") == "flex|E81AA"

    def test_the_and_suffix_normalized_before_truncation(self) -> None:
        # "The Gym Group Ltd" -> "gymgroup" -> first6 "gymgro".
        assert compute_identity_key("The Gym Group Ltd", "SW1A 1AA") == "gymgro|SW1A1AA"

    def test_missing_postcode_returns_none(self) -> None:
        # Fail-safe: no postcode -> no match -> fresh client.
        assert compute_identity_key("Fitness First", None) is None
        assert compute_identity_key("Fitness First", "  ") is None

    def test_unusable_name_returns_none(self) -> None:
        assert compute_identity_key(None, "E8 1AA") is None
        assert compute_identity_key("The", "E8 1AA") is None

    def test_multi_email_unite_same_key(self) -> None:
        # The whole point: the SAME business reaches one key even when the two
        # signings spell its name and postcode differently (and, in production,
        # arrive under different emails - email is not an input here, which is
        # exactly why it can no longer split one client into two).
        #
        # The original version of this test compared a call to the identical
        # call - `x == x`, which cannot fail whatever the implementation does.
        assert compute_identity_key("Brand Gym Hackney", "E8 1AA") == compute_identity_key(
            "brand gym  hackney!", "e8-1aa"
        )

    def test_ltd_suffix_and_case_do_not_split_one_business(self) -> None:
        assert compute_identity_key("Sample Gym Ltd", "E8 1AA") == compute_identity_key(
            "SAMPLE GYM LIMITED", "E8   1AA"
        )

    def test_franchise_separation_by_postcode(self) -> None:
        # Same brand, different location -> different key.
        a = compute_identity_key("Brand Gym Hackney", "E8 1AA")
        b = compute_identity_key("Brand Gym Croydon", "CR0 1AA")
        assert a != b


class TestNamesMateriallyDiverge:
    def test_same_normalized_name_does_not_diverge(self) -> None:
        assert names_materially_diverge("Brand Gym Hackney", "brand gym  hackney!") is False

    def test_suffix_only_difference_does_not_diverge(self) -> None:
        assert names_materially_diverge("Foobar Ltd", "Foobar Limited") is False

    def test_prefix_collision_diverges(self) -> None:
        # Same first-6 ("fitnes") but different full names -> divergent.
        assert names_materially_diverge("Fitness First", "Fitness Studio") is True

    @pytest.mark.parametrize(
        ("a", "b"),
        [(None, None), ("", ""), ("   ", "   "), ("The", "Ltd"), (None, "Real Gym")],
    )
    def test_empty_stem_is_divergence_not_agreement(self, a: str | None, b: str | None) -> None:
        """An UNUSABLE name is divergence, never a match (round 2, finding 6).

        Two unidentifiable signings both normalize to "", so plain equality
        called them a match - directly contradicting `identity_name`'s promise
        that an unidentifiable signing can never merge with another. "The" and
        "Ltd" are the sharp case: both are non-empty strings that normalize
        away to nothing.

        Added in round 6, which found this branch had no killing test and no
        manifest entry - delete it and two unidentifiable signings clear bar 1.
        Note the deliberate ASYMMETRY against `addresses_materially_diverge`,
        where absence ABSTAINS: this one corroborates, so absence must fail
        closed; that one disqualifies, so absence must not veto.
        """
        assert names_materially_diverge(a, b) is True


class TestNormalizePhone:
    def test_formatting_differences_agree(self) -> None:
        # The same business re-typing its own number differently is NOT
        # evidence of a different business; treating it so would flag genuine
        # returning clients.
        assert (
            normalize_phone("+44 7700 900123")
            == normalize_phone("07700 900123")
            == normalize_phone("+447700900123")
        )

    def test_country_code_ignored(self) -> None:
        assert normalize_phone("+1 415 700900123") == normalize_phone("415700900123")

    def test_different_numbers_do_not_agree(self) -> None:
        assert normalize_phone("+447700900123") != normalize_phone("+447700900999")

    @pytest.mark.parametrize("value", [None, "", "   ", "12345", "ext. 22", "+44"])
    def test_too_few_digits_is_unusable(self, value: str | None) -> None:
        # An extension or a truncated field must never corroborate anything.
        assert normalize_phone(value) == ""

    @pytest.mark.parametrize("value", ["5551", "4419", "x2274", "+44 20 55"])
    def test_short_numbers_die_to_the_length_gate_alone(self, value: str | None) -> None:
        """Round 8, finding 1: the length gate was revert-green.

        Every param of the test above is ALSO caught by a different guard
        ("12345" by the sequential check, "+44" and "ext. 22" by the
        single-distinct-digit check), so `if False:`-ing the length gate left
        all 220 identity-key tests green. These digits are non-sequential and
        multi-distinct, so ONLY the length gate rejects them - and if it broke,
        a 4-digit extension would become a usable phone able to corroborate
        bar 2, the unsafe direction.
        """
        assert normalize_phone(value) == ""


class TestCorroboratingSignalAgrees:
    """The second bar: a signal INDEPENDENT of the company record.

    Address is deliberately NOT accepted (review round 2, finding 2) - it is
    read from the same HubSpot company record as the postcode, so it agrees
    exactly when the key already does and corroborates nothing.
    """

    def test_matching_phone_corroborates_across_formats(self) -> None:
        assert (
            corroborating_signal_agrees(phone_a="+44 7700 900123", phone_b="07700 900123") is True
        )

    def test_differing_phone_does_not_corroborate(self) -> None:
        assert (
            corroborating_signal_agrees(phone_a="+447700900123", phone_b="+447700900999") is False
        )

    def test_absence_is_not_agreement(self) -> None:
        # THE point of finding 4: when only name and postcode agree - nothing
        # else known on either side - that is not proof of one business.
        assert corroborating_signal_agrees(phone_a=None, phone_b=None) is False

    def test_signal_present_on_only_one_side_does_not_corroborate(self) -> None:
        assert corroborating_signal_agrees(phone_a="+447700900123", phone_b=None) is False
        assert corroborating_signal_agrees(phone_a=None, phone_b="+447700900123") is False

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("+44 0000 000000", "+44 0000 000000"),
            ("1111111111", "1111111111"),
            ("123456789", "123456789"),
        ],
    )
    def test_placeholder_numbers_never_corroborate(self, a: str, b: str) -> None:
        # Two unrelated clients whose phone field was filled with filler must
        # not corroborate each other into a merge.
        #
        # NOTE these three are caught by the ORIGINAL denylist and
        # distinct-digit check. They are kept as regression cover for those,
        # but they prove nothing about the round-4 sequential guard - see
        # `TestSequentialDigitFiller` below, which is the test that actually
        # dies when `_is_sequential_digits` is removed.
        assert corroborating_signal_agrees(phone_a=a, phone_b=b) is False


class TestNameTokenizerDropsPunctuationAndRuns:
    """The tokenizer's own documented examples, which nothing tested.

    `_ALNUM_TOKEN`'s comment names "F45  Training" (double space) and "ltd." as
    the cases it exists to normalize; neither appeared in the suite.
    """

    def test_repeated_whitespace_collapses(self) -> None:
        assert normalize_name("F45  Training") == normalize_name("F45 Training") == "f45training"

    def test_trailing_suffix_punctuation_dropped(self) -> None:
        assert normalize_name("Foobar Ltd.") == normalize_name("Foobar Limited") == "foobar"


class TestSequentialDigitFiller:
    """Kills `_is_sequential_digits` (review round 4, one-line finding).

    The guard existed for two rounds with NO test naming a value only it
    catches, so deleting it left the suite green - which is how round 5 found
    it. `"1234567890"` is the most common filler number in existence and its
    9-digit tail is `"234567890"`, which the old denylist (deleted in round 7
    as dead code - every member was sequential) never held anyway.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "1234567890",  # the tail is "234567890" - NOT in the old denylist
            "0123456789",
            "9876543210",
            "2345678901",
            # The old denylist's own members and the docstring's rotations -
            # each must still die to the SHAPE check now the denylist is gone.
            "987654321",
            "012345678",
            "876543210",
        ],
    )
    def test_sequential_runs_are_filler(self, value: str) -> None:
        assert normalize_phone(value) == ""

    def test_sequential_filler_never_corroborates(self) -> None:
        # The 9-digit tail of "1234567890" really is the "234567890" the
        # source comment cites - computed here so the claim cannot go stale.
        assert "1234567890"[-9:] == "234567890"
        assert corroborating_signal_agrees(phone_a="1234567890", phone_b="1234567890") is False


class TestRealLandlinesSurvive:
    """Review round 5: the filler check was rejecting real UK landlines.

    `len(set(tail)) <= 2` treated `"+44 20 7700 0000"` (tail `"077000000"` -
    the wrong literal `"770000000"` stood here for two rounds, corrected round 7 -
    distinct digits {7, 0}) as filler, so bar 2 was PERMANENTLY unsatisfiable
    for every client on such a number and a genuine returning client got a
    duplicate sub-account on every signing, silently. Filler is a matter of
    shape - one repeated digit, or a repeating pair - not of how few distinct
    digits happen to appear.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "+44 20 7700 0000",  # London landline
            "020 7000 0000",
            "0800 100 1000",  # freephone
            "+44 161 200 2000",
        ],
    )
    def test_real_landline_is_usable(self, value: str) -> None:
        assert normalize_phone(value) != ""

    def test_london_landline_tail_is_the_documented_literal(self) -> None:
        # Pins the exact 9-digit tail the docstrings cite ("077000000"). The
        # wrong literal ("770000000") stood in three docstrings for two rounds
        # because nothing computed it - a stale-claim class the reviewer greps.
        assert normalize_phone("+44 20 7700 0000") == "077000000"

    def test_landline_corroborates_a_returning_client(self) -> None:
        assert (
            corroborating_signal_agrees(phone_a="+44 20 7700 0000", phone_b="020 7700 0000") is True
        )

    @pytest.mark.parametrize(
        "value",
        [
            "0000000000",
            "1212121212",
            "2121212121",
            # The exact 9-digit examples the constants' comments cite.
            "000000000",
            "121212121",
        ],
    )
    def test_shape_filler_is_still_rejected(self, value: str) -> None:
        # Narrowing the check must not reopen the hole it was narrowed from.
        assert normalize_phone(value) == ""


class TestPostcodeFormInvariance:
    """THE invariant: one postal value produces one key, whatever form it takes.

    Four axes vary independently in the wild - token order, separator, case and
    an attached town - and normalization must be blind to all four. Round 4
    fixed the ORDER axis by sorting tokens and broke the SEPARATOR axis doing
    it, which is review round 5's finding 2: a regression of the same class as
    the bug it fixed. Testing the invariant rather than the reported example is
    what makes that impossible to repeat.
    """

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("K1A 0B1", "K1A0B1"),  # Canada - broken by round 4's sort
            ("94107 1234", "94107-1234"),  # US ZIP+4 - broken by round 4's sort
            ("E8 1AA", "E8, 1AA"),  # UK, punctuated address line
            ("E8 1AA", "E8-1AA"),  # UK, hyphenated
            # A comma-separated line carrying a FLAT NUMBER. This is the case
            # that makes the relaxed separator load-bearing rather than
            # cosmetic: without it the UK branch misses, step 3 keeps every
            # digit-bearing token, and the flat number contaminates the key
            # ("2E81AA"). Found by the mutation runner, not by review - the
            # first three rows above all survive the unrelaxed separator.
            ("E8 1AA", "Flat 2, E8, 1AA"),
            ("E8 1AA", "e8  1aa"),  # case + whitespace
            ("London E8 1AA", "E8 1AA"),  # attached town (UK: regex extracts)
            # ("75008 Paris", "Paris 75008") is GONE from this list - the order
            # axis is deliberately open by theorem (see normalize_postcode and
            # TestOrderAxisIsDeliberatelyOpen in the properties file). Round 4
            # required it, and satisfying it is exactly what forced every
            # subsequent round's collide.
            ("D02 X285", "D02X285"),  # Ireland
        ],
    )
    def test_same_value_different_form_keys_identically(self, a: str, b: str) -> None:
        assert normalize_postcode(a) == normalize_postcode(b) != ""

    def test_different_postcodes_still_differ(self) -> None:
        # Form-invariance must not collapse genuinely different values.
        assert normalize_postcode("E8 1AA") != normalize_postcode("E9 1AA")
        assert normalize_postcode("75008 Paris") != normalize_postcode("75009 Paris")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # The key IS the flat ordered string (round 7's spec) - pinned
            # because migration 0013 warns stored keys are a snapshot of this
            # module and there is no recompute tool. Round 6 pinned "011ABK"
            # here: the sorted form, whose anagram collisions are the round-7
            # P0.
            ("K1A 0B1", "K1A0B1"),
            # ZIP+4 reduces to ZIP5, so "94107" and "94107-1234" are one client.
            ("94107 1234", "94107"),
            # The town is KEPT (dropping was round 5's collide) and order is
            # KEPT (sorting was round 6's) - so the town rides along verbatim.
            ("75008 Paris", "75008PARIS"),
            ("E8, 1AA", "E81AA"),
        ],
    )
    def test_canonical_output_is_pinned(self, value: str, expected: str) -> None:
        """Pin the exact canonical form, not just that two inputs agree.

        Round 4's sort produced "0B1K1A" and "123494107" for the first two -
        self-consistent, so an equality-only test could still pass while the
        stored key changed under everyone. Migration 0013 warns that stored
        keys are a snapshot of this normalizer, so the concrete output is part
        of the contract, not an implementation detail.
        """
        assert normalize_postcode(value) == expected


class TestMalformedPostcodeNeverMintsAUkKey:
    """Kills the round-4 anchoring of `_UK_POSTCODE`.

    Unanchored, the pattern matched a SUBSTRING of a malformed value, minting a
    confident but WRONG UK key instead of falling through to the fail-safe.
    Neither literal below appeared anywhere in the suite before round 5, so the
    anchors were revert-green.
    """

    def test_trailing_characters_do_not_mint_a_uk_key(self) -> None:
        assert normalize_postcode("AB12 3CDE") != "AB123CD"

    def test_extra_letter_does_not_mint_a_uk_key(self) -> None:
        assert normalize_postcode("E81AAX") != "E81AA"

    def test_a_malformed_value_never_collides_with_the_real_one(self) -> None:
        # The failure that matters: a malformed value keying as a REAL client's
        # postcode would merge two unrelated businesses.
        assert normalize_postcode("E81AAX") != normalize_postcode("E8 1AA")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Unit B2, 1st Floor, E8 1AA", "E81AA"),
            ("Suite C3 2nd Floor London SW1A 1AA", "SW1A1AA"),
        ],
    )
    def test_english_ordinal_is_not_read_as_the_inward_half(
        self, value: str, expected: str
    ) -> None:
        # A unit ordinal (a floor number) whose inward half matched the regex is
        # a fallback, not the answer: the real postcode is a later non-ordinal
        # match and wins (round 9 P1.3, refined round 10). The unit number no
        # longer latches onto the ordinal and keys instead of the postcode.
        assert normalize_postcode(value) == expected

    def test_two_sites_one_brand_do_not_collapse_via_the_ordinal(self) -> None:
        # Worse than a split: without preferring the real postcode over the
        # ordinal, both sites keyed on the unit ordinal, so two genuinely
        # different postcodes collapsed onto one key - the postcode axis stripped
        # of its separating power (round 9, P1.3).
        first = normalize_postcode("Unit A1, 1st Floor, E8 1AA")
        second = normalize_postcode("Unit A1, 1st Floor, CR0 2AB")
        assert first == "E81AA"
        assert second == "CR02AB"
        assert first != second

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # NOTE for future trimming (round 11, P3): the first four BARE params
            # coincide with the token path - they pass even with the ordinal
            # fallback deleted - so ONLY the two address-bearing params below
            # carry the mutation kill for the fallback. Do not trim those two.
            ("E8 1ST", "E81ST"),
            ("B33 8TH", "B338TH"),
            ("E8 2ND", "E82ND"),
            ("N7 3RD", "N73RD"),
            ("E8 1ST, UK", "E81ST"),
            ("Flat 2, 14 Mare St, E8 1ST", "E81ST"),
        ],
    )
    def test_ordinal_shaped_real_postcode_is_not_discarded(self, value: str, expected: str) -> None:
        # An inward half that looks like an ordinal ("1ST"/"2ND"/"3RD"/"4TH") can
        # be a REAL postcode - "B33 8TH" is one of GOV.UK's six canonical
        # examples. Skipping it outright (round 9) discarded ~1% of real UK
        # postcodes; round 10 keeps it as a fallback and returns it when no
        # non-ordinal match exists.
        assert normalize_postcode(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Unit A1, 1st Floor, B33 8TH", "B338TH"),
            ("Unit A1, 1st Floor, E8 1ST", "E81ST"),
            ("Suite C3 2nd Floor London SW1A 1ST", "SW1A1ST"),
            ("Suite C3 2nd Floor Leeds LS1 4ST", "LS14ST"),
        ],
    )
    def test_floor_ordinal_loses_to_an_ordinal_shaped_real_postcode(
        self, value: str, expected: str
    ) -> None:
        # Round 11 P0.1, mechanism replaced in round 12: the floor ordinal is
        # DROPPED by the structure-word and unit-designator guards, leaving the
        # real ordinal-shaped postcode as the sole surviving candidate - no
        # positional keep-first/keep-last rule exists any more (both were
        # disproved, rounds 10 and 11 respectively).
        assert normalize_postcode(value) == expected

    def test_two_ordinal_shaped_sites_do_not_collapse_via_the_floor_number(self) -> None:
        # The exact round-11 collision: both sites carried "A11ST" (the floor
        # ordinal) under the first-match fallback, erasing the postcode axis.
        first = normalize_postcode("Unit A1, 1st Floor, B33 8TH")
        second = normalize_postcode("Unit A1, 1st Floor, E8 1ST")
        assert first == "B338TH"
        assert second == "E81ST"
        assert first != second

    def test_unit_prefixed_ordinal_is_dropped_so_the_real_ordinal_postcode_wins(self) -> None:
        # Round 11's pin, re-grounded on the round-12 mechanism: "Unit B2 1st"
        # is dropped by the unit-designator guard (not outranked by position),
        # leaving "E8 1ST" as the sole surviving candidate. The required output
        # is unchanged from round 11.
        assert normalize_postcode("Unit B2 1st, E8 1ST") == "E81ST"

    def test_floor_ordinal_alone_does_not_mint_a_postcode_key(self) -> None:
        # Round 11, P0.1 (related): an ordinal immediately followed by a floor
        # word is a FLOOR, never a postcode. A value with no postcode at all must
        # not mint a confident key that collides with a genuine client at the
        # matching real postcode - it falls through to the verbatim token path.
        assert normalize_postcode("Unit B2, 1st Floor") == "UNITB21STFLOOR"

    def test_ordinal_fallback_returns_before_the_filler_gauntlet(self) -> None:
        # Documented behaviour (round 11, P3): the ordinal fallback returns like
        # every other UK-probe hit - before the filler checks - so a UK-shaped
        # string with an ordinal inward keys verbatim ("A0 0TH"). Consistent
        # with the non-ordinal path, and fail-toward-SPLIT if the value is junk.
        assert normalize_postcode("A0 0TH") == "A00TH"

    @pytest.mark.parametrize(
        "prefix",
        ["London ", "Unit A1, 1st Floor, ", "Suite C3 2nd Floor ", "Flat 2, 14 Mare St, "],
    )
    @pytest.mark.parametrize(
        "postcode",
        ["E8 1AA", "SW1A 1AA", "CR0 2AB", "E8 1ST", "B33 8TH", "N7 3RD"],
    )
    def test_address_prefix_does_not_change_the_key(self, prefix: str, postcode: str) -> None:
        # The invariant the raw probe exists for (round 10, prefix-parametrized
        # in round 11): a value carrying a town OR a unit/floor prefix must key
        # identically to the bare postcode - including ordinal-shaped postcodes
        # like "B33 8TH", which round 10's first-match fallback regressed for
        # exactly the unit/floor prefixes this now parametrizes.
        assert normalize_postcode(prefix + postcode) == normalize_postcode(postcode)

    @pytest.mark.parametrize(
        "suffix",
        [", UK", ", Unit A1 1st", ", 2nd Street", " 1st Floor", ", Suite C3 2nd"],
    )
    @pytest.mark.parametrize(
        "postcode",
        ["E8 1AA", "SW1A 1AA", "CR0 2AB", "E8 1ST", "B33 8TH", "N7 3RD"],
    )
    def test_address_suffix_does_not_change_the_key(self, suffix: str, postcode: str) -> None:
        # Round 12 test gap 1: the prefix test above covered only the direction
        # round 11 had already fixed, leaving the suffix mirror (P0.2's trailing
        # unit ordinal) invisible to the suite. Trailing address furniture -
        # country, unit ordinal, ordinal street, floor - must never change the
        # key of the real postcode that precedes it.
        assert normalize_postcode(postcode + suffix) == normalize_postcode(postcode)


class TestUnicodeFold:
    """Letters NFKD cannot decompose (review round 5).

    NFKD splits base + combining mark, but o-slash, eszett, l-stroke and ash
    are single indivisible code points - nothing to split - so the ASCII
    tokenizer DELETED them, keying one business two ways.
    """

    @pytest.mark.parametrize(
        ("accented", "plain"),
        [
            ("Bjørn Fitness", "Bjorn Fitness"),
            ("Café Gym", "Cafe Gym"),
            ("Łódź Gym", "Lodz Gym"),
            ("Æon Fitness", "AEon Fitness"),
        ],
    )
    def test_accented_and_plain_spellings_key_identically(self, accented: str, plain: str) -> None:
        assert normalize_name(accented) == normalize_name(plain) != ""


class TestAddressesMateriallyDiverge:
    """Bar 4 - a DISQUALIFIER, so its absence posture is inverted.

    Review round 5, finding 1. Address cannot corroborate (it collapses with
    the postcode, round 2 finding 2) but a DIFFERING address can still refuse a
    link, and that refusal is the only thing separating one owner's two sites
    when brand, head-office postcode and phone are all identical.
    """

    def test_different_addresses_diverge(self) -> None:
        assert (
            addresses_materially_diverge("Studio One, 1 Mare Street", "Studio Two, 99 Kingsland")
            is True
        )

    def test_same_address_does_not_diverge(self) -> None:
        assert addresses_materially_diverge("1 Mare Street", "1 Mare Street") is False

    def test_abbreviated_street_form_diverges_as_the_docstring_claims(self) -> None:
        """The docstring's own example pair, pinned verbatim.

        Round 7's live G1 instance: the docstring named "1 Mare St" vs
        "1 Mare Street" and no test used the abbreviated form - substring
        matching hid it behind the longer literal. STRICT divergence is the
        documented intent: a refusal costs a spare flagged sub-account, a
        false pass-through costs a wrong merge.
        """
        assert addresses_materially_diverge("1 Mare St", "1 Mare Street") is True

    def test_formatting_differences_do_not_diverge(self) -> None:
        assert addresses_materially_diverge("1 Mare Street", "1  mare  street.") is False

    @pytest.mark.parametrize(
        ("a", "b"),
        [(None, "1 Mare Street"), ("1 Mare Street", None), (None, None), ("", "1 Mare Street")],
    )
    def test_absence_abstains_rather_than_disqualifying(self, a: str | None, b: str | None) -> None:
        # THE asymmetry against `names_materially_diverge`, where absence IS
        # divergence. A missing corroborator must fail closed or a missing
        # signal reads as a match; a missing DISQUALIFIER must abstain or it
        # vetoes links it has no evidence against - which for the legacy rows
        # carrying no address would refuse every genuine returning client.
        assert addresses_materially_diverge(a, b) is False


class TestContactNameAgrees:
    """The THIRD bar (review round 4, finding 4) - used alongside phone, only
    on the NULL-identity-key email fallback, which has no postcode anchor.
    """

    def test_matching_full_name_agrees(self) -> None:
        assert contact_name_agrees("John", "Smith", "John", "Smith") is True

    def test_case_and_whitespace_insensitive(self) -> None:
        assert contact_name_agrees("  John ", "SMITH", "john", "smith") is True

    def test_different_last_name_does_not_agree(self) -> None:
        # THE regression case: two franchise sites sharing one ops@ mailbox
        # and one head-office phone must still be told apart by WHO signed.
        assert contact_name_agrees("Alice", "Hackney", "Bob", "Croydon") is False

    def test_different_first_name_does_not_agree(self) -> None:
        assert contact_name_agrees("Alice", "Smith", "Bob", "Smith") is False

    def test_absence_is_not_agreement(self) -> None:
        assert contact_name_agrees(None, None, None, None) is False
        assert contact_name_agrees("", "", "", "") is False

    def test_name_present_on_only_one_side_does_not_agree(self) -> None:
        assert contact_name_agrees("John", "Smith", None, None) is False
        assert contact_name_agrees(None, None, "John", "Smith") is False

    @pytest.mark.parametrize("missing", [None, "", "   "])
    def test_blank_surname_does_not_collapse_to_a_first_name_match(
        self, missing: str | None
    ) -> None:
        """Review round 5, finding 4 - and the reason bar 3 needed narrowing.

        Concatenate-and-strip meant a blank `Client.LastName` on BOTH rows
        reduced this bar to first names: two different Sarahs at two sites,
        sharing an `ops@` mailbox and a head-office line, both with a blank
        `Company.Zip`, linked with no flag - reopening round 2's finding 5
        through a different door.

        The surname is also the field most likely to be absent: it is sourced
        from a merge token pinned to the UK template, so an INT template
        lacking it yields None on EVERY row. Absence of a signal is absence of
        the signal, never agreement.
        """
        assert contact_name_agrees("Sarah", missing, "Sarah", missing) is False

    @pytest.mark.parametrize(
        "full_name",
        ["Head Office", "Accounts Department", "Front Desk", "The Office"],
    )
    def test_placeholder_contact_names_never_agree(self, full_name: str) -> None:
        # A department is what gets typed when the real signer is unknown, so
        # two unrelated clients would corroborate each other on it - the same
        # reasoning the placeholder postcode and phone denylists apply.
        # Parametrized on the FULL name so each denylist member appears
        # literally, and a member silently dropped from the set fails here.
        first, last = full_name.split(" ", 1)
        assert contact_name_agrees(first, last, first, last) is False

    @pytest.mark.parametrize(
        ("first", "last"),
        [
            ("Head", "Office."),
            ("Front", "Desk."),
            ("Accounts", "Dept"),
            ("Main", "Office"),
            ("Reception", "Desk"),
        ],
    )
    def test_trailing_punctuation_cannot_bypass_the_placeholder_set(
        self, first: str, last: str
    ) -> None:
        """Round 8: the membership test was a raw strip+lower denylist - the
        pattern this module deleted everywhere else - bypassed in the UNSAFE
        direction: ("Head", "Office.") CORROBORATED because "head office."
        missed the set. Both parts now fold + tokenize first, exactly as
        `normalize_name` does."""
        assert contact_name_agrees(first, last, first, last) is False

    def test_accented_signer_agrees_with_unaccented_spelling(self) -> None:
        # Round 8: no unicode fold meant "José"/"Jose" never agreed, so bar 3
        # was permanently unsatisfiable for an accented signer whose documents
        # differ only by an accent - the class round 5 fixed for names. Safe
        # direction, but a silent permanent self-skip.
        assert contact_name_agrees("José", "Ruiz", "Jose", "Ruiz") is True

    def test_placeholder_matching_is_case_and_space_insensitive(self) -> None:
        # The denylist is compared against the lowercased "first last" form, so
        # "Head Office" typed any which way must still be rejected.
        assert contact_name_agrees("HEAD", " Office ", "head", "office") is False

    def test_a_real_full_name_still_agrees(self) -> None:
        # Narrowing must not break the case bar 3 exists to serve: the SAME
        # person signing a second time for a genuinely returning client.
        assert contact_name_agrees("Sarah", "Okonkwo", "sarah", " Okonkwo ") is True


class TestPrefixCollisionIsFlaggedNotMerged:
    """`names_materially_diverge`'s own documented example, untested until now.

    The 6-char key truncation is deliberately lenient, so this function's job is
    catching the false matches that leniency creates. Its docstring names
    "Brand Gym Hackney Gym" vs "Brand Gym Hackney" as the pair that must
    diverge - a strictness that is the safe side of "never auto-merge".
    """

    def test_a_longer_name_at_the_same_key_diverges(self) -> None:
        assert names_materially_diverge("Brand Gym Hackney Gym", "Brand Gym Hackney") is True

    def test_the_two_still_share_an_identity_key(self) -> None:
        # Which is WHY the divergence check earns its place: the key alone
        # cannot separate them.
        assert compute_identity_key("Brand Gym Hackney Gym", "E8 1AA") == compute_identity_key(
            "Brand Gym Hackney", "E8 1AA"
        )


class TestPostcodePlaceholderShapes:
    """A denylist only catches the placeholders someone thought of.

    These are rejected by SHAPE, not membership, so filler nobody listed still
    fails (review round 2, P2).
    """

    @pytest.mark.parametrize("value", ["XXXX", "AAAA"])
    def test_repeated_letters_with_no_digit_rejected(self, value: str) -> None:
        # A repeated LETTER with no digit is filler by the no-digit shape.
        assert normalize_postcode(value) == ""

    @pytest.mark.parametrize("value", ["00000", "0000", "000"])
    def test_all_zero_purely_numeric_rejected(self, value: str) -> None:
        # An all-ZERO block is the ONLY single-repeated-digit filler shape; no
        # postal system issues one.
        assert normalize_postcode(value) == ""

    @pytest.mark.parametrize("value", ["2222", "111", "22222", "55555", "9999", "99999", "1111"])
    def test_letterless_repdigit_postcodes_survive(self, value: str) -> None:
        # Round 9, P1.2: real LETTERLESS repdigit postcodes - Itegem "2222",
        # Reykjavik "111", Arlington "22222", Young America "55555", Rottum
        # "9999" - were wrongly NULL-keyed by the purely-numeric branch, so the
        # returning-client check self-skipped on EVERY signing for those clients.
        # Round 8's fix spared only the letter-bearing spellings ("1111 AB"). A
        # nonzero repdigit block is a real postcode with or without letters.
        assert normalize_postcode(value) == value

    def test_filler_digits_beside_letters_still_reject(self) -> None:
        """ "00000 ABC" must not mint a key however many letters ride along.

        (Renamed in round 7: the old name cited "the town drop step", a
        mechanism rounds 5-6 shipped and round 7 deleted - a lossy drop was the
        round-5 collide. The rejection now comes from the contiguous
        repeated-digit shape check, which sees the "00000" run inside
        "00000ABC".)
        """
        assert normalize_postcode("00000 ABC") == ""

    @pytest.mark.parametrize("value", ["TBA", "NONE", "ASAP", "PENDING"])
    def test_no_digit_at_all_rejected(self, value: str) -> None:
        # Every real postal format in use carries at least one digit.
        assert normalize_postcode(value) == ""

    @pytest.mark.parametrize("value", ["75008", "10115", "D02X285", "E81AA"])
    def test_real_postcodes_still_survive(self, value: str) -> None:
        assert normalize_postcode(value) != ""


class TestPostcodeIsWeakAnchor:
    """A repdigit postcode keys (P1.2) but is too low-entropy to anchor a keyed
    merge on its own (review round 10, P0.1)."""

    @pytest.mark.parametrize("value", ["11111", "2222", "111", "99999", "1111"])
    def test_purely_numeric_repdigit_is_weak(self, value: str) -> None:
        # These KEY (they are not filler), but a shared repdigit postcode is not
        # proof of one business, so the caller must keep the signer bar.
        assert postcode_is_weak_anchor(value) is True

    @pytest.mark.parametrize("value", ["11111 USA", "11111 Berlin", "99999-XX"])
    def test_repdigit_with_adjacent_text_is_still_weak(self, value: str) -> None:
        # Round 11, P1.1: `normalized == digits` let filler typed with a country
        # or town walk past the gate, so two rows sharing that data-entry
        # convention keyed identically with bar 3 waived - the round-10 merge,
        # re-opened. Weakness is judged on the leading digit block, not on the
        # value being digits-only.
        assert postcode_is_weak_anchor(value) is True

    def test_the_adjacent_text_shape_keys_with_its_residue_yet_stays_weak(self) -> None:
        # The exact mechanism of the bypass: the residue letters survive
        # normalization, so the value is NOT digits-only - which is why the old
        # test missed it - yet the leading repdigit block still keys the row.
        assert normalize_postcode("11111 USA") == "11111USA"
        assert postcode_is_weak_anchor("11111USA") is True

    def test_stockholm_five_ones_is_weak_in_the_safe_direction(self) -> None:
        # "111 11" is a REAL Stockholm postcode that normalizes to "11111" and
        # is indistinguishable from filler. Classifying it weak only keeps bar 3
        # ON (a returning client with the same signer still links), which is the
        # safe direction - documented, not accidental.
        assert postcode_is_weak_anchor("111 11") is True

    @pytest.mark.parametrize("value", ["75008", "10115", "E8 1AA", "SW1A 1AA", "E1 1EE"])
    def test_a_postcode_with_real_entropy_is_strong(self, value: str) -> None:
        # High-entropy digit content ("75008") or a letter-structured code
        # ("E8 1AA", "E1 1EE" - repeated digits but under three of them) is a
        # real anchor.
        assert postcode_is_weak_anchor(value) is False

    def test_three_digit_doubled_run_is_not_filler(self) -> None:
        # "B33 8TH" has digit content "338" - one doubled digit plus a tail is
        # ordinary real-code content at three digits, so the doubled-run rule
        # is gated to four-plus.
        assert _digits_are_low_entropy("338") is False

    def test_brussels_thousand_is_weak_under_the_digit_content_rule(self) -> None:
        # SPEC CHANGE, round 12 P1.2 (this row was pinned STRONG in round 11):
        # classifying on digit content makes "1000" weak - its "000" run is
        # byte-identical to data-entry filler ("10000" is in the reviewer's
        # placeholder sweep), and a rule that keeps "1000" strong keeps the
        # filler strong too. Weak only keeps bar 3 REQUIRED: a genuine Brussels
        # returning client with the same signer still links, so the cost of the
        # reversal is a flag-not-merge on a different signer - the safe
        # direction, accepted and documented.
        assert postcode_is_weak_anchor("1000") is True

    @pytest.mark.parametrize("value", [None, "", "TBA", "00000"])
    def test_absent_or_filler_postcode_is_not_a_weak_anchor(self, value: str | None) -> None:
        # Absence / filler normalizes to "" (no key exists to anchor), and the
        # weak regex cannot match an empty string, so the gate abstains.
        assert postcode_is_weak_anchor(value) is False


class TestPostcodesMateriallyDiverge:
    """Bar 5 (review round 9 P1.1): a disqualifier - both-present-and-unequal
    REFUSES, absence ABSTAINS, and it never grants a link."""

    def test_two_different_real_postcodes_diverge(self) -> None:
        assert postcodes_materially_diverge("E8 1AA", "SW1A 1AA") is True

    def test_the_same_postcode_in_two_spellings_does_not_diverge(self) -> None:
        # Normalized comparison, so separators and case do not create a false
        # refusal for one real postcode.
        assert postcodes_materially_diverge("e8 1aa", "E8 1AA") is False

    @pytest.mark.parametrize(
        ("a", "b"),
        [(None, "E8 1AA"), ("E8 1AA", None), (None, None), ("TBA", "E8 1AA"), ("", "E8 1AA")],
    )
    def test_absence_abstains(self, a: str | None, b: str | None) -> None:
        # The fail-open branch: if either side is missing or normalizes to "",
        # bar 5 must abstain rather than veto an otherwise-corroborated link.
        assert postcodes_materially_diverge(a, b) is False


class TestPostcodeCandidateSpec:
    """Round 12 P0.1/P0.2: the candidate specification, stated as a matrix.

    The ordinal guard was wrong three ways in three rounds (skip-all, keep-first,
    keep-last-with-a-floor-denylist) because each was a positional patch against
    the review's example. This class IS the specification: every row below is a
    required output, covering the reviewer's sweep shapes from rounds 9-12 AND
    every previously verified row, so a future patch that trades one shape for
    another fails here immediately.
    """

    # -- Rows that must KEEP working (rounds 9-11 verified outputs) -----------
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("E8 1AA", "E81AA"),
            ("London E8 1AA", "E81AA"),
            ("B33 8TH", "B338TH"),
            ("London B33 8TH", "B338TH"),
            ("B2 1ST", "B21ST"),
            ("Unit B2, 1st Floor, E8 1AA", "E81AA"),
            ("Unit A1, 1st Floor, B33 8TH", "B338TH"),
            ("Unit B2 1st, E8 1ST", "E81ST"),
        ],
    )
    def test_previously_verified_rows_unchanged(self, value: str, expected: str) -> None:
        assert normalize_postcode(value) == expected

    # -- Round 12 P0.1: ordinal STREET words are the class FLOOR belonged to --
    @pytest.mark.parametrize(
        ("value_a", "value_b"),
        [
            # 20 of 25 world cities collapsed onto 'A13RD' at head. Distinct
            # geography must produce distinct keys (or no key at all).
            ("Unit A1, 3rd Avenue, Chicago IL 60601", "Unit A1, 3rd Avenue, New York NY 10001"),
            ("Suite C3 2nd Street London SW1A 1AA", "Suite C3 2nd Street Leeds LS1 4AB"),
        ],
    )
    def test_ordinal_street_never_collapses_distinct_geography(
        self, value_a: str, value_b: str
    ) -> None:
        key_a = normalize_postcode(value_a)
        key_b = normalize_postcode(value_b)
        assert key_a != key_b or (key_a == "" and key_b == "")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # A real postcode beside an ordinal street must win, both orders.
            ("Birmingham B33 8TH, Unit A1, 2nd Street", "B338TH"),
            ("London E8 1ST, Unit A1, 2nd Street", "E81ST"),
            ("Unit A1, 2nd Street, Birmingham B33 8TH", "B338TH"),
            ("2nd Avenue office, E8 1AA", "E81AA"),
        ],
    )
    def test_real_postcode_beats_ordinal_street(self, value: str, expected: str) -> None:
        assert normalize_postcode(value) == expected

    def test_ordinal_street_alone_mints_no_postcode_shaped_key(self) -> None:
        # 'Unit A1, 3rd Avenue' carries no postcode. Whatever key it produces
        # must NOT equal the key of a genuine 'A1 3RD'-shaped postcode.
        assert normalize_postcode("Unit A1, 3rd Avenue") != "A13RD"

    def test_structure_word_ordinal_without_unit_prefix_mints_no_postcode_key(self) -> None:
        # The structure-word guard's own kill: NO unit designator in the value,
        # so the unit-before guard cannot mask the mutation (round 11's
        # floor-params lesson, applied at authoring time). Without the guard,
        # 'C3 2nd Street' keys byte-identically to the genuine client at
        # 'C3 2ND'.
        assert normalize_postcode("C3 2nd Street") != normalize_postcode("C3 2ND")

    # -- Round 12 P0.2: the TRAILING unit ordinal (the keep-last mirror) ------
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("B33 8TH, Unit A1 1st", "B338TH"),
            ("E8 1ST, Unit A1 1st", "E81ST"),
        ],
    )
    def test_trailing_unit_ordinal_never_beats_the_real_postcode(
        self, value: str, expected: str
    ) -> None:
        assert normalize_postcode(value) == expected

    @pytest.mark.parametrize(
        "value", ["Unit B2 1st", "Unit B2, 1st", "Unit B2 1st Level", "Unit A1 1st"]
    )
    def test_unit_ordinal_alone_does_not_impersonate_a_postcode(self, value: str) -> None:
        # A unit number with a trailing ordinal is not a postcode. It must not
        # produce the same key as the genuine Birmingham client at 'B2 1ST'
        # (round 11 asked for exactly this; the floor-word denylist delivered it
        # only when the literal word FLOOR followed).
        assert normalize_postcode(value) != normalize_postcode("B2 1ST")
        assert normalize_postcode(value) != normalize_postcode("A1 1ST")

    # -- Ambiguity fails toward SPLIT (the reviewer's stated direction) -------
    def test_two_real_postcodes_are_ambiguous_and_split(self) -> None:
        # Two genuine postcode candidates and nothing to choose between them:
        # minting either would guess. NULL key -> fail-safe CREATE.
        assert normalize_postcode("E8 1AA / N1 4AB") == ""

    def test_two_undecidable_ordinals_split(self) -> None:
        # Two ordinal-shaped candidates, neither droppable by structure
        # evidence: position provably cannot decide (round 11 proved keep-last
        # wrong; round 10 proved keep-first wrong). SPLIT.
        assert normalize_postcode("E8 1ST B33 8TH") == ""

    @pytest.mark.parametrize("inward", ["1ST", "2ND", "3RD", "8TH"])
    def test_ordinal_inward_shapes_are_recognised(self, inward: str) -> None:
        # Pins the _ORDINAL_INWARD alphabet the comments cite: these are the
        # inward halves that are AMBIGUOUS (real postcode or unit ordinal).
        assert _ORDINAL_INWARD.fullmatch(inward)
        assert not _ORDINAL_INWARD.fullmatch("1AA")

    @pytest.mark.parametrize("street", ["3rd Avenue", "2nd Street"])
    def test_bare_ordinal_street_mints_no_postcode_shaped_key(self, street: str) -> None:
        # The structure-word guard's own alphabet: an ordinal street after a
        # unit token must never yield the 5-char ordinal key.
        key = normalize_postcode(f"Unit A1, {street}")
        assert key not in {"A13RD", "A12ND"}

    def test_one_real_plus_ordinal_keeps_the_real(self) -> None:
        # S1-26f's documented residual is unchanged: one REAL candidate wins
        # even when an ordinal-shaped value precedes it.
        assert normalize_postcode("B33 8TH, Head Office N1 4AB") == "N14AB"


class TestWeakAnchorDigitContent:
    """Round 12 P1.2: weak-anchor classification on DIGIT CONTENT, positionless.

    Round 11's leading-run regex modelled exactly one filler shape and anchored
    at the start. The reviewer's actual suggestion (both rounds) was to classify
    on the digit content of the normalized value. These rows are the reviewer's
    own placeholder sweep plus the strong rows that must stay strong.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "12345",
            "54321",
            "1234",
            "123456",
            "12345678",
            "10000",
            "11223",
            "00001",
            "98765",
            "11112",
            "12321",
            "1212",
            "13579",
            "11111",
            "99999",
            "2222",
            "111",
            # Sole-kill params for individual low-entropy clauses (each is
            # caught by exactly ONE rule, so its manifest mutation cannot be
            # masked by a sibling clause - the round-11 floor-params lesson):
            "1121",  # two-distinct-digits only
            "11123",  # three-same-digit-run only
            "1122",
            # repeating-pair ONLY: every length-4+ pair also has <=2 distinct
            # digits, so a length-3 alternation is the sole shape that reaches
            # the pair rule alone (the mutation runner proved "1212" masked).
            "121",
        ],
    )
    def test_placeholder_shaped_digit_content_is_weak(self, value: str) -> None:
        assert postcode_is_weak_anchor(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "11111 USA",
            "USA 11111",
            "PO Box 11111",
            "Head Office 99999",
            "11111 Berlin",
            "Berlin 11111",
        ],
    )
    def test_weakness_is_position_independent(self, value: str) -> None:
        # Leading text bypassed the round-11 start-anchored regex entirely.
        assert postcode_is_weak_anchor(value) is True

    @pytest.mark.parametrize(
        "value",
        ["E1 1EE", "SW1A 1AA", "E8 1AA", "75008", "60601", "D02X285"],
    )
    def test_real_postcodes_stay_strong(self, value: str) -> None:
        assert postcode_is_weak_anchor(value) is False

    def test_ordinal_shaped_extraction_is_weak_regardless_of_digit_content(self) -> None:
        # SPEC CHANGE, round 13 (pure-logic execution audit): "B33 8TH" was
        # pinned STRONG through round 12 - correct for digit content alone
        # ("338" has real entropy), wrong once the execution audit showed
        # WHY: "Studio B2, 1st" mints the SAME key as the genuine Birmingham
        # client at "B2 1ST" with a STRONG anchor, because the unit word
        # STUDIO was never on `_UNIT_BEFORE`'s list. Chasing every gym-
        # industry unit word one at a time repeats the exact mistake rounds
        # 9-12 made with the ordinal candidate rule itself. The class fix:
        # an ordinal-shaped extraction is, by `normalize_postcode`'s OWN
        # candidate rules, ambiguous - it survived only because nothing
        # recognisable disqualified it - so it can never certify a merge
        # alone. Cost: real ordinal-shaped UK postcodes (~1% of the format)
        # now also require the signer bar, the same safe-direction trade
        # every other weak-anchor clause makes.
        assert postcode_is_weak_anchor("B33 8TH") is True
        # A same-signer returning client at a genuine ordinal-shaped
        # postcode still links (bar 3 stays satisfiable, just required).
        assert postcode_is_weak_anchor("E8 1ST") is True
        # Non-ordinal real postcodes are unaffected.
        assert postcode_is_weak_anchor("E8 1AA") is False


class TestContactNameShapes:
    """Round 12 P1.1: bar 3 rejects by SHAPE, not enumeration.

    The 12-entry denylist was the enumeration antipattern this module deleted
    everywhere else, and ('Club','Manager') walked straight past it - round 2's
    franchise conflation re-entered through the field introduced to replace the
    missing postcode. Role titles, department shapes and first==last echoes are
    not people; identical on both rows they must refuse, not corroborate.
    """

    @pytest.mark.parametrize(
        ("first", "last"),
        [
            ("Club", "Manager"),
            ("The", "Manager"),
            ("General", "Manager"),
            ("Gym", "Manager"),
            ("Duty", "Manager"),
            ("Managing", "Director"),
            ("Company", "Secretary"),
            ("Business", "Owner"),
            ("Franchise", "Owner"),
            ("Head Office", "Manager"),
            ("Front", "Desk"),
            ("N/A", "N/A"),
            ("NA", "NA"),
            ("Unknown", "Unknown"),
            ("TBC", "TBC"),
            ("John", "Doe"),
        ],
    )
    def test_role_titles_and_placeholders_never_corroborate(self, first: str, last: str) -> None:
        assert contact_name_agrees(first, last, first, last) is False

    @pytest.mark.parametrize(
        ("first", "last"),
        [("Sarah", "Connor"), ("Dana", "Reed"), ("Marcus", "Webb"), ("José", "García")],
    )
    def test_real_signers_still_corroborate(self, first: str, last: str) -> None:
        assert contact_name_agrees(first, last, first, last) is True

    def test_multi_token_part_refuses_toward_split(self) -> None:
        # A part carrying 2+ tokens is department-shaped ('Head Office'). The
        # cost is that a double-barrelled given name also refuses - the SPLIT
        # direction, documented as the accepted trade. The param deliberately
        # contains NO role noun, so only the multi-token rule refuses it and
        # its manifest mutation cannot be masked by the role-noun guard.
        assert contact_name_agrees("Mary Jane", "Smith", "Mary Jane", "Smith") is False


class TestNonLatinScripts:
    """Round 12 P1.3: non-Latin letters are FOLDED INTO the key, not deleted.

    The ASCII-only tokenizer deleted what NFKD could not fold, so every
    Cyrillic/Greek/CJK business collapsed onto its Latin remnant ('gym') and
    bar 1 was structurally inert for the whole cohort - while a fully non-Latin
    name NULL-keyed on every signing (silent split). The INT PandaDoc account
    is live, which is what makes this cohort reachable.
    """

    def test_distinct_cyrillic_names_diverge(self) -> None:
        assert names_materially_diverge("Титан Gym", "Атлант Gym") is True

    def test_cyrillic_name_contributes_to_the_stem(self) -> None:
        assert normalize_name("Титан Gym") != normalize_name("Атлант Gym")
        assert normalize_name("Титан Gym") != "gym"

    def test_fully_non_latin_name_still_keys(self) -> None:
        assert compute_identity_key("Спортзал Титан", "E8 1AA") is not None

    @pytest.mark.parametrize(
        ("name_a", "name_b"),
        [("Fitness Παλλάς", "Fitness Ολύμπια"), ("ジム Fitness", "アトラス Fitness")],
    )
    def test_greek_and_cjk_names_diverge(self, name_a: str, name_b: str) -> None:
        assert names_materially_diverge(name_a, name_b) is True

    def test_latin_names_are_unaffected_by_the_widened_tokenizer(self) -> None:
        assert normalize_name("The F45 Training Ltd") == "f45training"
        assert normalize_name("Café Gym") == normalize_name("Cafe Gym")


class TestPlaceholderShapedNames:
    """Round 12, P2: the unidentifiable-can-never-merge invariant was an
    exact-string compare against one constant, so "N/A" keyed as `na|...` and
    a case variant of the placeholder itself keyed normally. Placeholders are
    a SHAPE, recognised on the normalized stem.
    """

    def test_placeholder_business_name_falls_through_to_the_legal_entity(self) -> None:
        assert identity_name("N/A", "Real Gym Ltd") == "Real Gym Ltd"

    @pytest.mark.parametrize("value", ["TBC", "tbd", "n/a", "None", "Not Applicable", "TEST"])
    def test_placeholder_shaped_name_yields_no_identity(self, value: str) -> None:
        assert identity_name(value, None) is None
        assert identity_name(None, value) is None

    def test_case_and_space_variants_of_the_constant_are_rejected(self) -> None:
        assert identity_name(None, " UNKNOWN - NEEDS REVIEW ") is None

    def test_placeholder_shaped_name_cannot_mint_a_key(self) -> None:
        assert compute_identity_key(identity_name("N/A", None), "E8 1AA") is None


class TestPhoneCountryCodeConflict:
    """Round 12, P2: the 9-digit tail drops the country code, so two real
    numbers from different countries could corroborate each other."""

    def test_malta_and_us_numbers_sharing_a_tail_do_not_agree(self) -> None:
        # The premise, computed not asserted in prose: both numbers really do
        # share the 9-digit tail - which is exactly why the tail alone must
        # not decide.
        assert normalize_phone("+356 2912 3456") == "629123456"
        assert normalize_phone("+1 (562) 912-3456") == "629123456"
        assert (
            corroborating_signal_agrees(phone_a="+356 2912 3456", phone_b="+1 (562) 912-3456")
            is False
        )

    def test_country_code_and_trunk_zero_forms_still_agree(self) -> None:
        # The reason the tail exists at all: one business re-typing its own
        # number with and without the country code is not two businesses.
        assert (
            corroborating_signal_agrees(phone_a="+44 7700 900123", phone_b="07700 900123") is True
        )

    def test_bare_national_form_agrees_with_the_international_form(self) -> None:
        assert corroborating_signal_agrees(phone_a="7700 900123", phone_b="+44 7700 900123") is True


class TestPhoneRealNumberStructure:
    """Round 13: the tail-suffix heuristic replaced by real per-country
    numbering-plan structure (`phonenumbers`) - closing the two residuals
    the pure-logic execution audit found (one merge-direction, one split-
    direction), neither closable by tightening the SAME digit-shape rule.
    """

    def test_cross_country_digit_coincidence_does_not_corroborate(self) -> None:
        # The construction that survived round 12's own fix: Spain and
        # Italy share a digit-identical national significant number once
        # Spain's country code is stripped away, and no digit-suffix rule
        # can tell that apart from a genuine national-prefix relationship.
        # Italy is deliberately NOT in `_PHONE_CANDIDATE_REGIONS` (it is not
        # a confirmed Bullet market), so the non-"+" Italian-shaped number
        # produces no candidate for the Spanish reading to coincide with.
        spain_full = "34655512345"
        italy_full = "0655512345"
        # The premise, computed not asserted in prose: stripping Spain's
        # "34" leaves exactly Italy's own national significant number.
        assert spain_full[len("34") :] == italy_full.lstrip("0")
        assert (
            corroborating_signal_agrees(phone_a="+34 655 512 345", phone_b="06 5551 2345") is False
        )

    @pytest.mark.parametrize(
        ("with_cc", "national"),
        [
            ("+45 32 12 34 56", "32 12 34 56"),  # Denmark
            ("+47 22 12 34 56", "22 12 34 56"),  # Norway
            ("+65 6123 4567", "6123 4567"),  # Singapore
            ("+352 621 123 456", "621 123 456"),  # Luxembourg
        ],
    )
    def test_short_nsn_countries_now_corroborate_across_forms(
        self, with_cc: str, national: str
    ) -> None:
        # These 8-digit-NSN countries used to fail PERMANENTLY: the fixed
        # 9-digit tail ate one digit of the NSN the moment a country code
        # was prepended, so the two forms' tails never matched by length,
        # let alone content. A genuine returning client here got a
        # duplicate sub-account on every re-signing, silently - the exact
        # class round 5 fixed for UK landlines, unfixed for this cohort
        # until now.
        assert corroborating_signal_agrees(phone_a=with_cc, phone_b=national) is True

    def test_a_country_outside_the_region_list_cannot_self_corroborate_without_a_plus(
        self,
    ) -> None:
        # Documented scope boundary, not a silent gap: Italy is not in
        # `_PHONE_CANDIDATE_REGIONS` (not a confirmed Bullet market), so a
        # GENUINE Italian number written once with "+39" and once without
        # cannot be resolved to the same reading - a missed link, the SAFE
        # direction, not a wrong one. (Two IDENTICAL non-"+" strings would
        # trivially self-match on whichever candidate region accepts them,
        # which is why this test uses the country-code-present/absent pair
        # rather than comparing the bare string to itself.)
        assert (
            corroborating_signal_agrees(phone_a="+39 06 5551 2345", phone_b="06 5551 2345") is False
        )

    def test_placeholder_shaped_numbers_never_reach_real_number_interpretation(
        self,
    ) -> None:
        # The filler pre-filter runs BEFORE any phonenumbers parsing, so a
        # placeholder that happens to be digit-count-plausible for some
        # real country never gets the chance to corroborate on that basis.
        assert corroborating_signal_agrees(phone_a="1234567890", phone_b="1234567890") is False


class TestAddressBoundaryShifts:
    """Round 12, P2: `normalize_name`'s no-separator concatenation erased
    token boundaries, so bar 4 abstained on genuinely different addresses -
    chained with an ordinal-hijacked key, that removed the last surviving bar.
    """

    def test_boundary_shifted_addresses_diverge(self) -> None:
        assert addresses_materially_diverge("Unit 1, 23 Mill Road", "Unit 12, 3 Mill Road") is True

    def test_spacing_and_punctuation_still_do_not_diverge(self) -> None:
        assert (
            addresses_materially_diverge("12 Mare Street,  London", "12 Mare Street, London")
            is False
        )


class TestOrdinalHijackViaUnenumeratedUnitWord:
    """Round 13 (pure-logic execution audit) - the worst finding.

    "Studio B2, 1st" carries NO real postcode, yet it minted the exact key
    of the genuine Birmingham client at "B2 1ST" with a STRONG anchor: bar
    3 was waived because `_UNIT_BEFORE`'s original list was office-generic
    and never enumerated STUDIO - the one word this agency's own gym data
    is full of. Two closures, tested independently: `_UNIT_BEFORE` now
    lists the gym-industry words directly (defense in depth), and
    `postcode_is_weak_anchor` treats every ordinal-shaped extraction as
    weak regardless of which words surround it (the class fix - it would
    have caught this even with STUDIO still unenumerated).
    """

    def test_studio_prefix_no_longer_mints_the_ordinal_key(self) -> None:
        assert normalize_postcode("Studio B2, 1st") != "B21ST"

    @pytest.mark.parametrize("prefix", ["Gym", "Bay", "Pod", "Kiosk", "Cabin", "Stall", "Studio"])
    def test_gym_industry_unit_words_do_not_mint_a_postcode_key(self, prefix: str) -> None:
        assert normalize_postcode(f"{prefix} B2 1st") != "B21ST"

    def test_the_class_fix_holds_even_for_an_unenumerated_word(self) -> None:
        # The defensive part of the fix is necessarily open-ended - a word
        # nobody thought to add is still a gap in `_UNIT_BEFORE` alone. This
        # is the CLASS closure: even if the value below used a unit word not
        # on that list, the resulting key is ordinal-shaped and therefore a
        # WEAK anchor unconditionally, so bar 3 stays required and no merge
        # can happen on this signal alone.
        key = normalize_postcode("Annex B2, 1st")  # "Annex" is not enumerated
        if key:
            assert postcode_is_weak_anchor(key) is True

    def test_end_to_end_bar_3_stays_required(self) -> None:
        # The full corroboration chain the reviewer traced: same brand, same
        # shared phone, a hijacked postcode - the signer bar must still be
        # the deciding factor, not waived by a false-strong anchor.
        key_real = compute_identity_key("Anytime Fitness", "B2 1ST")
        key_hijack = compute_identity_key("Anytime Fitness", "Studio B2, 1st")
        assert key_real != key_hijack or key_hijack is None
        if key_real is not None:
            anchor_postcode = key_real.partition("|")[2]
            assert postcode_is_weak_anchor(anchor_postcode) is True


class TestContactNameShapeCheckIsCommutative:
    """Round 13 (pure-logic execution audit): the shape checks ran on side A
    only, so a role-noun-shaped part FUSED into one token ("ClubManager")
    evaded both the role-noun and multi-token rules on that side, while the
    equality check at the end still matched it against a spelled-out "Club
    Manager" on the OTHER side - a real bug, and one that made the function
    depend on ARGUMENT ORDER, which a symmetric corroboration signal must
    never do.
    """

    def test_fused_role_title_on_either_side_refuses(self) -> None:
        assert contact_name_agrees("ClubManager", "Jones", "Club Manager", "Jones") is False
        assert contact_name_agrees("Club Manager", "Jones", "ClubManager", "Jones") is False

    def test_result_is_order_independent(self) -> None:
        a = contact_name_agrees("ClubManager", "Jones", "Club Manager", "Jones")
        b = contact_name_agrees("Club Manager", "Jones", "ClubManager", "Jones")
        assert a == b

    def test_real_signers_still_agree_both_orders(self) -> None:
        a = contact_name_agrees("Sarah", "Connor", "Sarah", "Connor")
        b = contact_name_agrees("Sarah", "Connor", "Sarah", "Connor")
        assert a is b is True


class TestPostcodeDuplicateCandidateNotAmbiguous:
    """Round 13 (pure-logic execution audit): candidates were counted by
    OCCURRENCE, not distinct value - a copy-pasted duplicate ("E8 1AA E8
    1AA") looked like "two genuine postcodes, no way to pick" and NULL-keyed
    an unambiguous value.
    """

    def test_duplicated_postcode_still_keys(self) -> None:
        assert normalize_postcode("E8 1AA E8 1AA") == "E81AA"

    def test_duplicated_postcode_with_punctuation_still_keys(self) -> None:
        assert normalize_postcode("E8 1AA, E8 1AA") == "E81AA"

    def test_genuinely_different_postcodes_still_split(self) -> None:
        # The real ambiguity case is unaffected by the dedupe.
        assert normalize_postcode("E8 1AA / N1 4AB") == ""


class TestSlashSeparatedPostcodes:
    """Round 13 (pure-logic execution audit): the separator class recognised
    whitespace/comma/period/hyphen but not a slash, so a slash-separated
    postcode fell through to the flat/token path and lost the town-invariant
    ("London E8/1AA" keyed as the WHOLE string, town included, instead of
    "E81AA")."""

    def test_slash_separated_postcode_matches_the_space_form(self) -> None:
        assert normalize_postcode("E8/1AA") == normalize_postcode("E8 1AA") == "E81AA"

    def test_town_prefixed_slash_postcode_matches_the_bare_form(self) -> None:
        assert normalize_postcode("London E8/1AA") == normalize_postcode("E8 1AA") == "E81AA"

    def test_slash_after_a_unit_designator_is_still_recognised(self) -> None:
        # "Unit/B2 1st" - the unit-before guard's separator class gap.
        assert normalize_postcode("Unit/B2 1st") != normalize_postcode("B2 1ST")


class TestFitnessIndustryRoleTitles:
    """Round 13 (pure-logic execution audit): `_ROLE_NOUNS` was office-generic
    and missed the titles this agency's OWN signer fields are full of - a
    gap of the exact same class round 12's fix closed for office titles.
    Each param is its own sole-kill target so a partial revert of the
    fitness-title additions is caught by name, not just by count.
    """

    @pytest.mark.parametrize(
        ("first", "last"),
        [
            ("Head", "Coach"),
            ("Personal", "Trainer"),
            ("Fitness", "Instructor"),
            ("Studio", "Lead"),
            ("Gym", "Lead"),
            ("Sales", "Rep"),
            # SOLE-KILL pairs: "personal" and "studio" paired with a non-role
            # last name, so removing either alone (and not any co-occurring
            # role noun from the pairs above) still fails these two.
            ("Personal", "Jones"),
            ("Studio", "Jones"),
            ("Duty", "Manager"),
            ("Membership", "Advisor"),
        ],
    )
    def test_fitness_titles_never_corroborate(self, first: str, last: str) -> None:
        assert contact_name_agrees(first, last, first, last) is False
