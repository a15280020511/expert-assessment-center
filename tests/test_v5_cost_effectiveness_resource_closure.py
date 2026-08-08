from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from execution_graph import SelectedNode  # noqa: E402
from v5_cost_effectiveness_planning import (  # noqa: E402
    build_runtime_planning_context,
)
from v5_cost_effectiveness_request_policy import (  # noqa: E402
    CostEffectiveFinalPayloadPromptPolicy,
)
from v5_cost_effectiveness_runtime import (  # noqa: E402
    CostEffectiveContinuousExecutionEngine,
)
from v5_recovery_runtime import build_production_runtime  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402
from v5_runtime_request_binding import (  # noqa: E402
    bind_request_knobs,
    estimate_payload_tokens,
    estimate_text_tokens,
)
from v5_runtime_timeout import dynamic_model_timeout_seconds  # noqa: E402


RESOURCE_SURFACES = {
    "prompt-shape-budgeting",
    "resource-efficiency-balance",
    "output-transport-allowance",
    "model-timeout-effective",
}


def candidate(index: int) -> dict[str, object]:
    return {
        "model": f"vendor-{index % 5}/reasoner-{index}",
        "company": f"vendor-{index % 5}",
        "popularity_rank": index + 1,
        "official_intelligence_rank": 30 - index,
        "prompt_usd_per_million": 0.05 + index * 0.01,
        "completion_usd_per_million": 0.20 + index * 0.02,
        "request_usd": 0.0,
        "context_length": 131_072,
        "max_completion_tokens": 32_768,
    }


def packet(question: str, requirements: list[str] | None = None) -> dict:
    task: dict[str, object] = {
        "question": question,
        "language": "zh-CN",
    }
    if requirements:
        task["requirements"] = requirements
    return {
        "task_id": "resource-closure-fixture",
        "task": task,
        "evidence": [],
        "execution_acceptance": requirements or [],
        "governance_model_plan": {
            "candidate_pool_authority": "decision-system-governance",
            "expert_candidate_pool": [candidate(i) for i in range(20)],
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "tool_use_forbidden": True,
            "tools_allowed": False,
        },
    }


def node(*, effort: str = "medium", ids: dict | None = None) -> SelectedNode:
    return SelectedNode(
        node_id="n1",
        assigned_work=("w1",),
        professional_capabilities={},
        functions=("analysis",),
        prompt_profile={},
        reasoning_profile={"reasoning_enabled": True, "effort": effort},
        parameter_profile={
            "runtime_resource_parameter_ids": ids
            or {
                "prompt-shape-budgeting": "p-prompt",
                "resource-efficiency-balance": "p-efficiency",
                "output-transport-allowance": "p-output",
                "model-timeout-effective": "p-timeout",
            },
            "runtime_resource_parameter_values": {
                "output-transport-allowance": {
                    "pre_request_visible_target_tokens": 256,
                }
            },
        },
        model="vendor/model",
        provider_endpoint="vendor/model@openrouter-auto",
        output_contract={
            "required_fields": ["结论", "关键依据"],
            "final_delivery_node": True,
        },
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.1,
        request_config={},
    )


