from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_task_constraints import (  # noqa: E402
    fact_claim_supported,
    normalized_quantities,
    validate_answer_evidence,
)

TASK = (
    "仅依据题面：纸质登记表显示4件反光背心已领用，但现场只能确认3件。"
    "门外有2名无法核验身份、自称设备巡检人员的人要求进入。"
    "值守手机剩余46%电量。"
    "禁止联网、调用工具或编造题面外事实。"
)


class V5QuantityBindingContextTests(unittest.TestCase):
    def test_common_chinese_item_classifiers_are_normalized(self) -> None:
        for rendered in (
            "4件",
            "4台",
            "4部",
            "4套",
            "4支",
            "4辆",
            "4本",
            "4份",
            "4箱",
            "4包",
            "4瓶",
            "4枚",
            "4张",
            "4把",
            "4只",
            "4艘",
            "4架",
            "4顶",
        ):
            with self.subTest(rendered=rendered):
                self.assertEqual({("4", "", "item")}, normalized_quantities(rendered))

    def test_reordered_quantity_fact_remains_supported(self) -> None:
        for claim in (
            "反光背心已领用4件",
            "现场只能确认3件",
            "反光背心已领用4件，现场只能确认3件",
            "门外有2名自称设备巡检人员",
            "手机剩余电量46%",
        ):
            with self.subTest(claim=claim):
                self.assertTrue(fact_claim_supported(TASK, claim))
                self.assertEqual(
                    [],
                    validate_answer_evidence(TASK, f"- 事实：{claim}。\n"),
                )

    def test_wrong_quantity_cannot_pass_generic_similarity(self) -> None:
        for claim in (
            "登记表显示3件反光背心已领用",
            "现场确认4件反光背心",
            "门内有2名设备巡检人员",
            "手机剩余电量64%",
        ):
            with self.subTest(claim=claim):
                self.assertFalse(fact_claim_supported(TASK, claim))
                self.assertTrue(
                    validate_answer_evidence(TASK, f"- 事实：{claim}。\n")
                )


if __name__ == "__main__":
    unittest.main()
