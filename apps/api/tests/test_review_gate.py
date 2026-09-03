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


# ---------------------------------------------------------------------------
# Round 12 (P1.5): the COMPOSITION ROOTS. Round 8 self-tested the leaf helpers
# and the manifest mutated the same leaves - `check_g1/g4/g5/g6`, the diff
# parser, the mutate runner's flow guards and its exit aggregation were all
# still revert-green: `for path in []` in check_g4 was a clean gate forever.
# Each class below backs a manifest mutation on the ROOT it exercises.
# ---------------------------------------------------------------------------


class TestCheckRootsAreWired:
    def test_check_g4_scans_the_tests_root(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "test_synthetic.py").write_text("if rows[0]:\n    assert rows\n")
        monkeypatch.setattr(review_gate, "TESTS_ROOT", tmp_path)
        result = review_gate.GateResult()
        review_gate.check_g4_conditional_assertions(result)
        assert len(result.findings) == 1

    def test_check_g5_scans_the_tests_root(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "test_seeds.py").write_text(
            "async def _seed_client(s, *, phone='+44 7700 900123'):\n    ...\n"
        )
        monkeypatch.setattr(review_gate, "TESTS_ROOT", tmp_path)
        result = review_gate.GateResult()
        review_gate.check_g5_fixture_defaults(result)
        assert len(result.findings) == 1

    def test_check_g1_reports_an_uncovered_literal(self, monkeypatch) -> None:
        monkeypatch.setattr(
            review_gate,
            "_added_comment_lines",
            lambda base: [("f.py", 3, 'rejects "AB12 3CDE" now')],
        )
        monkeypatch.setattr(review_gate, "_test_corpus", lambda: "")
        result = review_gate.GateResult()
        review_gate.check_g1_comment_literals(result, "base", [])
        assert len(result.findings) == 1
        assert "AB12 3CDE" in result.findings[0].message

    def test_check_g1_accepts_a_covered_literal(self, monkeypatch) -> None:
        monkeypatch.setattr(
            review_gate,
            "_added_comment_lines",
            lambda base: [("f.py", 3, 'rejects "AB12 3CDE" now')],
        )
        monkeypatch.setattr(review_gate, "_test_corpus", lambda: 'x = "AB12 3CDE"')
        result = review_gate.GateResult()
        review_gate.check_g1_comment_literals(result, "base", [])
        assert result.findings == []

    def test_check_g6_walks_every_pattern(self, monkeypatch) -> None:
        def _fake_occurrences(ref, pattern):
            if ref is None:  # the working tree
                return _Counter({"newdomainhit@bulletdigitalmedia.com": 1})
            return _Counter()

        monkeypatch.setattr(review_gate, "_occurrences_at", _fake_occurrences)
        result = review_gate.GateResult()
        review_gate.check_g6_pii(result, "base", [])
        # One finding per pattern (the fake reports the same value for all of
        # them); what matters is the loop actually visits the patterns.
        assert len(result.findings) == len(review_gate._PII_PATTERNS)

    def test_added_comment_lines_parses_the_diff(self, monkeypatch) -> None:
        diff = (
            "diff --git a/apps/api/src/x.py b/apps/api/src/x.py\n"
            "+++ b/apps/api/src/x.py\n"
            "@@ -0,0 +7 @@\n"
            '+    # the fix rejects "AB12 3CDE" outright\n'
            "+    code_line = 1\n"
        )

        def _fake_git(*args):
            return diff if args[0] == "diff" else ""

        monkeypatch.setattr(review_gate, "_git", _fake_git)
        lines = review_gate._added_comment_lines("base")
        assert lines == [("apps/api/src/x.py", 7, '    # the fix rejects "AB12 3CDE" outright')]


