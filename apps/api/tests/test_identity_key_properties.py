"""Property tests for the identity-key normalizers.

WHY THIS FILE EXISTS. `normalize_postcode` produced a blocking review finding in
THREE CONSECUTIVE ROUNDS (4, 5, 6). Each round fixed the reported example and
broke an adjacent one:

    round 4  sort tokens                  -> order fixed, SEPARATOR broken
    round 5  drop alphabetic tokens       -> separator fixed for digit-bearing
                                             tokens, broken for alpha-adjacent
                                             ones, AND a lossy COLLIDE added
                                             ("1011 AB" == "1011 CD")

The example-based tests could not see any of it. Round 6 diagnosed exactly why:
of the nine pairs `TestPostcodeFormInvariance` parametrizes, only ONE enters the
branch round 5 added, and that pair varies on the one axis where the branch is
correct. The test was standing next to the branch it did not enter.

That is not a care problem, it is a METHOD problem: the examples are chosen by
whoever wrote the fix, who by construction does not know which axis they missed.
So this file tests the INVARIANTS over GENERATED inputs instead. It earned its
place immediately - it found a split that 27 hand-picked examples did not
("AB12CD" matched the UK branch, "AB 12 CD" fell through to the token path).

A normalizer change that does not update this file is not finished.
"""

from __future__ import annotations

import itertools
import random
import re

import pytest

from bullet_api.worker.identity_key import compute_identity_key, normalize_postcode

# Separator forms a human actually types between parts of a postcode.
SEPARATORS = ["", " ", "  ", "-", ",", ", ", ".", " - "]

# Real postal shapes, written WITHOUT separators so each test can re-insert
# every separator form and assert the key never moves. Mix of UK, NL, CH, MT,
# CA, IE, DE, US, and the postcode-plus-town form.
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
]

# Order is STRUCTURAL in a UK postcode - permuting it yields a different value,
# not a different spelling - so order-independence is asserted only for the
# international token path, which is all round 4 ever asked for.
INTERNATIONAL_CORES = ["1011AB", "CH8001", "VLT1117", "75008PARIS", "2000AB"]

_TOKEN = re.compile(r"[A-Z]+|[0-9]+")


def _parts(core: str) -> list[str]:
    return _TOKEN.findall(core)


class TestPostcodeSeparatorIndependence:
    """INVARIANT 1: separators carry no meaning.

    Broken in rounds 4, 5 AND 6. One business writing "1011 AB" on one document
    and "1011AB" on the next must reach ONE key, or the returning-client check
    finds no candidate and silently provisions a duplicate sub-account.
    """

    @pytest.mark.parametrize("core", POSTCODE_CORES)
    def test_every_separator_form_of_one_postcode_shares_one_key(self, core: str) -> None:
        parts = _parts(core)
        if len(parts) < 2:
            pytest.skip("single-token postcode has no separator boundary")
        keys = {normalize_postcode(sep.join(parts)) for sep in SEPARATORS}
        assert len(keys) == 1, (
            f"{core!r} keys {len(keys)} different ways across separators: "
            f"{ {sep.join(parts): normalize_postcode(sep.join(parts)) for sep in SEPARATORS} }"
        )
        assert keys != {""}


class TestPostcodeOrderIndependence:
    """INVARIANT 2: token order carries no meaning on the international path.

    Round 4's requirement, and the only thing it asked for: "75008 Paris" and
    "Paris 75008" are one French business entered two ways.
    """

    @pytest.mark.parametrize("core", INTERNATIONAL_CORES)
    def test_every_token_order_shares_one_key(self, core: str) -> None:
        keys = {normalize_postcode(" ".join(p)) for p in itertools.permutations(_parts(core))}
        assert len(keys) == 1, f"{core!r} keys {len(keys)} ways across token order: {keys}"


