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
        self.assertNotIn("OPENROUTER_API_KEY", self.text)

    def test_gate_uses_task_independent_changed_paths(self):
        for fragment in (
            '- "open-model-market/**"',
            '- "tests/**"',
            '- "requirements*.txt"',
            '- ".github/workflows/promote-v5-production.yml"',
        ):
            self.assertIn(fragment, self.text)
        self.assertNotIn("pull_request_target:", self.text)
        self.assertNotIn("issue_comment:", self.text)

    def test_zero_cost_validation_precedes_fail_closed_state(self):
        ordered = (
            "name: Install pinned validation dependencies",
            "name: Run canonical static and unit gates",
            "name: Verify cleaned advisory architecture",
            "name: Run task-independent advisory matrix",
            "name: Enforce fail-closed promotion state",
            "name: Upload qualification diagnostics",
        )
        positions = [self.text.index(value) for value in ordered]
        self.assertEqual(positions, sorted(positions))

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
        self.assertIn("ruff==0.16.0", (ROOT / "requirements-dev.txt").read_text())
        self.assertIn('select = ["E4", "E7", "E9", "F"]', self.ruff_text)

    def test_matrix_exercises_generic_shapes(self):
        for key in ('"simple":', '"contract":', '"complex":', '"closed_world":', '"long":'):
            self.assertIn(key, self.text)
        self.assertIn("open-model-market/v5_price_ranked_pipeline.py", self.text)
        self.assertIn('dry["status"] == "validated-not-executed"', self.text)
        self.assertIn('dry["model_calls"] == 0', self.text)
        self.assertNotIn("tabletop", self.text.casefold())
        self.assertNotIn("v5-adaptive-search.json", self.text)
        self.assertNotIn("v5-optimization.json", self.text)

    def test_matrix_enforces_governance_owned_selection(self):
        required = (
            "decision-system-governance",
            'selection["model_selection_performed_locally"] is False',
            'selection["model_reranking_performed_locally"] is False',
            'selection["model_substitution_performed_locally"] is False',
            'selection["provider_resolution_performed_locally"] is True',
            'runtime["model_loop_allowed"] is False',
            'runtime["governance_model_calls"] == 0',
        )
        for fragment in required:
            self.assertIn(fragment, self.text)
        for forbidden in ("~openai/gpt-latest", "claude-opus", "gpt_synthesis_calls"):
            self.assertNotIn(forbidden, self.text.casefold())

    def test_gate_proves_old_local_planning_algorithms_are_absent(self):
        self.assertIn("removed=(", self.text)
        self.assertIn('for path in "${removed[@]}"; do test ! -e "$path"; done', self.text)
        for path in (
            "open-model-market/v5_planner.py",
            "open-model-market/v5_constitutional_pipeline.py",
            "open-model-market/v5_value_optimizer.py",
            "open-model-market/v5_cross_endpoint_planner.py",
            "open-model-market/v5_operational_resilience.py",
            "open-model-market/v5_general_task_planning.py",
            "open-model-market/task_semantic_compiler.py",
            "open-model-market/resource_matrix.py",
            "open-model-market/atomic_work_graph.py",
        ):
            self.assertIn(path, self.text)
        self.assertNotIn("solver_status", self.text)
        self.assertNotIn("preselection_objective_weights", self.text)

    def test_gate_explicitly_records_production_is_not_moved(self):
        self.assertIn("Production ref movement remains disabled until explicit acceptance.", self.text)
        self.assertIn("test ! -e .release-authorized", self.text)
        self.assertNotIn("refs/heads/production", self.text)

    def test_diagnostics_are_retained_even_on_failure(self):
        for fragment in (
            "if: always()",
            "release-validation-logs/",
            "production-validation-artifacts/",
            "if-no-files-found: warn",
            "retention-days: 30",
            "group: v5-production-qualification",
            "cancel-in-progress: false",
        ):
            self.assertIn(fragment, self.text)


if __name__ == "__main__":
    unittest.main()
