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

import ast as _ast
import importlib.util
import pathlib
import sys
from collections import Counter as _Counter

import pytest


def _load(name: str):
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves its own module through sys.modules at exec time, so
    # the module must be registered BEFORE exec_module runs.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


review_gate = _load("review_gate")
review_gate_mutate = _load("review_gate_mutate")
_GATE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "review_gate.py"


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


# ---------------------------------------------------------------------------
# Round 8: every detector below could previously be neutered wholesale with the
# whole self-test file green - "the gate built to end revert-green guards had
# revert-green detectors". Each class here backs a manifest mutation.
# ---------------------------------------------------------------------------


class TestG1DetectorInternals:
    @pytest.mark.parametrize(
        ("line", "is_prose"),
        [
            ('# a comment naming "E81AAX"', True),
            ('The docstring prose line names "E81AAX" too', True),
            ('value = normalize_postcode("E81AAX")', False),
            ('return "E81AAX"', False),
        ],
    )
    def test_comment_or_prose_detection(self, line: str, is_prose: bool) -> None:
        assert review_gate._is_comment_or_prose(line) is is_prose

    @pytest.mark.parametrize(
        ("literal", "is_data"),
        [
            ("E81AAX", True),
            ("1234567890", True),
            ("Cafe Gym", True),
            ("some_identifier", False),
            ("module.attr.path", False),
            ("I cannot tell", False),
        ],
    )
    def test_example_data_heuristic(self, literal: str, is_data: bool) -> None:
        assert review_gate._looks_like_example_data(literal) is is_data

    def test_quoted_literal_regex_extracts_both_quote_styles(self) -> None:
        hits = [
            m.group(1) or m.group(2)
            for m in review_gate._QUOTED.finditer("names \"E81AAX\" and '1011 AB' here")
        ]
        assert hits == ["E81AAX", "1011 AB"]

    def test_corpus_excludes_docstrings_but_keeps_bodies_and_decorators(
        self, tmp_path, monkeypatch
    ) -> None:
        """The round-8 AST scoping itself: a literal quoted only in a test's
        DOCSTRING is not coverage; body and parametrize-decorator literals are."""
        test_file = tmp_path / "test_synthetic.py"
        docstring = '"docstring quoting ' + '\\"DOCONLY1\\"' + ' is prose, not proof"'
        test_file.write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('v', ['DECOR8ED'])\n"
            "def test_x(v):\n"
            "    " + docstring + "\n"
            "    assert v != 'BODYLIT1'\n"
        )
        monkeypatch.setattr(review_gate, "TESTS_ROOT", tmp_path)
        corpus = review_gate._test_corpus()
        assert '"BODYLIT1"' in corpus
        assert '"DECOR8ED"' in corpus
        assert "DOCONLY1" not in corpus


class TestG5Detector:
    def _findings(self, source: str) -> list:
        result = review_gate.GateResult()
        review_gate._scan_seed_defaults(
            _ast.parse(source), pathlib.Path("synthetic/test_x.py"), result
        )
        return result.findings

    def test_seed_helper_with_pinned_corroborator_default_is_flagged(self) -> None:
        src = "async def _seed_client(s, *, phone='+44 7700 900123'):\n    ...\n"
        assert len(self._findings(src)) == 1

    def test_none_default_and_non_seed_names_are_clean(self) -> None:
        assert self._findings("async def _seed_client(s, *, phone=None):\n    ...\n") == []
        assert self._findings("async def helper(s, *, phone='x'):\n    ...\n") == []


class TestG6Detector:
    def test_net_new_value_is_flagged_and_preexisting_is_not(self) -> None:
        result = review_gate.GateResult()
        review_gate._net_new_pii(
            "test label",
            _Counter({"olddomainhit@bulletdigitalmedia.com": 2}),
            _Counter(
                {
                    "olddomainhit@bulletdigitalmedia.com": 2,
                    "newdomainhit@bulletdigitalmedia.com": 1,
                }
            ),
            [],
            result,
        )
        assert len(result.findings) == 1
        assert "newdomainhit" in result.findings[0].message

    def test_allowlisted_value_is_not_flagged(self) -> None:
        result = review_gate.GateResult()
        review_gate._net_new_pii(
            "test label",
            _Counter(),
            _Counter({"allowed-value-1": 1}),
            ["allowed-value-1"],
            result,
        )
        assert result.findings == []

    def test_service_account_pattern_matches_the_round8_live_instance_shape(self) -> None:
        pattern = review_gate._PII_PATTERNS["GCP service account"]
        assert pattern.search("bot-name@some-project.iam.gserviceaccount.com")
        assert not pattern.search("person@bulletdigitalmedia.com")


class TestMergeBaseRefusal:
    def test_unresolvable_base_exits_instead_of_passing_vacuously(self) -> None:
        # The docstring says this refusal exists to close the
        # vacuous-pass-on-unresolvable-base hole; round 8 found the refusal
        # itself could be `if False:`-ed with every self-test green.
        with pytest.raises(SystemExit):
            review_gate._merge_base("no-such-ref-anywhere-xyz")


class TestMutateRunnerClassification:
    """The load-bearing exit-code table (round 8: it had no tests of its own)."""

    @pytest.mark.parametrize(
        ("returncode", "output", "status"),
        [
            (1, "1 failed", "KILLED"),
            (0, "3 passed", "SURVIVED"),
            (0, "2 skipped", "UNPROVEN"),
            (0, "1 passed, 2 skipped", "SURVIVED"),
            (2, "interrupted", "ERROR"),
            (3, "internal error", "ERROR"),
            (4, "usage error: DB unreachable", "ERROR"),
            (5, "no tests ran", "ERROR"),
        ],
    )
    def test_the_full_table(self, returncode: int, output: str, status: str) -> None:
        got, _detail = review_gate_mutate._classify_pytest_result(returncode, output, "x::y")
        assert got == status
