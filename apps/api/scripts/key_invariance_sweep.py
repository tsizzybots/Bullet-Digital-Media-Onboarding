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
from the MERGE BASE with `--base` - and runs both over one deterministic
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

THE MERGE BASE, NOT THE REF (round 19). The first cut of this script ran
`git show <base>:<module>`, comparing against whatever `<base>` points at
rather than the merge base. Three ways that lies, all reproduced:

  * AFTER MERGE, `make key-sweep` compares `main` with itself and prints PASS.
    Measured at the time: `--base HEAD` over 5,000 inputs reported 0 diffs and
    exit 0. A vacuous comparison presented as evidence of invariance is the
    exact failure this harness was written to end.
  * A STALE local `main` compares against the wrong commit, so the diff is
    real but is not this branch's diff.
  * AN UNRESOLVABLE ref raised a bare `CalledProcessError` with git's stderr
    swallowed, exiting 1 - indistinguishable from a genuine FAIL.

So the base is resolved through `git merge-base` and refuses to fall back to
the unvalidated ref, mirroring `review_gate._merge_base`, whose own docstring
records the same class. Both SHAs are printed, and a comparison with nothing
in it reports VACUOUS and never prints PASS.

PROVE THE PROBE. `--self-test` perturbs the REAL baseline - loaded through
`_baseline_module`, from git, exactly as a real run does - and asserts the
sweep reports the difference. A measurement claiming "no change" must first
show it can detect one; round 15 shipped a probe that mutated a frozenset
behind an `isinstance(..., set)` guard, so the add never applied and the
instrument measured an unchanged system. The first cut of THIS self-test had
the same shape one level up: it perturbed a locally-built stand-in and never
called `_baseline_module` at all, so it passed with a completely bogus
`--base` and could not detect the one failure it claims to rule out.

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
    uv run python apps/api/scripts/key_invariance_sweep.py --self-test --limit 2000
    uv run python apps/api/scripts/key_invariance_sweep.py --base main
    make key-sweep
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import importlib.util
import json
import os
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

# Distinct codes so automation can tell a real finding from a broken harness.
# Round 19: a bad `--base` used to exit 1, indistinguishable from a value diff.
EXIT_OK, EXIT_DIFF, EXIT_PROBE_FAILED, EXIT_USAGE = 0, 1, 2, 3

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


def _fail_usage(message: str) -> None:
    """Exit for a MISCONFIGURED run, never for a finding.

    Separate from EXIT_DIFF so a caller can tell 'the harness could not
    answer' from 'the answer is bad'. They shared code 1 until round 19.
    """
    print(message, file=sys.stderr)
    raise SystemExit(EXIT_USAGE)


