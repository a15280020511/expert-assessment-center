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
            "architecture_selection_policy",
            "generic-semantic-matrix-only",
            "v5-adaptive-search.json",
            "explicit_output_contract",
            "model_company_policy",
            "global_monkey_patching",
        )
        for fragment in required:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.text)

    def test_release_gate_remains_read_only(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("test ! -e .release-authorized", self.text)
        self.assertNotIn("git push", self.text)
        self.assertNotIn("refs/heads/production", self.text)


if __name__ == "__main__":
    unittest.main()
