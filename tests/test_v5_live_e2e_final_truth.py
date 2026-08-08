from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_final_audit_hardening import rewrite_request_audit_assignment_truth  # noqa: E402
from v5_final_semantic_gate import (  # noqa: E402
    arithmetic_consistency_violations,
    production_task_obligation_violations,
)

TASK = """一家企业只能选A、B、C、D一种方案。请完成：1）计算四种方案总成本；2）分别给出预算优先、可靠性优先、综合均衡推荐；3）推导各方案之间的关键切换临界值；4）分别在当前值的50%、100%、150%时重新比较；5）如果故障期望次数整体同时高估或低估30%，判断推荐是否改变；6）做反例检查；7）最后给出可复核的决策表，将题面事实、派生计算和判断分开。"""

FALSE_GREEN = """## 核心判断
方案B当前总成本最低。

## 关键依据
- A与B切换临界值：8800+4.4x = 12800+2.2x ⇒ x ≈ 1818.18元。
- B与C切换临界值：12800+2.2x = 17600+0.88x ⇒ x ≈ 3636.36元。
- 50%情景：方案A最优。
- 150%情景：方案B最优。
- 高估30%（实际为预期的1.3倍）：方案B最优。
- 低估30%（实际为预期的0.7倍）：方案A最优。

## 不确定性与反例
故障率变化可能推翻结论。

## 可执行结论
预算优先推荐B；可靠性优先推荐D；综合均衡推荐B。决策表交付要求：下游节点需另行制作。
"""

COMPLIANT = """## 核心判断
当前比较完成。

## 关键依据
- A与B切换临界值：8800+4.4x = 12800+2.2x ⇒ x ≈ 1818.18元。
- B与C切换临界值：12800+2.2x = 17600+0.88x ⇒ x ≈ 3636.36元。
- C与D切换临界值：17600+0.88x = 22800+0.4x ⇒ x ≈ 10833.33元。
- 50%情景：重新比较完成。
- 100%情景：重新比较完成。
- 150%情景：重新比较完成。
- 高估30%（实际为预期的70%）：重新比较完成。
- 低估30%（实际为预期的130%）：重新比较完成。

| 类型 | 方案 | 内容 |
|---|---|---|
| 题面事实 | A | 已知输入 |
| 派生计算 | B | 计算结果 |
| 判断 | C | 推荐结论 |

## 不确定性与反例
已检查反例。

## 可执行结论
预算优先推荐A；可靠性优先推荐D；综合均衡推荐B。
"""


class LiveE2EFinalTruthTests(unittest.TestCase):
    def test_live_false_green_is_rejected_for_missing_explicit_obligations(self) -> None:
        violations = production_task_obligation_violations(TASK, FALSE_GREEN)
        self.assertTrue(
            any("explicit-scenario-percentage:100%" in value for value in violations),
            violations,
        )
        self.assertIn("missing-task-obligation:decision-table", violations)
        self.assertTrue(
            any("threshold-transition-coverage:D" in value for value in violations),
            violations,
        )
        self.assertTrue(
            any("high-estimate-actual-not-lower" in value for value in violations),
            violations,
        )
        self.assertTrue(
            any("low-estimate-actual-not-higher" in value for value in violations),
            violations,
        )

    def test_compliant_delivery_passes_new_live_obligations(self) -> None:
        violations = production_task_obligation_violations(TASK, COMPLIANT)
        interesting = [
            value
            for value in violations
            if "explicit-scenario-percentage" in value
            or "decision-table" in value
            or "threshold-transition-coverage" in value
            or "scenario-direction-inconsistency" in value
        ]
        self.assertEqual([], interesting)

    def test_chained_correct_linear_thresholds_do_not_false_positive(self) -> None:
        answer = (
            "A与B：6000+2800+4.4L = 9000+3800+2.2L → "
            "8800+4.4L = 12800+2.2L → 2.2L = 4000 → L_AB = 1818.18元\n"
            "C与D：17600+0.88L = 22800+0.4L → 0.48L = 5200 → "
            "L_CD = 10833.33元"
        )
        self.assertEqual([], arithmetic_consistency_violations(answer))

    def test_wrong_final_linear_threshold_is_rejected(self) -> None:
        answer = "A与B：8800+4.4L = 12800+2.2L → L_AB = 1900元"
        violations = arithmetic_consistency_violations(answer)
        self.assertTrue(
            any(value.startswith("linear-threshold-inconsistency:") for value in violations),
            violations,
        )

    def test_final_request_audit_uses_materialized_expert_assignment_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {
                "candidate_pool_authority": "decision-system-governance",
                "model_assignment_authority": (
                    "expert-assessment-center-current-ticket-generated-parameter-ortools"
                ),
                "selection_performed_by_governance": False,
                "model_substitution_allowed": True,
                "optimizer": "ortools-cp-sat",
                "optimizer_audit": {"optimality_proven": True},
            }
            (root / "governance-model-plan.json").write_text(
                json.dumps(plan), encoding="utf-8"
            )
            (root / "v5-request-audit.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "selection_authority": "decision-system-governance",
                        "model_selection_performed_locally": False,
                        "runtime_knob_coverage_status": "PASS",
                    }
                ),
                encoding="utf-8",
            )
            audit = rewrite_request_audit_assignment_truth(root)
            self.assertEqual(
                "decision-system-governance",
                audit["candidate_pool_authority"],
            )
            self.assertTrue(
                audit["selection_authority"].startswith("expert-assessment-center")
            )
            self.assertTrue(audit["model_selection_performed_locally"])
            self.assertTrue(audit["current_task_assignment_is_expert_owned"])
            self.assertEqual("PASS", audit["assignment_truth_status"])


if __name__ == "__main__":
    unittest.main()
