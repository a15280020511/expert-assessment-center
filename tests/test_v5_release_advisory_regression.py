from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-v5-production.yml"


class V5ReleaseAdvisoryRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_release_gate_does_not_import_named_task_architecture(self) -> None:
        for fragment in (
            "FAILED_PRODUCTION_TASK",
            "closed_book_tabletop_compaction_applied",
            "第五类复合事件",
            "桌面推演",
            "v5-adaptive-search.json",
            "v5-optimization.json",
            "solver_status",
            "preselection_objective_weights",
        ):
            self.assertNotIn(fragment, self.text)

    def test_release_gate_checks_governance_plan_properties(self) -> None:
        required = (
            "task-independent advisory matrix",
            "decision-system-governance",
            "v5_governance_model_plan.py",
            "v5_governed_plan_orchestrator.py",
            "model_selection_performed_locally",
            "model_reranking_performed_locally",
            "model_substitution_performed_locally",
            "provider_resolution_performed_locally",
            "model_loop_allowed",
        )
        for fragment in required:
            self.assertIn(fragment, self.text)
        for forbidden in ("~openai/gpt-latest", "~anthropic/claude-opus-latest"):
            self.assertNotIn(forbidden, self.text)

    def test_release_gate_remains_read_only(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("test ! -e .release-authorized", self.text)
        self.assertNotIn("git push", self.text)
        self.assertNotIn("refs/heads/production", self.text)
        self.assertNotIn("OPENROUTER_API_KEY", self.text)


if __name__ == "__main__":
    unittest.main()
