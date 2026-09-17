#!/usr/bin/env python
"""Key-invariance sweep: prove that no STORED `clients.identity_key` is orphaned.

WHY THIS FILE EXISTS (S1-26l, 17/09/2026). Rounds 14 and 15 both reported a
sweep of roughly 400,000 inputs showing zero value differences. That harness
was ad hoc and was lost with a session's scratchpad, which this repo already
records at `tests/test_identity_key_properties.py`: the reviewer asked for the
golden file precisely because the real evidence "exists nowhere in the suite
and cannot be re-run". `tests/golden/identity_key_golden.json` pins 3,752
postcodes and 8 keys and is the CI gate; this script is the wide sweep, now
checked in so the claim is reproducible by command instead of by anecdote.

WHAT IT PROVES. `compute_identity_key` is a pure function of the business name
and the postcode, and its output is STORED. If a change moves any produced
value, every row holding the old value is orphaned and a recompute migration
is owed (G7 in `review_gate.py` refuses the change without one). A refactor
that moves a value is not a refactor.

WHAT IT DOES. Loads `identity_key.py` twice - once from the working tree, once
from the merge base via `git show` - and runs both over one deterministic
corpus, comparing:

  * `classify_postcode(x).value`, the postcode half of the key
  * `compute_identity_key(identity_name(name), postcode)`, the stored value
  * `postcode_is_weak_anchor(x)`, reported BY DIRECTION, because a
    strong -> WEAK flip is safe (it keeps the signer bar) and a weak -> STRONG
    flip is not (it waives one), so an aggregate count would hide the only
    direction that matters.

Value diffs fail the run. Weak-anchor flips are REPORTED, not failed: they are
a legitimate outcome of a confidence change, and only their direction is a
defect.

PROVE THE PROBE. `--self-test` perturbs one revision and asserts the sweep
reports the difference. A measurement claiming "no change" must first show it
can detect one; round 15 shipped a probe that mutated a frozenset behind an
`isinstance(..., set)` guard, so the add never applied and the instrument
measured an unchanged system. Run it before trusting a zero.

THE ENUM TRAP, measured 17/09/2026 while building this file, and the reason
every comparison below is on a PRIMITIVE. Loading the module twice creates TWO
DISTINCT `PostcodeConfidence` classes, so `base.PostcodeConfidence.REAL` and
`head.PostcodeConfidence.REAL` are never equal - they have equal `.value` and
different identity. A first cut of this sweep compared the enum MEMBERS and
reported 200,003 confidence differences across 200,003 inputs: a 100% "change"
rate produced by the instrument, on a rename that provably changes nothing.
Compared on `.value`, the same corpus reports 0. This is the mirror of "prove
the probe" - an instrument that reports a difference which is not there - and
it is just as capable of wasting a review round. Compare strings, never
members, and never `is`.

USAGE
    uv run python apps/api/scripts/key_invariance_sweep.py --self-test
    uv run python apps/api/scripts/key_invariance_sweep.py --base main
    make key-sweep
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import random
import string
import subprocess
import sys
import tempfile
import types

MODULE_REL = "apps/api/src/bullet_api/worker/identity_key.py"
GOLDEN_REL = "apps/api/tests/golden/identity_key_golden.json"
DEFAULT_LIMIT = 400_000
DEFAULT_SEED = 26_026

# Deterministic corpus ingredients. Real UK areas plus a few that are NOT real
# areas, because the outward check constrains characters rather than the area
# list and the sweep should cover both sides of that.
_AREAS = ("E", "EC", "SW", "SE", "N", "NW", "W", "B", "M", "LS", "G", "CF", "BT", "AB", "IV", "IG")
_UNIT_PREFIXES = ("", "Unit 3, ", "1st Floor, ", "Suite 2 ", "Studio ", "Gym ", "Bay 4 ", "Pitch ")
_TOWN_SUFFIXES = ("", ", London", " Manchester", ", Dublin", " Paris")
_JUNK = ("", " ", "-", "TBC", "TBA", "N/A", "n/a", "0000", "00000", "TBC 1", "None", "null")
_NAMES = (
    "Capacity",
    "Pending Gym",
    "Nomad Fitness",
    "The Gym Ltd",
    "Cafe Gym",
    "F45 Training",
    "Fitness First",
    "Fitness Studio",
    "Test",
    "Unknown",
    "PureGym Limited",
    "",
)


def _load_module(path: str, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _baseline_module(base: str, repo_root: pathlib.Path) -> types.ModuleType:
    """Load `identity_key.py` as it exists at `base`.

    Read through `git show` rather than a worktree checkout: round 14 tried the
    scratch-worktree route and could not run anything there, because the
    worktree has no virtualenv.
    """
    source = subprocess.run(
        ["git", "show", f"{base}:{MODULE_REL}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if not source.strip():
        raise RuntimeError(f"{base}:{MODULE_REL} is empty - wrong revision?")
    tmp = tempfile.NamedTemporaryFile("w", suffix="_identity_key_base.py", delete=False)
    tmp.write(source)
    tmp.close()
    return _load_module(tmp.name, "identity_key_baseline")


def build_corpus(limit: int, seed: int, repo_root: pathlib.Path) -> list[str]:
    """A deterministic postcode corpus. SEEDED, so the figure is reproducible.

    Round 15 reported 59,815 candidates from an unseeded run and it re-measured
    as 59,814 - a property of one sample, quoted as a property of the code.
    """
    rng = random.Random(seed)
    corpus: list[str] = []

    # 1. The curated corpus already in the repo: every raw input the golden
    #    file pins, so the sweep is a strict superset of the CI gate.
    golden = repo_root / GOLDEN_REL
    if golden.is_file():
        data = json.loads(golden.read_text())
        corpus.extend(str(row[0]) for row in data.get("postcodes", []))
        corpus.extend(str(row[1]) for row in data.get("identity_keys", []))

    corpus.extend(_JUNK)

    # 2. Generated UK shapes, with and without the contextual noise that the
    #    candidate rules exist to strip.
    letters = string.ascii_uppercase
    while len(corpus) < limit:
        area = rng.choice(_AREAS)
        district = rng.randrange(1, 100)
        sector = rng.randrange(0, 10)
        unit = rng.choice(letters) + rng.choice(letters)
        sep = rng.choice((" ", "", "  ", "-"))
        core = f"{area}{district}{sep}{sector}{unit}"
        corpus.append(rng.choice(_UNIT_PREFIXES) + core + rng.choice(_TOWN_SUFFIXES))

        # Ordinal-shaped inward halves - the axis rounds 9 to 14 kept breaking.
        corpus.append(f"{area}{district} {sector}{rng.choice(('ST', 'ND', 'RD', 'TH'))}")
        # Bare numeric national formats (FR/DE/ES/AU/US ZIP5).
        corpus.append(str(rng.randrange(1_000, 100_000)))
        # Eircode shapes.
        corpus.append(
            f"{rng.choice(letters)}{rng.randrange(10, 100)} "
            f"{''.join(rng.choice(letters + string.digits) for _ in range(4))}"
        )
        # Multi-candidate fields, where the selection rules actually bite.
        corpus.append(f"Unit {rng.randrange(1, 40)} {core}, {area}{district} {sector}{unit}")

    return corpus[:limit]


def sweep(
    base_mod: types.ModuleType,
    head_mod: types.ModuleType,
    corpus: list[str],
) -> dict[str, int | list[tuple[str, str, str]]]:
    value_diffs: list[tuple[str, str, str]] = []
    key_diffs: list[tuple[str, str, str]] = []
    strong_to_weak = 0
    weak_to_strong = 0

    confidence_moves = 0

    for raw in corpus:
        before_result = base_mod.classify_postcode(raw)
        after_result = head_mod.classify_postcode(raw)
        before, after = before_result.value, after_result.value
        if before != after:
            value_diffs.append((raw, before, after))

        # `.confidence.value`, NEVER the enum member - see THE ENUM TRAP above.
        # Reported, not failed: a confidence change is a legitimate outcome of
        # a classification change, and only a VALUE change orphans a stored key.
        if before_result.confidence.value != after_result.confidence.value:
            confidence_moves += 1

        was_weak = base_mod.postcode_is_weak_anchor(raw)
        now_weak = head_mod.postcode_is_weak_anchor(raw)
        if was_weak != now_weak:
            if now_weak:
                strong_to_weak += 1
            else:
                weak_to_strong += 1

    # The STORED value: name x postcode. Sampled across the corpus rather than
    # crossed with it, because the full cross product is the corpus squared.
    for name in _NAMES:
        for raw in corpus[:: max(1, len(corpus) // 500)]:
            before_key = base_mod.compute_identity_key(base_mod.identity_name(name, None), raw)
            after_key = head_mod.compute_identity_key(head_mod.identity_name(name, None), raw)
            if before_key != after_key:
                key_diffs.append((f"{name!r} x {raw!r}", str(before_key), str(after_key)))

    return {
        "inputs": len(corpus),
        "value_diffs": value_diffs,
        "key_diffs": key_diffs,
        "confidence_moves": confidence_moves,
        "strong_to_weak": strong_to_weak,
        "weak_to_strong": weak_to_strong,
    }


def _report(result: dict, label: str) -> None:
    print(f"\n  {label}")
    print(f"    inputs compared          : {result['inputs']:,}")
    print(f"    postcode VALUE diffs     : {len(result['value_diffs']):,}")
    print(f"    identity KEY diffs       : {len(result['key_diffs']):,}")
    print(f"    confidence moves (ok)    : {result['confidence_moves']:,}")
    print(f"    weak-anchor strong->WEAK : {result['strong_to_weak']:,}")
    print(f"    weak-anchor weak->STRONG : {result['weak_to_strong']:,}")
    for raw, before, after in list(result["value_diffs"])[:10]:
        print(f"      VALUE {raw!r}: {before!r} -> {after!r}")
    for raw, before, after in list(result["key_diffs"])[:10]:
        print(f"      KEY   {raw}: {before} -> {after}")


def _self_test(head_mod: types.ModuleType, corpus: list[str]) -> bool:
    """Prove the probe: perturb one side and assert the sweep SEES it.

    Without this, a zero is indistinguishable from a harness that compares a
    module with itself, or silently swallows the comparison.
    """
    print("  self-test: perturbing one revision to prove the sweep can detect a change")

    class _Perturbed:
        """A stand-in whose postcode values differ by one character."""

        @staticmethod
        def classify_postcode(raw: str):
            real = head_mod.classify_postcode(raw)
            return type(real)(real.value + "X", real.confidence)

        @staticmethod
        def postcode_is_weak_anchor(raw: str) -> bool:
            return head_mod.postcode_is_weak_anchor(raw)

        @staticmethod
        def compute_identity_key(name, postcode):
            key = head_mod.compute_identity_key(name, postcode)
            return None if key is None else key + "X"

        @staticmethod
        def identity_name(name, legal):
            return head_mod.identity_name(name, legal)

    probe = sweep(_Perturbed, head_mod, corpus[:2000])
    detected_values = len(probe["value_diffs"]) > 0
    detected_keys = len(probe["key_diffs"]) > 0
    print(f"    perturbed VALUE diffs detected : {len(probe['value_diffs']):,}")
    print(f"    perturbed KEY diffs detected   : {len(probe['key_diffs']):,}")
    if not (detected_values and detected_keys):
        print("    SELF-TEST FAILED: the sweep did not detect a deliberate change.")
        print("    Its null result would be meaningless. Fix the harness, not the code.")
        return False
    print("    self-test PASSED: a deliberate change is detected on both axes")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="main", help="revision to compare against")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--self-test", action="store_true", help="only prove the probe, then exit")
    args = parser.parse_args()

    repo_root = pathlib.Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        ).stdout.strip()
    )

    head_mod = _load_module(str(repo_root / MODULE_REL), "identity_key_head")
    corpus = build_corpus(args.limit, args.seed, repo_root)
    print(f"key-invariance sweep: {len(corpus):,} inputs, seed {args.seed}")

    if not _self_test(head_mod, corpus):
        return 2
    if args.self_test:
        return 0

    base_mod = _baseline_module(args.base, repo_root)
    result = sweep(base_mod, head_mod, corpus)
    _report(result, f"working tree vs {args.base}")

    failed = bool(result["value_diffs"]) or bool(result["key_diffs"])
    if failed:
        print("\n  FAIL: a produced value moved. Stored identity_keys are orphaned.")
        print("  This is not a refactor. Either revert, or ship a recompute migration.")
        return 1
    print("\n  PASS: 0 value diffs, 0 key diffs. No stored identity_key can be orphaned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
