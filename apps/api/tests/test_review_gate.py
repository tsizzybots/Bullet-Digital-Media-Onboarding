"""Self-tests for the review gate's detection logic.

Review round 7: "the gate itself has no self-tests" - and it was right in the
way that stings, because the gate's G4 visitor missed six of the eight
conditional-assert shapes the reviewer tabulated, while its G1 check passed a
literal that appeared only as a PREFIX of a longer string in the corpus. A gate
trusted more than it deserves is worse than no gate; these pin exactly what
each check can and cannot see.

The gate lives in `scripts/` (not a package), so it is loaded by file path.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_GATE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "review_gate.py"
_spec = importlib.util.spec_from_file_location("review_gate", _GATE_PATH)
assert _spec is not None and _spec.loader is not None
review_gate = importlib.util.module_from_spec(_spec)
# @dataclass resolves its own module through sys.modules at exec time, so the
# module must be registered BEFORE exec_module runs.
sys.modules["review_gate"] = review_gate
_spec.loader.exec_module(review_gate)


def _g4_findings(source: str) -> int:
    """Run the G4 visitor over a synthetic test module, count findings."""
    import ast

    result = review_gate.GateResult()
    tree = ast.parse(source)
    review_gate._ConditionalAssertVisitor(_GATE_PATH, result).visit(tree)
    return len(result.findings)


class TestG4ConditionalAssertShapes:
    """The eight shapes from review round 7's table, each pinned by name."""

    def test_call_condition_with_top_level_assert_is_flagged(self) -> None:
        assert _g4_findings("if session.scalar_one():\n    assert x\n") == 1

    def test_attribute_condition_is_flagged(self) -> None:
        # `if row.linked_client_id:` - invisible before round 7.
        assert _g4_findings("if row.linked_client_id:\n    assert x\n") == 1

    def test_subscript_condition_is_flagged(self) -> None:
        # `if rows[0]:` - invisible before round 7.
        assert _g4_findings("if rows[0]:\n    assert x\n") == 1

    def test_assert_nested_in_a_for_loop_is_flagged(self) -> None:
        # `if hits: for h in hits: assert ...` - the commonest real shape,
        # invisible before round 7 because only top-level statements were read.
        assert _g4_findings("if f():\n    for r in rows:\n        assert r\n") == 1

    def test_assert_nested_in_a_with_block_is_flagged(self) -> None:
        assert _g4_findings("if f():\n    with open('x') as fh:\n        assert fh\n") == 1

    def test_assert_only_in_the_else_branch_is_flagged(self) -> None:
        # `if f(): pass else: assert` - the inverted skip, invisible before
        # round 7 because `orelse` was never inspected.
        assert _g4_findings("if f():\n    pass\nelse:\n    assert x\n") == 1

    def test_plain_parametrized_flag_is_not_flagged(self) -> None:
        # `if expect_link:` reads nothing derived - the legitimate
        # parametrize-driven branch.
        assert _g4_findings("if expect_link:\n    assert x\n") == 0

    def test_asserts_on_both_branches_are_not_flagged(self) -> None:
        # An if/else asserting on BOTH sides always runs one of them - the
        # branch-per-expectation pattern, not a silent skip.
        assert _g4_findings("if f():\n    assert a\nelse:\n    assert b\n") == 0


class TestG1QuotedLiteralRule:
    """The literal must appear as an exact QUOTED string, not a substring."""

    @pytest.mark.parametrize(
        ("corpus", "covered"),
        [
            ('assert diverge("1 Mare St", x)', True),  # exact quoted form
            ("assert diverge('1 Mare St', x)", True),  # single-quoted form
            ('assert diverge("1 Mare Street", x)', False),  # PREFIX only - the
            # round-7 live instance: substring matching reported this covered.
            ("no mention at all", False),
        ],
    )
    def test_prefix_of_a_longer_literal_is_not_coverage(self, corpus: str, covered: bool) -> None:
        # Calls the GATE's helper, never a re-implementation of the rule: the
        # first version of this test inlined its own copy of the check, and the
        # mutation runner immediately reported the gate's G1 mutation SURVIVED -
        # a tautology test cannot catch a revert. The round-7 oracle lesson,
        # caught recurring inside the very test written to encode it.
        assert review_gate._literal_is_covered("1 Mare St", corpus) is covered


class TestG6ScanScope:
    """Round 7: render.yaml, ci.yml, dashboard TS and JSON were outside the
    PII gate on a public repo - and this PR touched four of those."""

    @pytest.mark.parametrize(
        ("name", "scanned"),
        [
            ("apps/api/src/bullet_api/worker/ghl_subaccount.py", True),
            ("docs/CHANGELOG.md", True),
            ("render.yaml", True),
            (".github/workflows/ci.yml", True),
            ("apps/dashboard/src/components/client-detail.tsx", True),
            ("apps/api/tests/fixtures/ghl_hit.json", True),
            ("apps/dashboard/node_modules/pkg/index.ts", False),
            ("apps/api/.venv/lib/thing.py", False),
            ("pnpm-lock.yaml", False),
        ],
    )
    def test_scan_candidates(self, name: str, scanned: bool) -> None:
        assert review_gate._pii_scan_candidate(name) is scanned
