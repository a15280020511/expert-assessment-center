from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_compound_fact_provenance import compound_fact_claim_supported  # noqa: E402
from v5_final_semantic_gate import (  # noqa: E402
    arithmetic_consistency_violations,
    scenario_direction_violations,
)
from v5_output_contract_delivery import contract_aware_system_prompt  # noqa: E402
from v5_priority_preserving_heterogeneity import (  # noqa: E402
    first_equivalent_quality_heterogeneous_standby,
)
from v5_runtime_request_binding import bind_request_knobs  # noqa: E402


class LiveE2ERecoveryEfficiencyTests(unittest.TestCase):
    @staticmethod
    def _node(*, final: bool = True):
        return SimpleNamespace(
            node_id="node-1",
            model="vendor/model",
            provider_endpoint="vendor/model@openrouter-auto",
            assigned_work=("analysis-1",),
            functions=("analyze:task-analysis",),
            reasoning_profile={"effort": "medium"},
            parameter_profile={},
            prompt_profile={"modules": [], "role": "动态任务角色"},
            output_contract={
                "required_fields": ["核心判断", "关键依据", "不确定性与反例", "可执行结论"],
                "final_delivery_node": final,
                "machine_readable_required": False,
            },
        )

    def test_final_generic_node_cannot_delegate_to_downstream(self) -> None:
        prompt = contract_aware_system_prompt(self._node(final=True))
        self.assertIn("最终交付节点", prompt)
        self.assertIn("不存在负责补交的节点", prompt)
        self.assertNotIn("本节点是内部工作节点", prompt)

    def test_seven_explicit_deliverables_expand_visible_allowance(self) -> None:
        node = self._node(final=True)
        short_task = "1）计算结果。"
        long_task = "1）计算。2）推荐。3）阈值。4）50/100/150%。5）高低估。6）反例。7）决策表。"
        short_config, short_audit = bind_request_knobs(node, short_task, [])
        long_config, long_audit = bind_request_knobs(node, long_task, [])
        self.assertEqual(7, long_audit["explicit_delivery_unit_count"])
        self.assertGreater(long_config["max_tokens"], short_config["max_tokens"])
        self.assertGreater(long_audit["visible_output_requirement_tokens"], short_audit["visible_output_requirement_tokens"])

    def test_run396_chained_constant_arithmetic_is_not_false_positive(self) -> None:
        answer = (
            "A：4000 + 3×500 + 3×1.0×2000 = 4000+1500+6000 = 11500\n"
            "B：6500 + 3×700 + 3×0.5×2000 = 6500+2100+3000 = 11600\n"
            "C：10000 + 3×900 + 3×0.2×2000 = 10000+2700+1200 = 13900"
        )
        self.assertEqual([], arithmetic_consistency_violations(answer))

    def test_real_constant_error_still_fails(self) -> None:
        violations = arithmetic_consistency_violations("2+2=5")
        self.assertTrue(any(value.startswith("arithmetic-inconsistency:") for value in violations), violations)

    def test_threshold_numerator_chain_uses_final_approximation(self) -> None:
        answer = (
            "A↔B：5500+3L = 8600+1.5L → 1.5L=3100 → "
            "L=6200/3≈2066.67"
        )
        self.assertEqual([], arithmetic_consistency_violations(answer))

    def test_high_low_direction_is_clause_local_and_keeps_130_percent(self) -> None:
        answer = (
            "高估30%（实际=题面×70%）：推荐A；"
            "低估30%（实际=题面×130%）：推荐B。"
        )
        self.assertEqual([], scenario_direction_violations(answer))
        reversed_answer = (
            "高估30%（实际=题面×130%）：推荐B；"
            "低估30%（实际=题面×70%）：推荐A。"
        )
        self.assertEqual(2, len(scenario_direction_violations(reversed_answer)))

    def test_compound_fact_requires_every_local_clause_to_be_task_supported(self) -> None:
        task = (
            "题面事实：方案A初始投入4000元、每年维护500元、每年故障1.0次；"
            "B初始6500元、维护700元、故障0.5次；"
            "C初始10000元、维护900元、故障0.2次。"
        )
        claim = (
            "方案A初始投入4000元、每年维护500元、每年故障1.0次；"
            "B初始6500元、维护700元、故障0.5次；"
            "C初始10000元、维护900元、故障0.2次。"
        )
        self.assertTrue(compound_fact_claim_supported(task, claim))
        tampered = claim.replace("B初始6500元", "B初始9000元")
        self.assertFalse(compound_fact_claim_supported(task, tampered))

    def test_company_preference_never_crosses_quality_risk_bucket(self) -> None:
        inventory = [
            {"model": "anthropic/a", "estimated_quality": 0.9, "failure_probability": 0.1},
            {"model": "anthropic/b", "estimated_quality": 0.9, "failure_probability": 0.1},
            {"model": "deepseek/c", "estimated_quality": 0.9, "failure_probability": 0.1},
            {"model": "moonshot/d", "estimated_quality": 0.8, "failure_probability": 0.1},
        ]
        chosen = first_equivalent_quality_heterogeneous_standby(
            inventory,
            claimed=set(),
            hard_failed=set(),
            tried_companies={"anthropic"},
        )
        self.assertEqual("deepseek/c", chosen["model"])
        chosen = first_equivalent_quality_heterogeneous_standby(
            inventory,
            claimed=set(),
            hard_failed=set(),
            tried_companies={"anthropic", "deepseek"},
        )
        self.assertEqual("anthropic/a", chosen["model"])


if __name__ == "__main__":
    unittest.main()
