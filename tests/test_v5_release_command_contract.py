import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-v5-production.yml"
VALIDATE_WORKFLOW = ROOT / ".github" / "workflows" / "validate.yml"
RUFF_CONFIG = ROOT / "ruff.toml"


class TestV5ReleaseCommandContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.validate_text = VALIDATE_WORKFLOW.read_text(encoding="utf-8")
        cls.ruff_text = RUFF_CONFIG.read_text(encoding="utf-8")

    def test_gate_is_triggerable_but_cannot_promote(self):
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("pull_request:", self.text)
        self.assertIn("branches: [main]", self.text)
        self.assertIn('permissions:\n  contents: read', self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("git push", self.text)
        self.assertNotIn("update-ref", self.text)
        self.assertNotIn("--force-with-lease", self.text)
        self.assertNotIn("OPENROUTER_API_KEY", self.text)

    def test_gate_uses_task_independent_changed_paths(self):
        self.assertIn('- "open-model-market/**"', self.text)
        self.assertIn('- "tests/**"', self.text)
        self.assertIn(
            '- ".github/workflows/promote-v5-production.yml"',
            self.text,
        )
        self.assertNotIn(".release-request.json", self.text)
        self.assertNotIn("v5-production-release", self.text)
        self.assertNotIn("pull_request_target:", self.text)
        self.assertNotIn("issue_comment:", self.text)

    def test_zero_cost_validation_precedes_fail_closed_state(self):
        install = self.text.index(
            "name: Install pinned validation dependencies"
        )
        canonical = self.text.index(
            "name: Run canonical static and unit gates"
        )
        matrix = self.text.index(
            "name: Run task-independent constitutional matrix"
        )
        closed = self.text.index(
            "name: Enforce fail-closed promotion state"
        )
        diagnostics = self.text.index(
            "name: Upload qualification diagnostics"
        )
        self.assertLess(install, canonical)
        self.assertLess(canonical, matrix)
        self.assertLess(matrix, closed)
        self.assertLess(closed, diagnostics)

    def test_pull_request_and_gate_use_identical_canonical_commands(self):
        commands = (
            "python -m ruff check .",
            "python -m compileall -q open-model-market tests tools",
            "python -m unittest discover -s tests -p 'test*.py' -v",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.text.count(command), 1)
                self.assertEqual(self.validate_text.count(command), 1)
        self.assertNotIn("--select E9,F63,F7,F82", self.validate_text)
        self.assertIn('python-version: "3.12"', self.text)
        self.assertIn('python-version: "3.12"', self.validate_text)
        self.assertIn(
            "ruff==0.16.0",
            (ROOT / "requirements-dev.txt").read_text(),
        )
        self.assertIn('select = ["E4", "E7", "E9", "F"]', self.ruff_text)

    def test_matrix_exercises_generic_shapes_not_named_cases(self):
        self.assertIn('"simple":', self.text)
        self.assertIn('"contract":', self.text)
        self.assertIn('"complex":', self.text)
        self.assertIn(
            'signals["task_specific_production_branching"] is False',
            self.text,
        )
        self.assertIn(
            'signals["case_derived_compaction_applied"] is False',
            self.text,
        )
        self.assertIn(
            'signals["architecture_selection_policy"] == "generic-semantic-matrix-only"',
            self.text,
        )
        self.assertIn(
            'search["policy"] == "task-shape-feasibility-marginal-value"',
            self.text,
        )
        self.assertIn(
            'complexity["complex"] >= complexity["simple"]',
            self.text,
        )
        self.assertNotIn("tabletop", self.text.casefold())

    def test_matrix_enforces_company_uniqueness_and_no_monkey_patch(self):
        self.assertIn(
            "len(companies) == len(set(companies))",
            self.text,
        )
        self.assertIn(
            'dry["model_company_policy"] == "task-global-all-different"',
            self.text,
        )
        self.assertIn('dry["global_monkey_patching"] is False', self.text)
        self.assertIn('"cross_task_history_used": False', self.text)

    def test_gate_explicitly_records_production_is_not_moved(self):
        self.assertIn(
            "Production ref movement remains disabled until paid generic acceptance is attached.",
            self.text,
        )
        self.assertIn("test ! -e .release-authorized", self.text)
        self.assertNotIn("refs/heads/production", self.text)
        self.assertNotIn("release-receipt", self.text)

    def test_diagnostics_are_retained_even_on_failure(self):
        self.assertIn("if: always()", self.text)
        self.assertIn("release-validation-logs/", self.text)
        self.assertIn("production-validation-artifacts/", self.text)
        self.assertIn("if-no-files-found: warn", self.text)
        self.assertIn("retention-days: 30", self.text)
        self.assertIn("group: v5-production-qualification", self.text)
        self.assertIn("cancel-in-progress: false", self.text)


if __name__ == "__main__":
    unittest.main()
