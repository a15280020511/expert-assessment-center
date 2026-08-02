from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_task_delivery_contract as contracts  # noqa: E402
from v5_task_constraints import (  # noqa: E402
    fact_claim_supported,
    validate_answer_evidence,
)


HEADINGS = [
    "已知与未知",
    "隔离与警戒",
    "当班行动",
    "门禁处置",
    "物资复核",
    "移交条件",
]


class V5FlattenedContractCardinalityTests(unittest.TestCase):
    def test_flattened_requirements_do_not_become_h2_headings(self) -> None:
        task = (
            "最终输出必须且只能使用以下Markdown二级标题，并严格按此顺序，"
            "每节非空：已知与未知；隔离与警戒；当班行动；门禁处置；"
            "物资复核；移交条件。不得出现任何其他Markdown二级标题。 - "
            "执行要求： - 事实、假设、推断、未知四类标签必须明确区分；"
            "分类说明本身不得被当成事实声明。 - 正式事实条目使用指定前缀。"
        )
        extracted = contracts.extract_explicit_markdown_contract(task)
        self.assertEqual(extracted["exact_markdown_headings"], HEADINGS)
        self.assertEqual(extracted["task_explicit_delivery_section_count"], 6)

    def test_counted_flattened_requirements_are_trimmed_before_split(self) -> None:
        task = (
            "最终输出必须严格使用以下6个Markdown二级标题："
            "已知与未知；隔离与警戒；当班行动；门禁处置；物资复核；移交条件。"
            "必须逐项记录。 - 事实、假设、推断、未知必须区分。"
        )
        extracted = contracts.extract_explicit_markdown_contract(task)
        self.assertEqual(extracted["exact_markdown_headings"], HEADINGS)

    def test_cardinality_and_confirmation_surface_forms_are_supported(self) -> None:
        task = (
            "仅依据题面，不得编造。某小型夜间物资中转点只有3名值守人员。"
            "纸质清单显示5台手持终端已经交接，但现场只能确认4台。"
        )
        claims = (
            "现场为某小型夜间物资中转点，值守人员共3名。",
            "纸质清单显示已交接手持终端5台；现场实际可确认4台。",
        )
        for claim in claims:
            with self.subTest(claim=claim):
                self.assertTrue(fact_claim_supported(task, claim))
                answer = f"**事实｜来源：题面**：{claim}"
                self.assertEqual(validate_answer_evidence(task, answer), [])

    def test_quantity_swap_remains_rejected(self) -> None:
        task = (
            "仅依据题面，不得编造。纸质清单显示5台手持终端已经交接，"
            "但现场只能确认4台。"
        )
        answer = (
            "**事实｜来源：题面**：纸质清单显示已交接手持终端4台；"
            "现场实际可确认5台。"
        )
        self.assertTrue(validate_answer_evidence(task, answer))

    def test_spatial_promotion_remains_rejected(self) -> None:
        task = (
            "仅依据题面，不得编造。南门外闻到来源不明的焦糊味。"
            "门外有2名身份无法核验、自称设备检修人员的人要求进入。"
        )
        promoted = (
            "**事实｜来源：题面**：南门外有2名身份无法核验、"
            "自称设备检修人员的人要求进入。"
        )
        exact = (
            "**事实｜来源：题面**：门外有2名身份无法核验、"
            "自称设备检修人员的人要求进入。"
        )
        self.assertTrue(validate_answer_evidence(task, promoted))
        self.assertEqual(validate_answer_evidence(task, exact), [])


if __name__ == "__main__":
    unittest.main()
