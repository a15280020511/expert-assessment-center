from __future__ import annotations

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_run387_hardening import (  # noqa: E402
    HeterogeneousEvidenceExecutionEngine,
    arithmetic_consistency_violations,
    hardened_normalize_answer,
    hardened_validate_answer_evidence,
    task_obligation_violations,
)
from v5_task_constraints import compile_task_constraints  # noqa: E402
from v5_runtime import FailureCategory  # noqa: E402


TASK = """某小型仓储点需要在 A/B/C 三种备用电源方案中选择一种，计划使用 3 年。已知：A 购置价 4800 元，额定可用电量 5.0kWh，预计每年发生 0.9 次无法满足停电需求的事件，单次业务损失 1800 元，三年维护费合计 600 元；B 购置价 7200 元，额定可用电量 8.0kWh，预计每年发生 0.35 次无法满足停电需求的事件，单次业务损失 1800 元，三年维护费合计 900 元；C 购置价 9800 元，额定可用电量 12.0kWh，预计每年发生 0.12 次无法满足停电需求的事件，单次业务损失 1800 元，三年维护费合计 1200 元。假定残值均为 0，暂不考虑资金时间价值。请分别给出：预算优先、可靠性优先、综合均衡三种目标下的推荐；计算三年期预期总成本；指出哪些结论对‘单次业务损失’这一参数最敏感，并给出关键临界值或切换条件。最后说明如果业务损失估计存在 ±50% 误差，推荐是否会改变。必须展示可复核的三年期预期成本计算；必须区分确定数据、计算结果与判断；不得调用外部工具或补充题目之外的数据。"""


RUN387_FALSE_GREEN = """## 核心判断

**三年期预期总成本（题面数据+派生计算）：**

**三个目标下的推荐判断：**
- 可靠性优先：**选C**（故障频率最低0.12次/年，可用电量12.0kWh最充足）

**单次业务损失敏感性临界值：**

**±50%业务损失误差下的变化：**

## 关键依据

**事实数据来源（题面原样）：** 使用年限3年、购置费、年故障频率和维护费。
**成本计算公式应用（显式推导）：** 三年总成本 = 购置价 + 维护费总和 + 年故障频率 × 年数 × 单次损失

## 不确定性与反例

±50%误差下，综合均衡推荐B方案在1800±900元范围内保持可接受。

## 可执行结论

1. 预算优先→选A方案
2. 综合均衡→选B方案
3. 可靠性优先→选C方案
"""


COMPLETE = """## 核心判断

### 确定数据
题面给定3年、A/B/C购置费、维护费、年故障频率和单次损失1800元。

### 计算结果
A：4800+600+3×0.9×1800=10260元。
B：7200+900+3×0.35×1800=9990元。
C：9800+1200+3×0.12×1800=11648元。

预算优先：推荐A；可靠性优先：推荐C；综合均衡：推荐B。

### 敏感性与临界值
令单次损失为L。A=5400+2.7L，B=8100+1.05L，C=11000+0.36L。
A=B：2.7L+5400=1.05L+8100，L≈1636元/次。
B=C：1.05L+8100=0.36L+11000，L≈4203元/次。

±50%情景：L=900元/次时，A=7830元、B=9045元、C=11324元；L=2700元/次时，A=12690元、B=10935元、C=11972元。综合均衡仍可优先B，但纯成本最低方案从A切换到B。

## 关键依据
以上数值均由题面数据代入同一成本函数得到。

## 不确定性与反例
判断只在题面故障频率和损失模型成立时有效。

## 可执行结论
预算优先选A；可靠性优先选C；综合均衡选B，并在L跨越上述临界值时重算。
"""


