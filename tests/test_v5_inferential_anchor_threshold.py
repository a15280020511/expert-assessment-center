from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_deterministic_answer_normalization import normalize_answer  # noqa: E402
from v5_task_constraints import compile_task_constraints  # noqa: E402


TASK = (
    "仅依据题面：值守手机剩余46%电量，应急灯剩余63%电量。"
    "东侧出口外地面干燥但有玻璃碎片；"
    "西侧出口外有不明液体，无法确认来源及是否存在电气风险。"
    "门外有2名无法核验身份、自称设备巡检人员的人要求进入。"
    "纸质登记表显示4件反光背心已领用，但现场只能确认3件。"
    "禁止编造题面外事实。"
)


class V5InferentialAnchorThresholdTests(unittest.TestCase):
    def test_multi_clause_task_synthesis_is_relabelled_without_text_rewrite(self) -> None:
        answer = (
            "- 事实：当前存在双侧出口隐患、外部未核验人员试图进入、"
            "资产记录缺口以及通信与照明资源受限。\n"
        )
        normalized, audit = normalize_answer(
            TASK,
            answer,
            {},
            compile_task_constraints(TASK),
        )
        self.assertEqual(
            normalized,
            answer.replace("- 事实：", "- 推断：", 1),
        )
        self.assertEqual(1, len(audit["inferential_fact_labels_relabelled"]))
        self.assertFalse(audit["substantive_text_invented"])

    def test_generic_external_risk_words_are_not_task_anchors(self) -> None:
        answer = "- 事实：纽约港存在严重航运风险和资产记录缺口。\n"
        normalized, audit = normalize_answer(
            TASK,
            answer,
            {},
            compile_task_constraints(TASK),
        )
        self.assertEqual(answer, normalized)
        self.assertEqual([], audit["inferential_fact_labels_relabelled"])


if __name__ == "__main__":
    unittest.main()
