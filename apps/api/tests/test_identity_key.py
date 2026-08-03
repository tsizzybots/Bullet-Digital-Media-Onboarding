"""Unit tests for the returning-client identity key (S1-26c).

Pure functions, no DB - these pin the normalization rules the whole
returning-client match depends on.
"""

from __future__ import annotations

import pytest

from bullet_api.worker.identity_key import (
    LEGAL_ENTITY_PLACEHOLDER,
    compute_identity_key,
    corroborating_signal_agrees,
    identity_name,
    names_materially_diverge,
    normalize_address,
    normalize_name,
    normalize_phone,
    normalize_postcode,
)


class TestNormalizeName:
    def test_lowercases_and_strips_punctuation_whitespace(self) -> None:
        assert normalize_name("BFT  Hackney!") == "bfthackney"

    def test_drops_leading_the(self) -> None:
        # Otherwise "the gy" would eat the whole 6-char budget.
        assert normalize_name("The Gym Group") == "gymgroup"

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
        assert compute_identity_key("BFT", "E8 1AA") == "bft|E81AA"

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
        assert compute_identity_key("BFT Hackney", "E8 1AA") == compute_identity_key(
            "bft  hackney!", "e8-1aa"
        )

    def test_ltd_suffix_and_case_do_not_split_one_business(self) -> None:
        assert compute_identity_key("Sample Gym Ltd", "E8 1AA") == compute_identity_key(
            "SAMPLE GYM LIMITED", "E8   1AA"
        )

    def test_franchise_separation_by_postcode(self) -> None:
        # Same brand, different location -> different key.
        a = compute_identity_key("BFT Hackney", "E8 1AA")
        b = compute_identity_key("BFT East Croydon", "CR0 1AA")
        assert a != b


class TestNamesMateriallyDiverge:
    def test_same_normalized_name_does_not_diverge(self) -> None:
        assert names_materially_diverge("BFT Hackney", "bft  hackney!") is False

    def test_suffix_only_difference_does_not_diverge(self) -> None:
        assert names_materially_diverge("Foobar Ltd", "Foobar Limited") is False

    def test_prefix_collision_diverges(self) -> None:
        # Same first-6 ("fitnes") but different full names -> divergent.
        assert names_materially_diverge("Fitness First", "Fitness Studio") is True


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


class TestNormalizeAddress:
    def test_punctuation_and_case_ignored(self) -> None:
        assert normalize_address("123 High Street,  London") == normalize_address(
            "123 high street london"
        )

    def test_different_addresses_do_not_agree(self) -> None:
        assert normalize_address("123 High Street") != normalize_address("456 High Street")

    @pytest.mark.parametrize("value", [None, "", "   ", "---"])
    def test_blank_is_unusable(self, value: str | None) -> None:
        assert normalize_address(value) == ""


class TestCorroboratingSignalAgrees:
    def test_matching_phone_corroborates(self) -> None:
        assert (
            corroborating_signal_agrees(
                phone_a="+44 7700 900123",
                address_a=None,
                phone_b="07700 900123",
                address_b=None,
            )
            is True
        )

    def test_matching_address_corroborates_when_phone_is_absent(self) -> None:
        assert (
            corroborating_signal_agrees(
                phone_a=None,
                address_a="123 High Street",
                phone_b=None,
                address_b="123 High Street,",
            )
            is True
        )

    def test_address_corroborates_even_when_phones_differ(self) -> None:
        # Either signal satisfies it - a changed contact number should not veto
        # an otherwise solid match on the premises.
        assert (
            corroborating_signal_agrees(
                phone_a="+447700900123",
                address_a="123 High Street",
                phone_b="+447700900999",
                address_b="123 High Street",
            )
            is True
        )

    def test_differing_phone_with_no_address_does_not_corroborate(self) -> None:
        assert (
            corroborating_signal_agrees(
                phone_a="+447700900123",
                address_a=None,
                phone_b="+447700900999",
                address_b=None,
            )
            is False
        )

    def test_absence_is_not_agreement(self) -> None:
        # THE point of finding 4: when only name and postcode agree - nothing
        # else known on either side - that is not proof of one business, so it
        # must NOT corroborate. Two nulls are not a match.
        assert (
            corroborating_signal_agrees(phone_a=None, address_a=None, phone_b=None, address_b=None)
            is False
        )

    def test_signal_present_on_only_one_side_does_not_corroborate(self) -> None:
        assert (
            corroborating_signal_agrees(
                phone_a="+447700900123", address_a=None, phone_b=None, address_b=None
            )
            is False
        )
        assert (
            corroborating_signal_agrees(
                phone_a=None, address_a="123 High Street", phone_b=None, address_b=None
            )
            is False
        )
