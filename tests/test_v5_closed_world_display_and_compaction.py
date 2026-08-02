from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_constitutional_runtime import ConstitutionalPromptPolicy  # noqa: E402
from v5_task_constraints import (  # noqa: E402
    closed_world_numeric_prompt,
    compile_task_constraints,
)


class ClosedWorldDisplayAndCompactionTests(unittest.TestCase):
    def test_prompt_preserves_original_chinese_units(self) -> None:
        task = "闭卷，不得编造。只有2名值守；库存表6顶，现场5顶。"
        prompt = closed_world_numeric_prompt(task, compile_task_constraints(task))
        self.assertIn("2名", prompt)
        self.assertIn("6顶", prompt)
        self.assertIn("5顶", prompt)
        self.assertNotIn("2:people", prompt)
        self.assertNotIn("6:item", prompt)

    def test_upstream_compaction_removes_only_duplicate_raw_mirror(self) -> None:
        contract = {
            "validated_claims": ["事实A"],
            "conclusions": ["结论B"],
            "raw_fields": {"validated_claims": "事实A", "conclusions": "结论B"},
            "schema_version": "v5-node-result-1",
        }
        compact = ConstitutionalPromptPolicy._compact_upstream_contract(contract)
        self.assertNotIn("raw_fields", compact)
        self.assertEqual(["事实A"], compact["validated_claims"])
        self.assertEqual(["结论B"], compact["conclusions"])
        self.assertEqual("v5-node-result-1", compact["schema_version"])


if __name__ == "__main__":
    unittest.main()
