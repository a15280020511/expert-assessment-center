from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_task_delivery_contract as contract_policy  # noqa: E402
from v5_task_envelope import build_task_envelope, work_output_contract  # noqa: E402

HEADINGS = ["已知条件", "比较结果", "条件化建议", "风险与不确定性"]
TASK = (
    "某机构只能选择一个实施方案。\n\n"
    "执行要求：\n"
    "- 必须包含四个二级标题：已知条件、比较结果、条件化建议、风险与不确定性\n"
    "- 最终报告不得引入题面外事实"
)


class MustContainH2ContractTests(unittest.TestCase):
    def test_concrete_chinese_h2_wording_is_extracted(self) -> None:
        explicit = contract_policy.extract_explicit_markdown_contract(TASK)
        self.assertEqual(explicit["exact_markdown_headings"], HEADINGS)
        self.assertEqual(
            explicit["contract_extraction_policy"],
            "explicit-format-text-only-must-contain-h2",
        )

    def test_only_final_node_inherits_user_headings(self) -> None:
        final = work_output_contract(
            TASK,
            ["直接结论", "推理链", "行动方案"],
            final_node=True,
        )
        internal = work_output_contract(
            TASK,
            ["核心判断", "关键证据"],
            final_node=False,
        )
        self.assertEqual(final["required_fields"], HEADINGS)
        self.assertEqual(final["exact_markdown_headings"], HEADINGS)
        self.assertEqual(internal["required_fields"], ["核心判断", "关键证据"])
        self.assertNotIn("exact_markdown_headings", internal)

    def test_task_envelope_preserves_exact_final_contract(self) -> None:
        envelope = build_task_envelope(
            TASK,
            minimum_context_length=16_384,
            maximum_completion_tokens=6_144,
        )
        explicit = envelope["explicit_delivery_contract"]
        self.assertEqual(explicit["required_fields"], HEADINGS)
        self.assertEqual(explicit["exact_markdown_headings"], HEADINGS)

    def test_trailing_requirement_is_not_absorbed(self) -> None:
        task = (
            "必须包含四个二级标题：已知条件、比较结果、条件化建议、风险与不确定性；"
            "每节不得为空；不得调用外部工具。"
        )
        explicit = contract_policy.extract_explicit_markdown_contract(task)
        self.assertEqual(explicit["exact_markdown_headings"], HEADINGS)


if __name__ == "__main__":
    unittest.main()