class Run387HardeningTests(unittest.TestCase):
    def test_false_green_report_is_rejected_for_missing_semantic_obligations(self) -> None:
        violations = task_obligation_violations(TASK, RUN387_FALSE_GREEN)
        self.assertIn("missing-task-obligation:auditable-numeric-calculation", violations)
        self.assertIn("missing-task-obligation:derived-threshold-or-switch-condition", violations)
        self.assertIn("missing-task-obligation:two-sided-error-scenarios", violations)

    def test_complete_answer_satisfies_task_obligation_layer(self) -> None:
        violations = task_obligation_violations(TASK, COMPLETE)
        self.assertEqual([], violations)

    def test_normalizer_preserves_proven_derived_quantities_and_removes_inventions(self) -> None:
        constraints = compile_task_constraints(TASK)
        answer = """## 核心判断
A：4800+600+3×0.9×1800=10260元。
建议采集至少6个月数据，若偏离>30%则调整。
## 关键依据
计算均来自题面。
## 不确定性与反例
无外部数据。
## 可执行结论
预算优先选A。
"""
        normalized, audit = hardened_normalize_answer(
            TASK,
            answer,
            {"required_fields": ["核心判断", "关键依据", "不确定性与反例", "可执行结论"]},
            constraints,
        )
        self.assertIn("10260", normalized)
        self.assertNotIn("6个月", normalized)
        self.assertNotIn("30%", normalized)
        self.assertGreaterEqual(audit["preserved_derived_quantity_line_count"], 1)
        self.assertGreaterEqual(audit["removed_line_count"], 1)

    def test_explicit_arithmetic_error_from_run387_is_detected(self) -> None:
        wrong = """A：4800+600+3×0.9×900=6570元。
A=B：2.7L+3060=1.05L+8100 → L≈5533元/次。"""
        violations = arithmetic_consistency_violations(wrong)
        self.assertTrue(any(value.startswith("arithmetic-inconsistency:") for value in violations))
        self.assertTrue(any(value.startswith("linear-threshold-inconsistency:") for value in violations))

    def test_full_hardened_evidence_gate_rejects_run387_false_green(self) -> None:
        constraints = compile_task_constraints(TASK)
        violations = hardened_validate_answer_evidence(
            TASK,
            RUN387_FALSE_GREEN,
            constraints,
        )
        self.assertTrue(any(value.startswith("missing-task-obligation:") for value in violations))

    def test_company_heterogeneity_precedes_cost_only_after_quality_and_risk(self) -> None:
        tried = {"aion-labs"}
        repeated_cheaper = {
            "model": "aion-labs/aion-3.0",
            "estimated_quality": 0.8,
            "failure_probability": 0.1,
            "estimated_cost": 0.001,
        }
        new_company = {
            "model": "anthropic/claude-haiku-4.5",
            "estimated_quality": 0.8,
            "failure_probability": 0.1,
            "estimated_cost": 0.01,
        }
        better_quality_repeat = {
            "model": "aion-labs/aion-2.0",
            "estimated_quality": 0.9,
            "failure_probability": 0.1,
            "estimated_cost": 0.02,
        }
        key_repeat = HeterogeneousEvidenceExecutionEngine._failure_rank_key(
            repeated_cheaper,
            FailureCategory.QUALITY_GATE_FAILED,
            tried,
        )
        key_new = HeterogeneousEvidenceExecutionEngine._failure_rank_key(
            new_company,
            FailureCategory.QUALITY_GATE_FAILED,
            tried,
        )
        key_better = HeterogeneousEvidenceExecutionEngine._failure_rank_key(
            better_quality_repeat,
            FailureCategory.QUALITY_GATE_FAILED,
            tried,
        )
        self.assertLess(key_new, key_repeat, "new company should beat cost when quality/risk tie")
        self.assertLess(key_better, key_new, "better task quality must still beat diversity")

    def test_truncation_repair_is_implemented_before_cross_model_recovery(self) -> None:
        import inspect

        source = inspect.getsource(HeterogeneousEvidenceExecutionEngine._recover_node)
        retry_pos = source.index('call(adapted, "retry")')
        recovery_pos = source.index("DynamicExecutionEngine._recover_node")
        self.assertLess(retry_pos, recovery_pos)
        self.assertIn("same-model-feedback-rebind-before-cross-model-recovery", source)


if __name__ == "__main__":
    unittest.main()