class TestPostcodeIsNotLossy:
    """INVARIANT 3: two DIFFERENT postcodes must never share a key.

    Round 5 broke this by dropping alphabetic tokens: "1011 AB" and "1011 CD"
    are two different Amsterdam postcodes that collapsed to "1011", so two sites
    of one brand could FALSE-MERGE where round 4 kept them apart. A split costs
    a spare sub-account; a collide can put one client's assets in another's
    account, so this is the invariant with the expensive failure.
    """

    def test_generated_postcodes_do_not_collide(self) -> None:
        rng = random.Random(20260825)
        seen: dict[str, str] = {}
        collisions: list[tuple[str, str, str]] = []
        for _ in range(5000):
            digits = "".join(rng.choice("0123456789") for _ in range(rng.randint(3, 5)))
            letters = "".join(
                rng.choice("ABCDEFGHJKLMNPQRSTVWXYZ") for _ in range(rng.randint(1, 3))
            )
            value = f"{digits} {letters}"
            key = normalize_postcode(value)
            if not key:
                continue
            canonical = "".join(sorted(re.sub(r"[^A-Z0-9]", "", value.upper())))
            if key in seen and seen[key] != canonical:
                collisions.append((value, seen[key], canonical))
            seen[key] = canonical
        assert not collisions, f"distinct postcodes sharing a key: {collisions[:5]}"

    @pytest.mark.parametrize(
        ("a", "b"),
        [("1011 AB", "1011 CD"), ("VLT 1117", "VLT 1234"), ("75008 Paris", "75009 Paris")],
    )
    def test_named_near_misses_stay_apart(self, a: str, b: str) -> None:
        assert normalize_postcode(a) != normalize_postcode(b)


class TestPostcodeRoundSixCases:
    """The exact pairs review round 6 asked for, pinned by name.

    Kept alongside the generated properties rather than instead of them: these
    prove the reported bug is closed, the properties prove the CLASS is.
    """

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("1011 AB", "1011AB"),
            ("CH 8001", "CH-8001"),
            ("VLT 1117", "VLT1117"),
            ("75008 Paris", "Paris-75008"),
            ("94107", "94107-1234"),
        ],
    )
    def test_reported_splits_are_closed(self, a: str, b: str) -> None:
        assert normalize_postcode(a) == normalize_postcode(b) != ""

    def test_reported_collision_is_closed(self) -> None:
        assert normalize_postcode("1011 AB") != normalize_postcode("1011 CD")

    @pytest.mark.parametrize("form", ["1011 AB", "1011AB", "1011-AB", "1011, AB", "1011.AB"])
    def test_every_written_form_of_one_nl_postcode_agrees(self, form: str) -> None:
        """The tokenizer's own documented example, pinned literally.

        `_POSTCODE_TOKEN`'s comment names "1011 AB", "1011AB" and "1011-AB" as
        the forms it exists to collapse; a comment naming example data with no
        test containing it is exactly what the gate's G1 check exists to catch.
        """
        assert normalize_postcode(form) == normalize_postcode("1011 AB") != ""

    def test_zip_plus_four_does_not_concatenate_into_a_nine_digit_key(self) -> None:
        """Round 4's sort produced "123494107" here and round 5's drop produced
        "941071234" - both nine-digit concatenations that split a US business
        from its own ZIP5. Neither may come back."""
        key = normalize_postcode("94107-1234")
        assert key == normalize_postcode("94107") == "94107"
        assert key not in {"941071234", "123494107"}


class TestIdentityKeyInheritsTheInvariants:
    """The key is what actually gates the merge, so assert at that level too.

    A normalizer that is form-invariant but a `compute_identity_key` that is not
    would leave the real defect in place while the unit tests looked green.
    """

    @pytest.mark.parametrize("core", POSTCODE_CORES)
    def test_one_business_reaches_one_key_across_separator_forms(self, core: str) -> None:
        parts = _parts(core)
        if len(parts) < 2:
            pytest.skip("single-token postcode has no separator boundary")
        keys = {compute_identity_key("Sample Gym", sep.join(parts)) for sep in SEPARATORS}
        assert len(keys) == 1, f"{core!r} produced {len(keys)} identity keys: {keys}"
        assert None not in keys
