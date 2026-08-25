"""Run the reviewer's own techniques against our diff, before they do.

Five rounds of review on S1-26b/c produced the same shape of finding every
time: the fix was right, the test proving it was missing or could not fail.
The reviewer catches these with a small set of repeatable moves. This script
mechanizes the ones that are mechanizable, so a finding of that class fails
here rather than in a review round.

    G1  comment-literal coverage   - a fix comment naming example data must
                                     have that data in a test
    G4  no conditional assertions  - an `assert` behind an `if` reading the
                                     system under test passes silently
    G5  no defaults on identity    - fixture defaults that pin a discriminating
        fixture params               signal in one direction
    G6  diff-scoped PII            - live ids / client emails net-new vs the
                                     merge base

G2 (mutation manifest) lives in `review_gate_mutate.py` because it has to run
pytest repeatedly. G3/G7-G10 are reasoning checks and ride in the PR body.

Exit code 0 = clean, 1 = findings. `--json` for machine-readable output.

NOT a substitute for `/pre-pr-review`. This catches the mechanical classes;
that catches the ones needing judgement.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "apps" / "api"
TESTS_ROOT = API_ROOT / "tests"
ALLOWLIST_PATH = Path(__file__).parent / "review_gate_allowlist.json"


@dataclass
class Finding:
    check: str
    location: str
    message: str

    def render(self) -> str:
        return f"  {self.location}\n      {self.message}"


@dataclass
class GateResult:
    findings: list[Finding] = field(default_factory=list)

    def add(self, check: str, location: str, message: str) -> None:
        self.findings.append(Finding(check, location, message))


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    ).stdout


def _merge_base(base: str) -> str:
    """Resolve `base` to a commit, or exit.

    Never falls back to the unvalidated ref string. It used to, and an
    unresolvable base then produced an EMPTY diff, so G1 reported `clean` over
    the entire change - silently, and exactly where it matters: a shallow CI
    checkout (`actions/checkout` defaults to `fetch-depth: 1`, so `main` is
    absent), a fork whose default branch is `master`, or `BASE=origin/main`
    with no prior fetch.
    """
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        sys.exit(
            f"review gate: base ref {base!r} does not resolve to a commit "
            f"({probe.stderr.strip()}).\nIn CI, checkout with fetch-depth: 0. "
            f"Refusing to report a vacuous pass against an empty diff."
        )
    resolved = _git("merge-base", base, "HEAD").strip()
    if not resolved:
        sys.exit(
            f"review gate: no merge base between {base!r} and HEAD - unrelated "
            f"histories or a shallow clone. Refusing to report a vacuous pass."
        )
    return resolved


def _load_allowlist() -> dict[str, list[str]]:
    if not ALLOWLIST_PATH.exists():
        return {}
    return json.loads(ALLOWLIST_PATH.read_text())


# ---------------------------------------------------------------------------
# G1 - comment-literal coverage
#
# The reviewer's cheapest move: our own fix comments name the exact string to
# grep for. `_is_sequential_digits`'s comment names "1234567890"; the suite
# contains zero occurrences of it. The comment hands them the query.
# ---------------------------------------------------------------------------

_QUOTED = re.compile(r'"([^"\n]{2,60})"|\'([^\'\n]{2,60})\'')

# A literal worth checking looks like EXAMPLE DATA, not a code reference.
# Either it carries a digit ("E81AAX", "+44 7700 900123") or it is a
# capitalised phrase ("Café Gym"). Identifiers, dotted paths and event names
# are excluded by construction - they have no space and no leading capital.
_IDENTIFIER_ISH = re.compile(r"^[a-z_][a-z0-9_]*$|^[a-z_.]+$|^[A-Z_]+$")


def _looks_like_example_data(literal: str) -> bool:
    if _IDENTIFIER_ISH.match(literal):
        return False
    if "." in literal and " " not in literal:  # dotted paths: event names, attrs
        return False
    if "_" in literal and " " not in literal:  # snake_case identifiers
        return False
    # A literal truncated by a docstring boundary keeps its trailing space and
    # runs to prose length. Real example data is short and self-contained.
    if literal != literal.strip() or literal.count(" ") > 3:
        return False
    words = literal.split()
    if not words:
        return False
    has_digit = any(ch.isdigit() for ch in literal)
    # "Café Gym" / "Brand Gym Hackney" are data; "I cannot tell" is prose. Data
    # reads as a proper noun - every word capitalised or numeric.
    proper_noun_phrase = len(words) > 1 and all(
        word[:1].isupper() or word[:1].isdigit() for word in words
    )
    return has_digit or proper_noun_phrase


def _is_comment_or_prose(text: str) -> bool:
    """A comment, or docstring prose - a line with no code punctuation that
    sits inside a triple-quoted block, approximated by "no assignment, no call,
    starts with a word or quote"."""
    stripped = text.strip()
    if stripped.startswith("#"):
        return True
    if not stripped or not re.match(r"^[A-Za-z`\"']", stripped):
        return False
    return not re.search(r"[=(){}\[\]]|^(def|class|import|from|return|if|for)\b", stripped)


