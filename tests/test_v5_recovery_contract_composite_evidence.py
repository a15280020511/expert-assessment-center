from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_cross_endpoint_planner import CrossEndpointPlannerPolicy  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402
from v5_task_constraints import (  # noqa: E402
    fact_claim_supported,
    validate_answer_evidence,
)
from v5_task_delivery_contract import project_task_for_node  # noqa: E402


class V5RecoveryContractCompositeEvidenceTests(unittest.TestCase):
    def test_internal_projection_removes_dangling_final_format_lead(self) -> None:
        task = (
            "请制定立即处置方案。最终输出必须且只能使用以下6个Markdown二级标题，"
            "并严格按此顺序，每节非空：事实边界；隔离警戒；人员分工；"
            "门禁核验；耗材复核；移交条件。不得出现其他二级标题。"
        )
        projected = project_task_for_node(
            task,
            {
                "required_fields": ["assumptions", "conclusions"],
                "machine_readable_required": False,
            },
        )
        self.assertIn("请制定立即处置方案", projected)
        self.assertNotIn("最终输出必须且只能", projected)
        self.assertNotIn("事实边界", projected)
        self.assertIn("内部节点任务投影", projected)

    def test_recovery_contract_serviceability_precedes_lower_cost(self) -> None:
        policy = CrossEndpointPlannerPolicy(
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=1,
                cost_anomaly_usd=0.35,
                quality_tier="value",
                tools_allowed=False,
                provider_lock_required=True,
            )
        )

        def row(
            *,
            candidate_id: str,
            cost: float,
            supported: list[str],
            delivery: float,
        ) -> dict:
            return {
                "candidate_id": candidate_id,
                "model": f"vendor/{candidate_id}",
                "provider_slug": "provider-a",
                "provider_endpoint": f"vendor/{candidate_id}@provider-a",
                "estimated_cost": cost,
                "estimated_quality": 0.60,
                "quality_uncertainty": 0.12,
                "failure_probability": 0.06,
                "professional_capabilities": {
                    "structured_output": 0.90,
                    "delivery": delivery,
                },
                "output_contract": {
                    "required_fields": [
                        "assumptions",
                        "conclusions",
                        "criteria",
                        "evidence_gaps",
                        "options",
                        "ranking",
                    ],
                    "machine_readable_required": False,
                },
                "parameter_profile": {
                    "parameters": {
                        "reasoning": {"effort": "medium", "exclude": True}
                    },
                    "supported_parameters": supported,
                    "dynamic_parameter_decisions": {
                        "reasoning_effort": "medium",
                        "structured_delivery": False,
                    },
                    "operational_serviceability": {
                        "expected_visible_output_tokens": 2000,
                        "estimated_deadline_ratio": 0.25,
                    },
                },
            }

        cheaper_weak = row(
            candidate_id="response-format-only",
            cost=0.004,
            supported=["response_format", "reasoning_effort"],
            delivery=0.52,
        )
        stronger_contract = row(
            candidate_id="structured-control",
            cost=0.010,
            supported=[
                "response_format",
                "structured_outputs",
                "reasoning_effort",
            ],
            delivery=0.52,
        )
        self.assertEqual(policy._contract_delivery_risk(cheaper_weak), 1)
        self.assertEqual(policy._contract_delivery_risk(stronger_contract), 0)
        self.assertLess(
            policy._recovery_sort_key(
                stronger_contract,
                "different-provider",
                critical_delivery=False,
            ),
            policy._recovery_sort_key(
                cheaper_weak,
                "different-provider",
                critical_delivery=False,
            ),
        )

    def test_production_composite_fact_summary_is_supported(self) -> None:
        task = (
            "仅依据题面，不得编造。西门外闻到来源不明的焦糊味，"
            "无法确认来源，也无法确认是否存在电气风险。"
            "西门外有2名身份无法核验、自称设备维护人员的人要求进入。"
            "纸质交接单显示8箱密封耗材已经交接，但现场只能确认7箱。"
        )
        claim = (
            "西门外焦糊味来源不明，来访者身份无法核验，"
            "交接单与现场实物数量存在差异。"
        )
        self.assertTrue(fact_claim_supported(task, claim))
        self.assertEqual(
            validate_answer_evidence(
                task,
                f"**事实｜来源：题面**：{claim}",
            ),
            [],
        )

    def test_composite_summary_rejects_location_or_object_mutation(self) -> None:
        task = (
            "仅依据题面，不得编造。西门外闻到来源不明的焦糊味，"
            "无法确认来源。"
        )
        self.assertFalse(
            fact_claim_supported(task, "东门外焦糊味来源不明。")
        )
        self.assertFalse(
            fact_claim_supported(task, "西门外汽油味来源不明。")
        )

    def test_identity_summary_rejects_wrong_subject(self) -> None:
        task = (
            "仅依据题面，不得编造。西门外有2名身份无法核验、"
            "自称设备维护人员的人要求进入。"
        )
        self.assertTrue(fact_claim_supported(task, "来访者身份无法核验。"))
        self.assertFalse(fact_claim_supported(task, "值守人员身份无法核验。"))

    def test_discrepancy_summary_requires_different_same_unit_quantities(self) -> None:
        unequal = (
            "纸质交接单显示8箱密封耗材已经交接，但现场只能确认7箱。"
        )
        equal = (
            "纸质交接单显示8箱密封耗材已经交接，现场也确认8箱。"
        )
        claim = "交接单与现场实物数量存在差异。"
        self.assertTrue(fact_claim_supported(unequal, claim))
        self.assertFalse(fact_claim_supported(equal, claim))


if __name__ == "__main__":
    unittest.main()