class TestG7NormalizerMigration:
    """Round 12, P1.6: the recompute obligation is enforced, not prose."""

    BASE_SRC = (
        'import re\n_UK_POSTCODE = re.compile("A")\n'
        'def normalize_postcode(v):\n    "doc"\n    return v\n'
    )

    def _run(self, monkeypatch, tmp_path, current_src: str, diff_files: list[str]) -> list:
        module = tmp_path / "identity_key.py"
        module.write_text(current_src)
        for name in diff_files:
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("op.add_column('clients', 'identity_key')")

        def _fake_git(*args):
            if args[0] == "show":
                return self.BASE_SRC
            if args[0] == "diff":
                return "\n".join(diff_files)
            return ""

        monkeypatch.setattr(review_gate, "_git", _fake_git)
        monkeypatch.setattr(review_gate, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(review_gate, "_IDENTITY_KEY_MODULE", "identity_key.py")
        result = review_gate.GateResult()
        review_gate.check_g7_normalizer_migration(result, "base")
        return result.findings

    def test_semantic_change_without_migration_fails(self, monkeypatch, tmp_path) -> None:
        changed = self.BASE_SRC.replace('re.compile("A")', 're.compile("B")')
        assert len(self._run(monkeypatch, tmp_path, changed, [])) == 1

    def test_semantic_change_with_key_migration_passes(self, monkeypatch, tmp_path) -> None:
        changed = self.BASE_SRC.replace('re.compile("A")', 're.compile("B")')
        files = ["apps/api/alembic/versions/0099_recompute.py"]
        assert self._run(monkeypatch, tmp_path, changed, files) == []

    def test_docstring_only_change_does_not_trip(self, monkeypatch, tmp_path) -> None:
        changed = self.BASE_SRC.replace('"doc"', '"a completely rewritten docstring"')
        assert self._run(monkeypatch, tmp_path, changed, []) == []

    def test_fingerprint_ignores_non_key_code(self) -> None:
        a = review_gate._normalizer_fingerprint("def unrelated():\n    return 1\n")
        b = review_gate._normalizer_fingerprint("def unrelated():\n    return 2\n")
        assert a == b == ""


class TestMutateRunnerFlowGuards:
    def test_exit_code_full_table(self) -> None:
        table = [
            (dict(survived=False, errored=False, unproven_fails=False), 0),
            (dict(survived=True, errored=False, unproven_fails=False), 1),
            (dict(survived=False, errored=True, unproven_fails=False), 1),
            (dict(survived=False, errored=False, unproven_fails=True), 1),
            (dict(survived=True, errored=True, unproven_fails=True), 1),
        ]
        for kwargs, expected in table:
            assert review_gate_mutate._exit_code(**kwargs) == expected

    def test_apply_refuses_an_ambiguous_pattern(self, tmp_path) -> None:
        target = tmp_path / "mod.py"
        target.write_text("guard()\nguard()\n")
        outcome = review_gate_mutate._apply(target, "guard()", "pass")
        assert isinstance(outcome, str) and "ambiguous" in outcome
        assert target.read_text() == "guard()\nguard()\n"  # untouched

    def test_apply_refuses_a_stale_pattern(self, tmp_path) -> None:
        target = tmp_path / "mod.py"
        target.write_text("something_else()\n")
        outcome = review_gate_mutate._apply(target, "guard()", "pass")
        assert isinstance(outcome, str) and "stale" in outcome

    def test_restore_refuses_to_clobber_a_concurrent_edit(self, tmp_path) -> None:
        target = tmp_path / "mod.py"
        target.write_text("the operator edited this mid-run\n")
        problem = review_gate_mutate._restore(target, "original\n", "mutated\n")
        assert problem is not None and "NOT restored" in problem
        assert target.read_text() == "the operator edited this mid-run\n"

    def test_node_id_resolution_reads_the_collect_outcome(self, monkeypatch) -> None:
        class _Proc:
            def __init__(self, rc: int, out: str) -> None:
                self.returncode, self.stdout, self.stderr = rc, out, ""

        monkeypatch.setattr(review_gate_mutate, "_pytest", lambda *a: _Proc(0, "collected 1 item"))
        assert review_gate_mutate._node_id_resolves("tests/x.py::t") is True
        monkeypatch.setattr(
            review_gate_mutate, "_pytest", lambda *a: _Proc(4, "ERROR: no tests ran")
        )
        assert review_gate_mutate._node_id_resolves("tests/x.py::gone") is False

    @pytest.mark.parametrize(
        ("run_status", "expect_clean", "detail_fragment"),
        [
            ("SURVIVED", True, ""),
            ("KILLED", False, "does NOT pass"),
            ("UNPROVEN", False, "skips on unmutated source"),
        ],
    )
    def test_baseline_reads_the_unmutated_run(
        self, monkeypatch, run_status: str, expect_clean: bool, detail_fragment: str
    ) -> None:
        monkeypatch.setattr(review_gate_mutate, "_BASELINE_CACHE", {})
        monkeypatch.setattr(review_gate_mutate, "_run_test", lambda node_id: (run_status, "detail"))
        clean, detail = review_gate_mutate._baseline_is_clean("tests/x.py::t")
        assert clean is expect_clean
        assert detail_fragment in detail

    def test_main_refuses_a_file_escaping_the_api_root(self, tmp_path, monkeypatch, capsys) -> None:
        manifest = tmp_path / "manifest.toml"
        manifest.write_text(
            "[[mutation]]\n"
            'name = "escape attempt"\n'
            'file = "../../etc/hosts"\n'
            'find = "x"\nreplace = "y"\n'
            'must_fail = "tests/x.py::t"\n'
        )
        monkeypatch.setattr(review_gate_mutate, "MANIFEST", manifest)
        monkeypatch.setattr(review_gate_mutate.sys, "argv", ["review_gate_mutate.py"])
        rc = review_gate_mutate.main()
        out = capsys.readouterr().out
        assert rc == 1
        assert "escapes" in out

    def test_main_refuses_a_must_fail_that_selects_no_test(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        manifest = tmp_path / "manifest.toml"
        manifest.write_text(
            "[[mutation]]\n"
            'name = "renamed test"\n'
            'file = "scripts/review_gate.py"\n'
            'find = "def main"\nreplace = "def main"\n'
            'must_fail = "tests/test_nowhere.py::TestGone::test_gone"\n'
        )

        class _Proc:
            returncode, stdout, stderr = 4, "ERROR: no tests ran", ""

        monkeypatch.setattr(review_gate_mutate, "MANIFEST", manifest)
        monkeypatch.setattr(review_gate_mutate, "_pytest", lambda *a: _Proc())
        monkeypatch.setattr(review_gate_mutate.sys, "argv", ["review_gate_mutate.py"])
        rc = review_gate_mutate.main()
        out = capsys.readouterr().out
        assert rc == 1
        assert "selects no test" in out