def _strip_docstring_delimiters(text: str) -> str:
    """Else `\"\"\"Text ... or \"` reads as one literal spanning the whole line."""
    return text.replace('"""', "").replace("'''", "")


def _added_comment_lines(base: str) -> list[tuple[str, int, str]]:
    """(file, approximate line, text) for added comment/docstring-prose lines.

    Diffs the WORKING TREE against the merge base, not `base...HEAD`. The gate's
    whole purpose is to run before a commit, so scanning only committed history
    would report findings the author has already fixed and miss the ones they
    just introduced.
    """
    diff = _git("diff", base, "--unified=0", "--", "apps/api/src")
    out: list[tuple[str, int, str]] = []

    # UNTRACKED files are not in `git diff` at all, so a brand-new module's fix
    # comments were never scanned - and "brand new, not yet added" is precisely
    # the pre-commit state this gate exists to cover. Every line of one counts
    # as added.
    untracked = _git(
        "ls-files", "--others", "--exclude-standard", "--", "apps/api/src"
    ).splitlines()
    for name in untracked:
        path = REPO_ROOT / name
        if not path.is_file() or path.suffix != ".py":
            continue
        for index, text in enumerate(path.read_text(errors="replace").splitlines(), start=1):
            if _is_comment_or_prose(text):
                out.append((name, index, _strip_docstring_delimiters(text)))

    current_file = ""
    line_no = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line_no = int(m.group(1)) if m else 0
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            text = raw[1:]
            if _is_comment_or_prose(text):
                out.append((current_file, line_no, _strip_docstring_delimiters(text)))
            line_no += 1
    return out


def _test_corpus() -> str:
    parts = []
    for path in TESTS_ROOT.rglob("*.py"):
        parts.append(path.read_text(errors="replace"))
    return "\n".join(parts)


def check_g1_comment_literals(result: GateResult, base: str, allowlist: list[str]) -> None:
    corpus = _test_corpus()
    seen: set[str] = set()
    for file, line, text in _added_comment_lines(base):
        for match in _QUOTED.finditer(text):
            literal = match.group(1) or match.group(2)
            if not literal or literal in seen or literal in allowlist:
                continue
            if not _looks_like_example_data(literal):
                continue
            seen.add(literal)
            if literal not in corpus:
                result.add(
                    "G1",
                    f"{file}:{line}",
                    f'comment names example data "{literal}" but no test contains it - '
                    f"the guard it documents is revert-green",
                )


# ---------------------------------------------------------------------------
# G4 - assertions must not hide behind a condition read from the system
# ---------------------------------------------------------------------------


class _ConditionalAssertVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, result: GateResult) -> None:
        self.path = path
        self.result = result

    def visit_If(self, node: ast.If) -> None:
        if self._contains_assert(node.body) and self._condition_reads_system(node.test):
            self.result.add(
                "G4",
                f"{self.path.relative_to(REPO_ROOT)}:{node.lineno}",
                "assertions are gated behind a condition read from the system under "
                "test - this passes silently in exactly the case it exists to catch",
            )
        self.generic_visit(node)

    @staticmethod
    def _contains_assert(body: list[ast.stmt]) -> bool:
        return any(isinstance(stmt, ast.Assert) for stmt in body)

    @staticmethod
    def _condition_reads_system(test: ast.expr) -> bool:
        """True when the condition calls something, i.e. is derived rather than
        a static parametrized flag. `if flagged.scalar_one():` qualifies;
        `if expect_link:` does not."""
        return any(isinstance(n, ast.Call) for n in ast.walk(test))


def check_g4_conditional_assertions(result: GateResult) -> None:
    for path in TESTS_ROOT.rglob("test_*.py"):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        _ConditionalAssertVisitor(path, result).visit(tree)


# ---------------------------------------------------------------------------
# G5 - fixture helpers must not default a discriminating signal
#
# `_seed_client(contact_first_name="Sample", contact_last_name="Signer")` makes
# every seeded row agree on the signal, so a bar that reads it is pinned in one
# direction and mutating it to a constant passes the whole suite.
# ---------------------------------------------------------------------------

# Only the CORROBORATING signals - the fields a matching bar reads to decide
# link-vs-flag. A shared `business_name`/`postal_code`/`email` default is
# usually the deliberate setup for a sibling test (it is how two rows come to
# share an identity key at all), but a shared PHONE or CONTACT NAME silently
# satisfies a bar the test never mentions, so the bar is pinned open and
# mutating it to a constant passes the whole suite.
_DISCRIMINATING_PARAMS = {
    "phone",
    "contact_first_name",
    "contact_last_name",
    "address",
}