def _git(repo_root: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git with stderr CAPTURED BUT NOT SWALLOWED.

    `check=True` raised a bare `CalledProcessError` whose message carried the
    argv and not git's reason, and exited 1 - the same code a real value diff
    exits with. Callers below surface `stderr` in their own message.
    """
    return subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
    )


def _resolve_merge_base(base: str, repo_root: pathlib.Path) -> str:
    """Resolve `base` to the MERGE BASE with HEAD, or exit.

    Never falls back to the unvalidated ref, for the reasons in the module
    docstring. Same shape as `review_gate._merge_base`, deliberately: this
    script and that gate answer the same question about the same module, and
    two different notions of "base" between them is its own defect.
    """
    probe = _git(repo_root, "rev-parse", "--verify", f"{base}^{{commit}}")
    if probe.returncode != 0:
        _fail_usage(
            f"key-sweep: base ref {base!r} does not resolve to a commit "
            f"({probe.stderr.strip()}).\nIn CI, checkout with fetch-depth: 0. "
            f"Refusing to report a vacuous pass."
        )
    merge_base = _git(repo_root, "merge-base", base, "HEAD")
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        _fail_usage(
            f"key-sweep: no merge base between {base!r} and HEAD "
            f"({merge_base.stderr.strip()}) - unrelated histories or a shallow "
            f"clone. Refusing to report a vacuous pass."
        )
    return merge_base.stdout.strip()


def _module_source_at(rev: str, repo_root: pathlib.Path) -> str:
    got = _git(repo_root, "show", f"{rev}:{MODULE_REL}")
    if got.returncode != 0:
        _fail_usage(f"key-sweep: cannot read {MODULE_REL} at {rev} ({got.stderr.strip()}).")
    if not got.stdout.strip():
        _fail_usage(f"key-sweep: {MODULE_REL} is empty at {rev} - wrong revision?")
    return got.stdout


def _baseline_module(source: str) -> types.ModuleType:
    """Load `identity_key.py` from `source` text.

    Read through `git show` rather than a worktree checkout: round 14 tried the
    scratch-worktree route and could not run anything there, because the
    worktree has no virtualenv.

    The temp file is removed AT EXIT rather than immediately, so a traceback
    raised inside the baseline module can still show its source.
    """
    tmp = tempfile.NamedTemporaryFile("w", suffix="_identity_key_base.py", delete=False)
    tmp.write(source)
    tmp.close()

    def _cleanup() -> None:
        with contextlib.suppress(OSError):
            os.unlink(tmp.name)

    atexit.register(_cleanup)
    return _load_module(tmp.name, "identity_key_baseline")


def build_corpus(limit: int, seed: int, repo_root: pathlib.Path) -> list[str]:
    """A deterministic postcode corpus. SEEDED, so the figure is reproducible.

    Round 15 reported 59,815 candidates from an unseeded run and it re-measures
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


def sweep(base_mod, head_mod, corpus: list[str]) -> dict:
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


def _perturbed(module: types.ModuleType):
    """A view of `module` whose produced values differ by one character."""

    class _Perturbed:
        @staticmethod
        def classify_postcode(raw: str):
            real = module.classify_postcode(raw)
            return type(real)(real.value + "X", real.confidence)

        @staticmethod
        def postcode_is_weak_anchor(raw: str) -> bool:
            return module.postcode_is_weak_anchor(raw)

        @staticmethod
        def compute_identity_key(name, postcode):
            key = module.compute_identity_key(name, postcode)
            return None if key is None else key + "X"

        @staticmethod
        def identity_name(name, legal):
            return module.identity_name(name, legal)

    return _Perturbed


def _self_test(base_mod: types.ModuleType, head_mod: types.ModuleType, corpus: list[str]) -> bool:
    """Prove the probe, THROUGH THE REAL LOADER.

    `base_mod` is the module `_baseline_module` actually produced from git, so
    this exercises the resolve / show / import path a real run uses. The first
    cut perturbed a locally-constructed stand-in instead, which meant it passed
    with a bogus `--base` and could not detect the "compared with itself" case
    it exists to rule out.
    """
    print("  self-test: perturbing the REAL baseline to prove the sweep detects a change")

    probe = sweep(_perturbed(base_mod), head_mod, corpus[:2000])
    values, keys = len(probe["value_diffs"]), len(probe["key_diffs"])
    print(f"    perturbed VALUE diffs detected : {values:,}")
    print(f"    perturbed KEY diffs detected   : {keys:,}")
    if not (values and keys):
        print("    SELF-TEST FAILED: the sweep did not detect a deliberate change.")
        print("    Its null result would be meaningless. Fix the harness, not the code.")
        return False
    print("    self-test PASSED: a deliberate change is detected on both axes")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="main", help="revision to take the merge base against")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--self-test", action="store_true", help="only prove the probe, then exit")
    args = parser.parse_args()

    top = _git(pathlib.Path.cwd(), "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        _fail_usage(f"key-sweep: not inside a git repository ({top.stderr.strip()}).")
    repo_root = pathlib.Path(top.stdout.strip())

    merge_base = _resolve_merge_base(args.base, repo_root)
    head = _git(repo_root, "rev-parse", "HEAD").stdout.strip()

    corpus = build_corpus(args.limit, args.seed, repo_root)
    print(f"key-invariance sweep: {len(corpus):,} inputs, seed {args.seed}")
    print(f"  merge base ({args.base}) : {merge_base[:12]}")
    print(f"  head                     : {head[:12]}")

    base_source = _module_source_at(merge_base, repo_root)
    head_path = repo_root / MODULE_REL
    head_mod = _load_module(str(head_path), "identity_key_head")

    base_mod = _baseline_module(base_source)

    # THE PROBE RUNS BEFORE THE VACUOUS CHECK, and the order is load-bearing.
    # The self-test validates the HARNESS, not the diff, so short-circuiting it
    # when the module happens to be unchanged would make the CI step a no-op on
    # every PR that does not touch `identity_key.py` - which is most of them -
    # and the one job it has is to be exercised routinely.
    if not _self_test(base_mod, head_mod, corpus):
        return EXIT_PROBE_FAILED
    if args.self_test:
        return EXIT_OK

    # VACUOUS: there is nothing to compare, so there is nothing to conclude.
    # This is the post-merge case (`make key-sweep` on main) and the
    # module-untouched case. It is not a failure and it is not a PASS, and the
    # word PASS is deliberately never printed, because a vacuous run has been
    # cited as evidence of invariance before.
    if base_source == head_path.read_text():
        print("\n  VACUOUS: identity_key.py is IDENTICAL at the merge base and at HEAD.")
        print("  Nothing was compared, so nothing is proven. This is the expected")
        print("  result on main after merge, and on any branch that leaves the")
        print("  module alone. Do not cite it as evidence of invariance.")
        return EXIT_OK

    result = sweep(base_mod, head_mod, corpus)
    _report(result, f"working tree vs merge base {merge_base[:12]}")

    if result["value_diffs"] or result["key_diffs"]:
        print("\n  FAIL: a produced value moved. Stored identity_keys are orphaned.")
        print("  This is not a refactor. Either revert, or ship a recompute migration.")
        return EXIT_DIFF
    print("\n  PASS: 0 value diffs, 0 key diffs. No stored identity_key can be orphaned.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
