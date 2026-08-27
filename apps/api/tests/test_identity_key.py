"""Unit tests for the returning-client identity key (S1-26c).

Pure functions, no DB - these pin the normalization rules the whole
returning-client match depends on.
"""

from __future__ import annotations

import pytest

from bullet_api.worker.identity_key import (
    LEGAL_ENTITY_PLACEHOLDER,
    addresses_materially_diverge,
    compute_identity_key,
    contact_name_agrees,
    corroborating_signal_agrees,
    identity_name,
    names_materially_diverge,
    normalize_name,
    normalize_phone,
    normalize_postcode,
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

    @pytest.mark.parametrize("value", ["99999", "XXXX", "1111", "AAAA"])
    def test_single_repeated_character_rejected(self, value: str) -> None:
        assert normalize_postcode(value) == ""

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