def check_g5_fixture_defaults(result: GateResult) -> None:
    for path in TESTS_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if not node.name.startswith("_seed"):
                continue
            kwonly = node.args.kwonlyargs
            defaults = node.args.kw_defaults
            for arg, default in zip(kwonly, defaults, strict=False):
                if arg.arg not in _DISCRIMINATING_PARAMS or default is None:
                    continue
                if isinstance(default, ast.Constant) and default.value is None:
                    continue  # None default is "absent", not a pinned value
                result.add(
                    "G5",
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno}",
                    f"`{node.name}` defaults the discriminating signal "
                    f"`{arg.arg}` - every seeded row agrees on it, so a bar "
                    f"reading it is pinned in one direction",
                )


# ---------------------------------------------------------------------------
# G6 - diff-scoped PII: net-new live identifiers vs the merge base
# ---------------------------------------------------------------------------

# A GHL location id is 20-22 chars of mixed case WITH digits and irregular
# casing (shaped like `aB3cD4eF5gH6iJ7kL8mN`). Requiring a digit and rejecting CamelCase
# keeps Python class names (`ExtractedClientFields`) out of the results.
_CAMEL_CASE = re.compile(r"^(?:[A-Z][a-z0-9]+){2,}$")
_GHL_ID_SHAPE = re.compile(
    r"\b(?=[A-Za-z0-9]{20,22}\b)(?=[^\s]*[a-z])(?=[^\s]*[A-Z])(?=[^\s]*[0-9])[A-Za-z0-9]{20,22}\b"
)

_PII_PATTERNS = {
    "client-domain email": re.compile(r"[\w.+-]+@(?:bulletdigitalmedia)\.com"),
    "Render id": re.compile(r"\b(?:srv|dep|evg|crn)-[a-z0-9]{15,}\b"),
    "GHL location id": _GHL_ID_SHAPE,
}

_PII_SCAN_GLOBS = ("apps/api/**/*.py", "docs/**/*.md")


def _occurrences_at(ref: str | None, pattern: re.Pattern[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    if ref is None:
        for glob in _PII_SCAN_GLOBS:
            for path in REPO_ROOT.glob(glob):
                counts.update(pattern.findall(path.read_text(errors="replace")))
        return counts
    files = _git("ls-tree", "-r", "--name-only", ref).splitlines()
    for name in files:
        if not (name.startswith("apps/api/") or name.startswith("docs/")):
            continue
        if not name.endswith((".py", ".md")):
            continue
        counts.update(pattern.findall(_git("show", f"{ref}:{name}")))
    return counts


def check_g6_pii(result: GateResult, base: str, allowlist: list[str]) -> None:
    for label, pattern in _PII_PATTERNS.items():
        before = _occurrences_at(base, pattern)
        after = _occurrences_at(None, pattern)
        for value, count in after.items():
            if value in allowlist or _CAMEL_CASE.match(value):
                continue
            was = before.get(value, 0)
            if count > was:
                result.add(
                    "G6",
                    "working tree",
                    f"{label} `{value}` net-new: merge-base has {was}, HEAD has {count}",
                )


# ---------------------------------------------------------------------------


CHECKS = {
    "G1": "comment-literal coverage",
    "G4": "no conditional assertions",
    "G5": "no defaults on discriminating fixture params",
    "G6": "diff-scoped PII",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="main", help="branch to diff against")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--only", help="run a single check, e.g. G1")
    args = parser.parse_args()

    base = _merge_base(args.base)
    allowlist = _load_allowlist()
    result = GateResult()

    selected = [args.only] if args.only else list(CHECKS)
    if "G1" in selected:
        check_g1_comment_literals(result, base, allowlist.get("G1", []))
    if "G4" in selected:
        check_g4_conditional_assertions(result)
    if "G5" in selected:
        check_g5_fixture_defaults(result)
    if "G6" in selected:
        check_g6_pii(result, base, allowlist.get("G6", []))

    if args.json:
        payload = [
            {"check": f.check, "location": f.location, "message": f.message}
            for f in result.findings
        ]
        print(json.dumps(payload, indent=2))
        return 1 if result.findings else 0

    print(f"review gate - diffing against {base[:12]}\n")
    by_check: dict[str, list[Finding]] = {}
    for finding in result.findings:
        by_check.setdefault(finding.check, []).append(finding)

    for code in selected:
        found = by_check.get(code, [])
        status = f"{len(found)} finding(s)" if found else "clean"
        print(f"{code} {CHECKS[code]:<48} {status}")
        for finding in found:
            print(finding.render())
        if found:
            print()

    total = len(result.findings)
    print(f"\n{'FAIL' if total else 'PASS'}: {total} finding(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
