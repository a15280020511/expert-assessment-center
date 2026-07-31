from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_budget_runtime_parity import planning_raw_budget_usd  # noqa: E402
from v5_general_task_planning import (  # noqa: E402
    classify_task,
    compile_task_semantics,
)
from v5_pipeline import _planning_limits  # noqa: E402
from v5_runtime import ProductionRuntime, RuntimeConfig  # noqa: E402


REAL_TASK = (
    "真实决策任务：用户在福州市担任小学夜班保安，独自在岗亭值班；"
    "岗亭无法申请学校有线网络或室内Wi-Fi，但移动4G/5G流量可用；"
    "每月流量需求约100GB以上；希望长期月成本尽量控制在20元人民币左右，"
    "可以接受一次性购买随身Wi-Fi设备。请比较方案A：继续使用手机热点，"
    "与方案B：购买并使用随身Wi-Fi。仅依据题面已知条件分析，不调用外部工具、"
    "不联网、不编造具体套餐价格。必须给出：1）关键假设与未知信息；"
    "2）一次性成本和月成本的比较公式及盈亏平衡阈值；"
    "3）稳定性、信号、电池、发热、携带、维护和故障风险；"
    "4）什么条件下选A、什么条件下选B；5）明确推荐；"
    "6）一个低成本、可撤销的7天验证步骤。事实、假设和推断必须分开。"
)


class V5ConsumerDecisionBudgetTests(unittest.TestCase):
    @staticmethod
    def run_config() -> SimpleNamespace:
        return SimpleNamespace(
            task=REAL_TASK,
            minimum_context_length=16384,
            max_completion_tokens=3000,
        )

    def test_consumer_failure_risk_does_not_become_coding_research_or_high_stakes(self) -> None:
        run = self.run_config()
        profile = classify_task(REAL_TASK, run)
        self.assertEqual("business", profile.primary_domain)
        self.assertIn("business", profile.domains)
        self.assertNotIn("coding", profile.domains)
        self.assertNotIn("research", profile.domains)
        self.assertFalse(profile.high_stakes)

    def test_real_task_has_a_three_initial_call_interpretation(self) -> None:
        run = self.run_config()
        profile = classify_task(REAL_TASK, run)
        compilation = compile_task_semantics(profile, run)
        signals = compilation["task_signals"]
        self.assertEqual(["business"], signals["active_domains"])
        minimum_calls = min(
            sum(
                int(work["independence_requirements"]["minimum_independent_copies"])
                for work in interpretation["atomic_work"]
            )
            for interpretation in compilation["interpretations"]
        )
        self.assertLessEqual(minimum_calls, 3)
        self.assertEqual(1, len(compilation["interpretations"]))
        self.assertEqual(1, len(compilation["interpretations"][0]["atomic_work"]))
        self.assertTrue(signals["cost_performance_compaction_applied"])

    def test_planner_uses_runtime_risk_multiplier(self) -> None:
        runtime = ProductionRuntime(
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=1,
                cost_anomaly_usd=0.25,
                quality_tier="value",
            )
        )
        limits = _planning_limits(
            total_calls=4,
            recovery_calls=1,
            planning_nodes=3,
            anomaly_budget=0.25,
            runtime=runtime,
        )
        self.assertAlmostEqual(1.18, limits.cost_risk_multiplier)
        self.assertAlmostEqual(0.25 / 1.18, planning_raw_budget_usd(limits))
        self.assertEqual(3, limits.max_nodes)
        self.assertEqual(4, limits.max_model_calls)
        self.assertEqual(1, limits.max_retries)
        self.assertEqual(1, limits.max_replacements)


if __name__ == "__main__":
    unittest.main()
