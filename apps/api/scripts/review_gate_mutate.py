"""G2 - break every declared guard and prove a test dies with it.

The finding that recurred through five review rounds was never "the fix is
wrong". It was "the fix is right and nothing would notice if you deleted it".
Round 5 stated it as a procedure: *"Delete all three guards and 609 tests still
pass."*

So this runs that procedure against ourselves. For each entry in
`tests/mutation_manifest.toml` it applies the mutation, runs ONLY the named
test, and asserts that test FAILS. A surviving mutation means the guard is
undefended - the test that claims to cover it would not notice its removal.

Three outcomes per mutation:

    KILLED    the test failed as it should - the guard is genuinely defended
    SURVIVED  the test passed with the guard broken - a finding
    UNPROVEN  the test skipped (needs Postgres) - NOT a pass; a skipped test
              cannot fail, and treating skips as green is exactly what let two
              review rounds ship

The file is always restored, including on interrupt or crash.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "apps" / "api"
MANIFEST = API_ROOT / "tests" / "mutation_manifest.toml"
LOCKFILE = API_ROOT / ".review_gate_mutate.lock"
SIDECAR_SUFFIX = ".review_gate.orig"

# Per-test wall clock. A hung pytest (a DB lock, a leaked connection, a stray
# `pdb.set_trace()`) would otherwise hang the runner forever WITH SOURCE
# MUTATED ON DISK - which is what pushes an operator into killing the terminal,
# the one exit path that cannot restore.
TEST_TIMEOUT_SECONDS = 600

# pytest exit codes. 1 = tests failed (a genuine kill). 2 interrupted,
# 3 internal error, 4 usage error, 5 no tests collected - every one of those is
# "the run did not answer the question", NOT "the guard is defended". Reading
# any non-zero as KILLED is how a stale node id, or `CI=1` with Postgres down
# (conftest raises UsageError -> 4), reports every guard green.
_PYTEST_TESTS_FAILED = 1
_PYTEST_NON_ANSWERS = {2, 3, 4, 5}

KILLED, SURVIVED, UNPROVEN, ERROR = "KILLED", "SURVIVED", "UNPROVEN", "ERROR"


@dataclass
class Outcome:
    name: str
    status: str
    detail: str = ""


def _pytest(node_id: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            f"apps/api/{node_id}",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            *extra,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=TEST_TIMEOUT_SECONDS,
    )


def _node_id_resolves(node_id: str) -> bool:
    """Does this `must_fail` node id actually select a test?

    Checked BEFORE mutating. The manifest guards the `find` side going stale
    (it reports ERROR), but the `must_fail` side is the likelier drift - rename
    a test class and every non-zero exit still reads as KILLED, so the guard it
    names could be deleted outright with the gate staying green.
    """
    proc = _pytest(node_id, "--collect-only")
    return proc.returncode == 0 and " no tests ran" not in (proc.stdout + proc.stderr)


def _run_test(node_id: str) -> tuple[str, str]:
    """Return (status, detail) for one mutated guard's named test."""
    try:
        proc = _pytest(node_id)
    except subprocess.TimeoutExpired:
        return ERROR, f"pytest exceeded {TEST_TIMEOUT_SECONDS}s - the guard is unproven"

    output = proc.stdout + proc.stderr
    if proc.returncode in _PYTEST_NON_ANSWERS:
        return ERROR, (
            f"pytest exited {proc.returncode} (not a test failure) - the run did not "
            f"answer the question. Last line: {output.strip().splitlines()[-1:]}"
        )
    # A run of only-skipped tests exits 0, so the exit code alone cannot tell
    # "defended" from "never actually executed".
    if " skipped" in output and " passed" not in output and " failed" not in output:
        return UNPROVEN, "the named test skipped - it cannot fail, so it cannot kill"
    if proc.returncode == _PYTEST_TESTS_FAILED:
        return KILLED, ""
    return SURVIVED, f"{node_id} passed with the guard broken"


def _apply(path: Path, find: str, replace: str) -> tuple[str, str] | str:
    """Mutate `find` -> `replace`. Returns (original, mutated) or an error string.

    Writes a sidecar copy of the original first, so a kill -9 between here and
    the restore leaves recoverable evidence rather than a silently-broken guard
    in someone's working tree.
    """
    original = path.read_text()
    occurrences = original.count(find)
    if occurrences == 0:
        return "pattern not found - manifest is stale"
    if occurrences > 1:
        # `replace(..., 1)` would silently take the first, so the mutation could
        # land on a different site than the one the entry names while still
        # reporting KILLED.
        return f"pattern is ambiguous ({occurrences} occurrences) - make it unique"
    path.with_suffix(path.suffix + SIDECAR_SUFFIX).write_text(original)
    mutated = original.replace(find, replace, 1)
    _atomic_write(path, mutated)
    return original, mutated


