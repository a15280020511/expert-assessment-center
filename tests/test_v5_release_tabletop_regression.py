from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-v5-production.yml"


class V5ReleaseTabletopRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_release_gate_reuses_exact_failed_production_task(self) -> None:
        self.assertIn(
            "from tests.test_v5_tabletop_production_semantics import FAILED_PRODUCTION_TASK",
            self.text,
        )
        self.assertNotIn(
            "比较三个公共投资方案，完成财务、合规、证据、预测、红队和最终决策",
            self.text,
        )

    def test_release_gate_keeps_original_budget_and_semantic_contract(self) -> None:
        required = (
            "--maximum-total-calls 4",
            "--maximum-recovery-calls 1",
            "--cost-anomaly-usd 0.25",
            "closed_book_tabletop_compaction_applied",
            "external_evidence_required",
            "matrix['matrices'][0]['hard_requirements'] == []",
            "optimization['solver_status'] in {'OPTIMAL', 'FEASIBLE'}",
            "1 <= len(graph['nodes']) <= 3",
            "global_monkey_patching",
            "cross_task_history_used",
        )
        for fragment in required:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.text)


if __name__ == "__main__":
    unittest.main()