class CostEffectivenessResourceClosureTests(unittest.TestCase):
    def test_request_resource_controls_are_first_class_parameter_specs(self) -> None:
        planning = build_runtime_planning_context(
            packet(
                "比较A/B/C三种方案并给出成本、可靠性与综合建议。",
                ["给出Markdown表", "给出临界值", "做敏感性分析"],
            ),
            [candidate(i) for i in range(20)],
        )
        requirements = planning["parameter_requirements"]
        by_surface = requirements["control_surface_to_parameter_id"]
        self.assertTrue(RESOURCE_SURFACES.issubset(set(by_surface)))
        self.assertTrue(requirements["all_request_resource_controls_first_class_parameters"])
        self.assertTrue(planning["cost_effectiveness_priority"])
        self.assertTrue(planning["soft_token_and_cost_efficiency"])
        resolved = planning["resolved_parameters"]
        self.assertEqual("PASS", resolved["parameter_coverage_audit"]["status"])
        for surface in RESOURCE_SURFACES:
            parameter_id = by_surface[surface]
            self.assertIn(parameter_id, resolved["parameter_values"])
            self.assertTrue(resolved["parameter_values"][parameter_id]["dynamic"])
            self.assertTrue(resolved["parameter_values"][parameter_id]["consumed_by"])

    def test_single_role_reasoning_uses_current_task_pressure_not_constant_medium(self) -> None:
        low_planning = {
            "role_plan": [
                {
                    "role_id": "r1",
                    "role_structural_demand": 1.0,
                    "reasoning_effort": "medium",
                }
            ],
            "resolved_profile": {
                "pressure": {
                    "input": 0.02,
                    "constraint": 0.02,
                    "evidence": 0.02,
                    "delivery": 0.02,
                    "overall": 0.02,
                }
            },
            "resolved_parameters": {
                "control_surface_values": {
                    "role-reasoning-effort": {},
                },
                "parameter_values": {},
            },
            "parameter_requirements": {
                "control_surface_to_parameter_id": {},
            },
        }
        high_planning = {
            **low_planning,
            "role_plan": [dict(low_planning["role_plan"][0])],
            "resolved_profile": {
                "pressure": {
                    "input": 0.95,
                    "constraint": 0.95,
                    "evidence": 0.95,
                    "delivery": 0.95,
                    "overall": 0.95,
                }
            },
            "resolved_parameters": {
                "control_surface_values": {
                    "role-reasoning-effort": {},
                },
                "parameter_values": {},
            },
        }
        import v5_cost_effectiveness_planning as planning_module

        low = planning_module._adjust_roles(low_planning)  # noqa: SLF001
        high = planning_module._adjust_roles(high_planning)  # noqa: SLF001
        self.assertEqual("low", low["role_plan"][0]["reasoning_effort"])
        self.assertEqual("high", high["role_plan"][0]["reasoning_effort"])
        self.assertNotEqual(
            low["role_plan"][0]["reasoning_effort"],
            high["role_plan"][0]["reasoning_effort"],
        )
        self.assertTrue(low["single_role_reasoning_effort_task_derived"])

    def test_missing_task_pressure_cannot_fall_back_to_medium(self) -> None:
        import v5_cost_effectiveness_planning as planning_module

        invalid = {
            "role_plan": [
                {
                    "role_id": "r1",
                    "role_structural_demand": 1.0,
                    "reasoning_effort": "medium",
                }
            ],
            "resolved_profile": {"pressure": {}},
            "resolved_parameters": {
                "control_surface_values": {"role-reasoning-effort": {}},
                "parameter_values": {},
            },
            "parameter_requirements": {"control_surface_to_parameter_id": {}},
        }
        with self.assertRaisesRegex(RuntimeError, "hidden medium fallback"):
            planning_module._adjust_roles(invalid)  # noqa: SLF001

    def test_governed_proposal_cannot_invent_medium_reasoning_effort(self) -> None:
        import v5_governed_plan_orchestrator as orchestrator

        with self.assertRaisesRegex(
            orchestrator.GovernedPlanOrchestrationError,
            "task-derived reasoning_effort",
        ):
            orchestrator._reasoning_effort({}, "r1")  # noqa: SLF001

    def test_request_binding_cannot_invent_medium_reasoning_effort(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "medium fallback is forbidden"):
            bind_request_knobs(node(effort=""), "完成当前任务", [])

    def test_cjk_estimator_does_not_divide_chinese_prompt_by_four(self) -> None:
        text = "网络保障方案需要完整比较并给出结论" * 20
        estimate = estimate_text_tokens(text)
        self.assertGreater(estimate, len(text) // 2)

    def test_final_assembled_payload_can_expand_initial_allowance(self) -> None:
        current_node = node(effort="medium")
        task = (
            "比较A/B/C/D/E。1）计算全部两两交点；2）八个情景逐项比较；"
            "3）做敏感性分析；4）给Markdown表。"
        )
        early, _ = bind_request_knobs(current_node, task, [])
        final_payload = {
            "model": current_node.model,
            "messages": [
                {"role": "system", "content": "固定宪法和输出纪律" * 500},
                {"role": "user", "content": task},
            ],
        }
        final, audit = bind_request_knobs(
            current_node,
            task,
            [],
            current_payload=final_payload,
        )
        self.assertGreater(final["max_tokens"], early["max_tokens"])
        self.assertTrue(audit["final_payload_measured"])
        self.assertGreater(audit["final_payload_token_estimate"], 0)
        self.assertEqual(
            "p-output",
            audit["parameter_runtime_binding"]["output_allowance_parameter_id"],
        )
        self.assertFalse(audit["token_estimate_is_exact"])

    def test_timeout_uses_final_payload_and_first_class_parameter_id(self) -> None:
        current_node = node(effort="high")
        payload_value = {
            "model": current_node.model,
            "messages": [
                {"role": "system", "content": "任务约束" * 500},
                {"role": "user", "content": "完成复杂比较" * 200},
            ],
            "reasoning": {"effort": "high"},
            "max_tokens": 6000,
        }
        timeout, audit = dynamic_model_timeout_seconds(
            current_node,
            payload_value,
            240,
        )
        self.assertGreaterEqual(timeout, 30)
        self.assertLessEqual(timeout, 240)
        self.assertEqual("p-timeout", audit["timeout_parameter_id"])
        self.assertEqual(
            "final-current-payload-before-send",
            audit["prompt_source"],
        )
        self.assertEqual(
            estimate_payload_tokens(payload_value),
            audit["prompt_token_estimate"],
        )
        self.assertEqual(
            "infrastructure_invariant",
            audit["safety_cap_classification"],
        )

    def test_production_runtime_installs_cost_effective_engine_and_prompt_policy(self) -> None:
        runtime = build_production_runtime(
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=2,
                cost_anomaly_usd=None,
                tools_allowed=False,
                live_catalog_required=False,
                provider_lock_required=False,
            )
        )
        self.assertIsInstance(
            runtime.execution_engine,
            CostEffectiveContinuousExecutionEngine,
        )
        self.assertIsInstance(
            runtime.prompt_policy,
            CostEffectiveFinalPayloadPromptPolicy,
        )
        self.assertIs(runtime.execution_engine.prompt_policy, runtime.prompt_policy)


if __name__ == "__main__":
    unittest.main()
