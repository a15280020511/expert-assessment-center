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
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("git push", self.text)
        self.assertNotIn("update-ref", self.text)
        self.assertNotIn("--force-with-lease", self.text)
        self.assertNotIn("OPENROUTER_API_KEY", self.text)

    def test_gate_uses_task_independent_changed_paths(self):
        self.assertIn('- "open-model-market/**"', self.text)
        self.assertIn('- "tests/**"', self.text)
        self.assertIn('- "requirements*.txt"', self.text)
        self.assertIn(
            '- ".github/workflows/promote-v5-production.yml"',
            self.text,
        )
        self.assertNotIn("pull_request_target:", self.text)
        self.assertNotIn("issue_comment:", self.text)

    def test_zero_cost_validation_precedes_fail_closed_state(self):
        install = self.text.index(
            "name: Install pinned validation dependencies"
        )
        canonical = self.text.index(
            "name: Run canonical static and unit gates"
        )
        architecture = self.text.index(
            "name: Verify cleaned advisory architecture"
        )
        matrix = self.text.index(
            "name: Run task-independent advisory matrix"
        )
        closed = self.text.index(
            "name: Enforce fail-closed promotion state"
        )
        diagnostics = self.text.index(
            "name: Upload qualification diagnostics"
        )
        self.assertLess(install, canonical)
        self.assertLess(canonical, architecture)
        self.assertLess(architecture, matrix)
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
        self.assertIn('python-version: "3.12"', self.text)
        self.assertIn('python-version: "3.12"', self.validate_text)
        self.assertIn(
            "ruff==0.16.0",
            (ROOT / "requirements-dev.txt").read_text(),
        )
        self.assertIn('select = ["E4", "E7", "E9", "F"]', self.ruff_text)

    def test_matrix_exercises_generic_shapes(self):
        for key in ('"simple":', '"contract":', '"complex":', '"closed_world":'):
            self.assertIn(key, self.text)
        self.assertIn("open-model-market/v5_pipeline.py", self.text)
        self.assertIn('dry["status"] == "validated-not-executed"', self.text)
        self.assertIn('dry["model_calls"] == 0', self.text)
        self.assertIn('constraints["fail_closed"] is True', self.text)
        self.assertNotIn("tabletop", self.text.casefold())
        self.assertNotIn("v5-adaptive-search.json", self.text)
        self.assertNotIn("v5-optimization.json", self.text)

    def test_matrix_enforces_advisory_only_governance(self):
        required = (
            "~openai/gpt-latest",
            "~anthropic/claude-opus-latest",
            'dry["claude_calls_per_task"] == 1',
            'dry["claude_is_advisory_only"] is True',
            'dry["claude_gatekeeping_allowed"] is False',
            'dry["gpt_synthesis_calls"] == 1',
            'dry["second_claude_review_allowed"] is False',
            'runtime["final_authority"] == "deterministic-constitutional-validator"',
            'runtime["model_loop_allowed"] is False',
        )
        for fragment in required:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.text)

    def test_gate_proves_local_selection_algorithms_are_absent(self):
        for path in (
            "v5_planner.py",
            "v5_constitutional_pipeline.py",
            "v5_value_optimizer.py",
            "v5_cross_endpoint_planner.py",
            "v5_operational_resilience.py",
        ):
            self.assertIn(f"test ! -e open-model-market/{path}", self.text)
        self.assertIn('dry["local_scoring_used"] is False', self.text)
        self.assertIn('dry["optimizer_used"] is False', self.text)
        self.assertIn('dry["cp_sat_used"] is False', self.text)
        self.assertNotIn("solver_status", self.text)
        self.assertNotIn("preselection_objective_weights", self.text)

    def test_gate_explicitly_records_production_is_not_moved(self):
        self.assertIn(
            "Production ref movement remains disabled until explicit acceptance.",
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
