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


class V5RecoveryVisibilityAndSpatialExistentialTests(unittest.TestCase):
    def test_spatial_existential_surface_variant_is_supported(self) -> None:
        task = (
            "仅依据题面，不得编造。东门内侧地面有少量清水，"
            "旁边放着一个未接电且外壳破裂的充电器。"
        )
        claim = (
            "东门内侧地面有少量清水，旁边有未接电且外壳破裂的充电器。"
        )
        self.assertTrue(fact_claim_supported(task, claim))
        self.assertEqual(
            validate_answer_evidence(
                task,
                f"**事实｜来源：题面**：{claim}",
            ),
            [],
        )

    def test_spatial_or_object_change_remains_rejected(self) -> None:
        task = (
            "仅依据题面，不得编造。东门内侧地面有少量清水，"
            "旁边放着一个未接电且外壳破裂的充电器。"
        )
        changed_location = (
            "西门内侧地面有少量清水，旁边有未接电且外壳破裂的充电器。"
        )
        changed_object = (
            "东门内侧地面有少量清水，旁边有未接电且外壳破裂的风扇。"
        )
        self.assertFalse(fact_claim_supported(task, changed_location))
        self.assertFalse(fact_claim_supported(task, changed_object))

    def test_unanchored_role_count_supports_generic_onsite_paraphrase(self) -> None:
        task = (
            "仅依据题面，不得编造。某夜间档案转运点只有3名值守人员。"
        )
        claim = "3名值守人员在现场。"
        self.assertTrue(fact_claim_supported(task, claim))
        self.assertEqual(
            validate_answer_evidence(
                task,
                f"**事实｜来源：题面**：{claim}",
            ),
            [],
        )

    def test_generic_onsite_cannot_erase_explicit_location(self) -> None:
        task = (
            "仅依据题面，不得编造。南门外有2名设备维护人员等待核验。"
        )
        claim = "2名设备维护人员在现场。"
        self.assertFalse(fact_claim_supported(task, claim))
        self.assertTrue(
            validate_answer_evidence(
                task,
                f"**事实｜来源：题面**：{claim}",
            )
        )

    def test_sensory_existence_and_shared_location_compression_is_supported(self) -> None:
        task = (
            "仅依据题面，不得编造。南门外闻到来源不明的焦糊味，"
            "无法确认来源，也无法确认是否存在电气风险。"
            "南门外有2名身份无法核验、自称设备维护人员的人要求进入。"
        )
        claim = (
            "南门外有焦糊味，且有2名身份无法核验的自称设备维护人员要求进入。"
        )
        self.assertTrue(fact_claim_supported(task, claim))
        self.assertEqual(
            validate_answer_evidence(
                task,
                f"**事实｜来源：题面**：{claim}",
            ),
            [],
        )

    def test_sensory_paraphrase_preserves_named_location_and_object(self) -> None:
        task = (
            "仅依据题面，不得编造。南门外闻到来源不明的焦糊味。"
        )
        self.assertFalse(fact_claim_supported(task, "北门外有焦糊味。"))
        self.assertFalse(fact_claim_supported(task, "南门外有汽油味。"))

    def test_recovery_ranks_visible_delivery_control_before_cheaper_risk(self) -> None:
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
            model: str,
            provider: str,
            cost: float,
            supported: list[str],
        ) -> dict:
            return {
                "candidate_id": candidate_id,
                "model": model,
                "provider_slug": provider,
                "provider_endpoint": f"{model}@{provider}",
                "estimated_cost": cost,
                "estimated_quality": 0.57,
                "quality_uncertainty": 0.10,
                "failure_probability": 0.06,
                "parameter_profile": {
                    "parameters": {
                        "reasoning": {"effort": "high", "exclude": True}
                    },
                    "supported_parameters": supported,
                    "reasoning_token_ceiling_sent": False,
                    "dynamic_parameter_decisions": {
                        "reasoning_effort": "high"
                    },
                    "operational_serviceability": {
                        "expected_visible_output_tokens": 4159,
                        "estimated_deadline_ratio": 0.35,
                    },
                },
            }

        cheaper_uncontrolled = row(
            candidate_id="node-gemma",
            model="google/gemma-4-26b-a4b-it",
            provider="nextbit/bf16",
            cost=0.0073,
            supported=["reasoning", "include_reasoning", "max_tokens"],
        )
        controlled = row(
            candidate_id="node-mercury",
            model="inception/mercury-2",
            provider="inception",
            cost=0.0122,
            supported=[
                "reasoning",
                "reasoning_effort",
                "include_reasoning",
                "max_tokens",
            ],
        )
        self.assertEqual(
            policy._reasoning_visibility_risk(cheaper_uncontrolled),
            1,
        )
        self.assertEqual(policy._reasoning_visibility_risk(controlled), 0)
        controlled_key = policy._recovery_sort_key(
            controlled,
            "deepinfra/bf16",
            critical_delivery=False,
        )
        uncontrolled_key = policy._recovery_sort_key(
            cheaper_uncontrolled,
            "deepinfra/bf16",
            critical_delivery=False,
        )
        self.assertLess(controlled_key, uncontrolled_key)

    def test_medium_or_visible_reasoning_is_not_penalized(self) -> None:
        policy = CrossEndpointPlannerPolicy(
            RuntimeConfig(
                total_call_limit=2,
                recovery_call_limit=1,
                cost_anomaly_usd=0.25,
                quality_tier="value",
                tools_allowed=False,
                provider_lock_required=True,
            )
        )
        row = {
            "parameter_profile": {
                "parameters": {
                    "reasoning": {"effort": "medium", "exclude": True}
                },
                "supported_parameters": ["reasoning", "max_tokens"],
                "reasoning_token_ceiling_sent": False,
                "operational_serviceability": {
                    "expected_visible_output_tokens": 2000
                },
            }
        }
        self.assertEqual(policy._reasoning_visibility_risk(row), 0)
        row["parameter_profile"]["parameters"]["reasoning"] = {
            "effort": "high",
            "exclude": False,
        }
        self.assertEqual(policy._reasoning_visibility_risk(row), 0)


if __name__ == "__main__":
    unittest.main()
