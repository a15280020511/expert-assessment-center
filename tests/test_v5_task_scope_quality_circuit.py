from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_run387_hardening import task_obligation_violations  # noqa: E402
from v5_runtime import FailureCategory, RuntimeAttempt  # noqa: E402
from v5_task_constraints import normalized_quantities  # noqa: E402
from v5_task_scope_quality_circuit import (  # noqa: E402
    project_business_task,
    repeated_deterministic_quality_signal,
)


RUN407_TASK = """在福州，一个普通成年人如果主要目标是靠劳动获得稳定、可持续的净收入，在以下四类工作中怎么选：1）外卖骑手；2）快递员；3）保安；4）网约车司机？请做现实就业决策比较。至少比较：实际到手净收入潜力与波动、典型工时与休息、入行门槛和前期资本、车辆/设备成本与折旧、油电/维修/保险等持续成本、事故与职业风险、劳动强度、天气暴露、平台规则依赖、淡旺季与订单波动、收入稳定性、社保/劳动保障、长期可持续性、转行与技能积累。最终必须：A）给普通求职者的总体排序；B）分别给‘追求收入上限’‘追求稳定轻松’‘本金很少’‘已有合适车辆’‘只能短期过渡’五种情形的推荐；C）指出结论最敏感的现实变量和反例条件；D）给出明确决策表。若题面没有提供2026年福州实时薪资或平台抽成等精确数据，不得把未经题面或证据支持的精确数值伪装成事实，应使用定性、区间性或条件化表达并明确不确定性。

执行要求：
- 专家不得调用外部工具、浏览器、搜索或数据库，只使用题面、显式证据和模型已有一般知识；缺乏实时证据的2026年福州精确工资、抽成、订单量不得伪造
- 专家数量、角色/职业、工作分工、依赖关系、Role DAG、reasoning effort、模型组合、恢复与standby必须由当前任务动态计算，不得固定3人/4人/裁判模板
- 提示词必须采用固定宪法/安全/事实纪律骨架 + 当前任务动态角色、认知操作、任务投影、上游结果与输出合同；不得使用固定职业关键词路由
- 模型分配必须按当前任务动态求解，遵循具体问题具体分析、动态适配、小付出大回报、性价比优先；费用/Token是软优化，不得成为硬准入或结果否决门禁
- 最终完整payload组装后重新测量并绑定动态reasoning/max_tokens/effective timeout；每次真实模型尝试和质量观测后更新current-run时空状态；禁止跨任务历史
- 最终报告必须明确区分事实/一般经验、推断、条件性判断和建议，给出总体排序、五类情形推荐、关键敏感变量/反例和Markdown决策表
- 只有full/full_success/all-quality-gates-passed才算本次实战成功"""


def attempt(index: int, model: str, reasons: list[str]) -> RuntimeAttempt:
    return RuntimeAttempt(
        attempt_index=index,
        attempt_kind="replacement" if index > 1 else "initial",
        candidate_id="n1",
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        request={"model": model},
        status="quality_gate_failed",
        answer="answer",
        quality_score=0.5,
        gate_reasons=reasons,
        latency_seconds=0.1,
        usage={"completion_tokens": 50},
        response_id=f"r{index}",
        response_model=model,
        response_provider="provider",
        failure={
            "category": FailureCategory.QUALITY_GATE_FAILED.value,
            "retryable": False,
        },
    )


class TaskScopeQualityCircuitTests(unittest.TestCase):
    def test_run407_control_requirements_do_not_become_business_obligations(self) -> None:
        projected, audit = project_business_task(RUN407_TASK)
        self.assertTrue(audit["projection_applied"])
        self.assertNotIn("性价比优先", projected)
        self.assertNotIn("reasoning effort", projected)
        self.assertNotIn("full/full_success", projected)
        self.assertIn("缺乏实时证据的2026年福州精确工资", projected)
        self.assertIn("最终报告必须明确区分事实/一般经验", projected)

        answer = """总体建议：以稳定和长期可持续为目标，优先比较快递与保安；外卖更偏向收入弹性和短期过渡，网约车高度依赖已有车辆条件。\n\n|情形|建议|\n|---|---|\n|总体|快递/保安优先比较|"""
        violations = task_obligation_violations(projected, answer)
        self.assertFalse(
            any("goal-recommendation:性价比优先" in row for row in violations)
        )
        self.assertFalse(
            any("classification:calculated" in row for row in violations)
        )
        self.assertFalse(
            any("derived-calculation-result" in row for row in violations)
        )

    def test_control_plane_numbers_are_not_authoritative_business_quantities(self) -> None:
        projected, _ = project_business_task(RUN407_TASK)
        quantities = normalized_quantities(projected)
        self.assertNotIn(("3", "", "people"), quantities)
        self.assertNotIn(("4", "", "people"), quantities)
        self.assertIn(("2026", "", "year"), quantities)

    def test_plain_task_without_transport_marker_is_unchanged(self) -> None:
        task = "比较A与B，并给出建议。"
        projected, audit = project_business_task(task)
        self.assertEqual(task, projected)
        self.assertFalse(audit["projection_applied"])

    def test_repeated_same_deterministic_obligation_across_three_models_opens_signal(self) -> None:
        reason = "missing-task-obligation:decision-table"
        attempts = [
            attempt(1, "vendor/a", [reason]),
            attempt(2, "vendor/b", [reason]),
            attempt(3, "vendor/c", [reason]),
        ]
        signal = repeated_deterministic_quality_signal(attempts)
        self.assertIsNotNone(signal)
        self.assertEqual(reason, signal["reason"])
        self.assertEqual(3, signal["distinct_model_count"])
        self.assertEqual(3, signal["dynamic_evidence_threshold"])

    def test_two_models_do_not_open_systemic_signal(self) -> None:
        reason = "missing-task-obligation:decision-table"
        attempts = [
            attempt(1, "vendor/a", [reason]),
            attempt(2, "vendor/b", [reason]),
        ]
        self.assertIsNone(repeated_deterministic_quality_signal(attempts))

    def test_non_contract_quality_reason_does_not_open_systemic_signal(self) -> None:
        attempts = [
            attempt(1, "vendor/a", ["arithmetic-inconsistency:line-1"]),
            attempt(2, "vendor/b", ["arithmetic-inconsistency:line-1"]),
            attempt(3, "vendor/c", ["arithmetic-inconsistency:line-1"]),
        ]
        self.assertIsNone(repeated_deterministic_quality_signal(attempts))


if __name__ == "__main__":
    unittest.main()
