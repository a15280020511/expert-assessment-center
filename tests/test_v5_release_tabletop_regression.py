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

    def test_release_gate_requires_four_work_fail_closed_semantics(self) -> None:
        required = (
            "--maximum-total-calls 6",
            "--maximum-recovery-calls 1",
            "--cost-anomaly-usd 0.25",
            "closed_book_tabletop_decomposition_applied",
            "minimum_distinct_model_companies",
            "explicit_markdown_contract",
            "fail_closed_on_quality_gate",
            "len(graph['nodes']) == 4",
            "len(selected_companies) == 4",
            "global_monkey_patching",
            "cross_task_history_used",
        )
        for fragment in required:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.text)


if __name__ == "__main__":
    unittest.main()