def _atomic_write(path: Path, text: str) -> None:
    """Replace `path` in one step, so a kill mid-write cannot truncate source."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _restore(path: Path, original: str, mutated: str) -> str | None:
    """Put the original back, refusing to clobber a concurrent edit.

    The snapshot is taken before the mutation and written back after a test run
    that can take a minute. If the file changed in between - the operator's
    editor, format-on-save, a file watcher - writing the snapshot back would
    destroy their work silently, on a run that reports success.
    """
    current = path.read_text()
    if _digest(current) != _digest(mutated):
        return (
            f"{path} changed during the run; the original was NOT restored to avoid "
            f"destroying that edit. Recover it from {path.name}{SIDECAR_SUFFIX}"
        )
    _atomic_write(path, original)
    path.with_suffix(path.suffix + SIDECAR_SUFFIX).unlink(missing_ok=True)
    return None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _check_no_stale_sidecars() -> list[Path]:
    """Sidecars left behind mean a previous run died mid-mutation."""
    return sorted(API_ROOT.rglob(f"*{SIDECAR_SUFFIX}"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-db", action="store_true", help="run db-marked mutations too")
    parser.add_argument("--only", help="substring match on a mutation name")
    parser.add_argument(
        "--allow-unproven",
        action="store_true",
        help="exit 0 despite UNPROVEN guards (no local Postgres). NOT for CI.",
    )
    args = parser.parse_args()

    stale = _check_no_stale_sidecars()
    if stale:
        print("REFUSING TO RUN - a previous run died mid-mutation and left:")
        for path in stale:
            print(f"  {path}")
        print(
            "\nEach sidecar holds the ORIGINAL of a file this tool mutated. Compare it "
            "against the live file, restore it, then delete the sidecar."
        )
        return 1

    manifest = tomllib.loads(MANIFEST.read_text())
    mutations = manifest.get("mutation", [])
    outcomes: list[Outcome] = []
    selected = [m for m in mutations if not args.only or args.only in m["name"]]

    if args.only and not selected:
        print(f"--only {args.only!r} matched no mutation - refusing to report a vacuous pass.")
        return 1

    print(f"mutation manifest: {len(mutations)} guard(s) declared\n")

    # Restore-on-signal. `try/finally` covers Python exceptions but NOT SIGTERM
    # or SIGHUP, so an IDE stopping the task or a closed terminal would leave a
    # guard deleted in the working tree with no marker - and this tool runs
    # immediately before a commit.
    in_flight: dict[Path, tuple[str, str]] = {}

    def _restore_all_and_exit(signum: int, _frame: FrameType | None) -> None:
        for path, (original, mutated) in in_flight.items():
            problem = _restore(path, original, mutated)
            print(f"\nsignal {signum}: {problem or f'restored {path}'}")
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, _restore_all_and_exit)

    for entry in selected:
        name = entry["name"]
        if entry.get("requires_db") and not args.include_db:
            outcomes.append(
                Outcome(name, UNPROVEN, "db-marked; pass --include-db with Postgres up")
            )
            print(f"  {UNPROVEN:<9} {name}")
            continue

        path = API_ROOT / entry["file"]
        if not path.resolve().is_relative_to(API_ROOT):
            outcomes.append(Outcome(name, ERROR, f"file escapes {API_ROOT}: {entry['file']}"))
            print(f"  {ERROR:<9} {name}")
            continue

        # Verify the named test EXISTS before mutating. Without this a renamed
        # test makes every non-zero exit read as KILLED, so the guard it names
        # could be deleted outright and the gate would stay green.
        if not _node_id_resolves(entry["must_fail"]):
            outcomes.append(
                Outcome(name, ERROR, f"must_fail selects no test: {entry['must_fail']}")
            )
            print(f"  {ERROR:<9} {name}")
            continue

        applied = _apply(path, entry["find"], entry["replace"])
        if isinstance(applied, str):
            outcomes.append(Outcome(name, ERROR, f"{entry['file']}: {applied}"))
            print(f"  {ERROR:<9} {name}")
            continue

        original, mutated = applied
        in_flight[path] = (original, mutated)
        try:
            status, detail = _run_test(entry["must_fail"])
        finally:
            problem = _restore(path, original, mutated)
            in_flight.pop(path, None)
            if problem is not None:
                status, detail = ERROR, problem

        outcomes.append(Outcome(name, status, detail))
        print(f"  {status:<9} {name}")
        if detail and status != UNPROVEN:
            print(f"            {detail}")

    survived = [o for o in outcomes if o.status == SURVIVED]
    errored = [o for o in outcomes if o.status == ERROR]
    unproven = [o for o in outcomes if o.status == UNPROVEN]
    killed = [o for o in outcomes if o.status == KILLED]

    print(
        f"\n{len(killed)} killed, {len(survived)} survived, "
        f"{len(unproven)} unproven, {len(errored)} manifest error(s)"
    )
    if unproven:
        print("\nUNPROVEN is not a pass. Bring Postgres up and re-run with --include-db:")
        for outcome in unproven:
            print(f"  - {outcome.name}")
        if args.allow_unproven:
            print("\n(--allow-unproven: exiting 0 anyway. These guards are NOT verified.)")
    if survived:
        print("\nSURVIVED - these guards are revert-green, exactly the round-5 finding:")
        for outcome in survived:
            print(f"  - {outcome.name}: {outcome.detail}")
    if errored:
        print("\nMANIFEST ERRORS - a `find` pattern no longer matches the source:")
        for outcome in errored:
            print(f"  - {outcome.name}: {outcome.detail}")

    # UNPROVEN FAILS THE BUILD. It used to exit 0 while printing "UNPROVEN is
    # not a pass", so the default `make review-gate` was green with every
    # db-marked guard never executed - the tool contradicting its own premise,
    # and the same shape as the skipped-tests-report-green failure that let two
    # review rounds ship. `--allow-unproven` is the deliberate laptop escape.
    unproven_fails = bool(unproven) and not args.allow_unproven
    return 1 if (survived or errored or unproven_fails) else 0


def _main_locked() -> int:
    """Serialise runs. Two concurrent runs mutating the same file permanently
    corrupt it while BOTH report success: the second snapshots the first's
    mutated text as its "original" and restores that. `identity_key.py` and
    `ghl_subaccount.py` each carry many entries, and running `review-gate` and
    `review-gate-db` in two terminals is the documented workflow."""
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCKFILE.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(
                "another review-gate mutation run holds the lock. Concurrent runs "
                "corrupt source files while both report success - refusing to start."
            )
            return 1
        try:
            return main()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


if __name__ == "__main__":
    sys.exit(_main_locked())
