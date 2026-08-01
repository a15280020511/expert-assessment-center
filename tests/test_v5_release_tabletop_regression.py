from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-v5-production.yml"


class V5ReleaseConstitutionalRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_release_gate_does_not_import_a_named_task_fixture(self) -> None:
        forbidden = (
            "FAILED_PRODUCTION_TASK",
            "test_v5_tabletop_production_semantics",
            "closed_book_tabletop_compaction_applied",
            "第五类复合事件",
            "桌面推演",
        )
        for fragment in forbidden:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, self.text)

    def test_release_gate_checks_generic_properties(self) -> None:
        required = (
            "task_specific_production_branching",
            "case_derived_compaction_applied",
            "v5-adaptive-search.json",
            "explicit_output_contract",
            "model_company_policy",
            "global_monkey_patching",
            "cross_task_history_used",
        )
        for fragment in required:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.text)


if __name__ == "__main__":
    unittest.main()
