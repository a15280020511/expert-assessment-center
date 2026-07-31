from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from execution_graph import ExecutionGraph, GraphLimits, SelectedNode  # noqa: E402
from publish_report import resolve_paths  # noqa: E402
from v5_recovery_runtime import (  # noqa: E402
    CrossEndpointPlannerPolicy,
    build_production_runtime,
)
from v5_runtime import FailureCategory, RuntimeConfig  # noqa: E402


def selected_node(
    *,
    node_id: str = "node-luna",
    model: str = "openai/gpt-5.6-luna",
    provider: str = "openai/flex",
    estimated_cost: float = 0.003,
) -> SelectedNode:
    return SelectedNode(
        node_id=node_id,
        assigned_work=("work-decision",),
        professional_capabilities={"analysis": 0.7, "delivery": 0.7},
        functions=("analysis", "decision_comparison"),
        prompt_profile={"modules": ["decision_comparison", "structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
        parameter_profile={
            "supported_parameters": ["max_tokens", "reasoning"],
            "recommended_output_allowance_tokens": 3000,
        },
        model=model,
        provider_endpoint=f"{model}@{provider}",
        output_contract={
            "required_fields": [
                "assumptions",
                "conclusions",
                "criteria",
                "evidence_gaps",
                "options",
                "ranking",
                "tradeoffs",
                "uncertainties",
            ],
            "machine_readable_required": False,
            "must_separate_fact_assumption_inference": True,
        },
        estimated_quality=0.7,
        quality_uncertainty=0.1,
        estimated_cost=estimated_cost,
        failure_probability=0.02,
        request_config={
            "provider": {
                "only": [provider],
                "order": [provider],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "reasoning": {"effort": "medium", "exclude": True},
        },
    )


def candidate(
    candidate_id: str,
    model: str,
    provider: str,
    cost: float,
    quality: float,
    failure: float,
) -> dict:
    node = selected_node(
        node_id=candidate_id,
        model=model,
        provider=provider,
        estimated_cost=cost,
    )
    value = {
        "candidate_id": candidate_id,
        "interpretation_id": "interpretation-real",
        "coverage_keys": ["work-decision#0"],
        "assigned_work": ["work-decision"],
        "copy_indices": [0],
        "professional_capabilities": dict(node.professional_capabilities),
        "functions": list(node.functions),
        "prompt_profile": dict(node.prompt_profile),
        "reasoning_profile": dict(node.reasoning_profile),
        "parameter_profile": dict(node.parameter_profile),
        "model": model,
        "provider_endpoint": node.provider_endpoint,
        "provider_slug": provider,
        "output_contract": dict(node.output_contract),
        "estimated_quality": quality,
        "quality_uncertainty": 0.1,
        "estimated_cost": cost,
        "failure_probability": failure,
        "request_config": dict(node.request_config),
        "independence_groups": [],
    }
    return value


VALID_ANSWER = """## assumptions
事实：岗亭没有有线网络；移动网络可用。假设：两方案使用可比较的流量条件。

## conclusions
推断：应先做七天可撤销试用，再按稳定性、成本和维护负担选择。

## criteria
记录断流次数、恢复耗时、发热、电量消耗、连接步骤和月成本。

## evidence_gaps
不确定性：未知运营商、设备价格、套餐限速与岗亭具体信号。

## options
方案A为手机热点；方案B为随身Wi-Fi。两者均不得预设具体价格。

## ranking
若热点稳定且不影响电话与续航，A优先；若频繁发热断流，B优先。

## tradeoffs
A一次性投入低但占用手机；B设备独立但增加购买、充电和维护负担。

## uncertainties
事实、假设、推断和未知信息已分开；最终选择以现场记录为准。
"""


class V5CrossEndpointRecoveryTests(unittest.TestCase):
    def config(self) -> RuntimeConfig:
        return RuntimeConfig(
            total_call_limit=2,
            recovery_call_limit=1,
            cost_anomaly_usd=0.25,
            quality_tier="value",
            tools_allowed=False,
            provider_lock_required=True,
        )

    def test_empty_response_is_not_retried_on_same_endpoint(self) -> None:
        runtime = build_production_runtime(self.config())
        self.assertNotIn(
            FailureCategory.PROVIDER_EMPTY_RESPONSE,
            runtime.retry_policy.retry_same_endpoint_categories,
        )
        self.assertIn(
            FailureCategory.PROVIDER_EMPTY_RESPONSE,
            runtime.recovery_policy.replace_categories,
        )

    def test_recovery_pool_prefers_low_cost_different_providers(self) -> None:
        config = self.config()
        policy = CrossEndpointPlannerPolicy(config)
        selected = candidate(
            "node-luna",
            "openai/gpt-5.6-luna",
            "openai/flex",
            0.003,
            0.64,
            0.023,
        )
        optimization = {
            "execution_graph": {
                "nodes": [
                    {
                        **selected,
                        "node_id": selected["candidate_id"],
                    }
                ],
                "metadata": {"interpretation_id": "interpretation-real"},
            }
        }
        bundle = {
            "candidates": [
                selected,
                candidate("node-opus", "anthropic/claude-opus-5", "anthropic", 0.2135, 0.85, 0.016),
                candidate("node-glm", "z-ai/glm-5.2", "decart/fp4", 0.0111, 0.63, 0.0235),
                candidate("node-terra", "openai/gpt-5.6-terra", "openai/flex", 0.0255, 0.75, 0.02),
                candidate("node-gemini", "google/gemini-3.5-flash", "google-vertex/global/flex", 0.0384, 0.61, 0.024),
            ]
        }
        result = policy.rebalance_recovery_pool(optimization, bundle)
        rows = result["execution_graph"]["metadata"]["recovery_pool"]["node-luna"]
        self.assertEqual("z-ai/glm-5.2", rows[0]["model"])
        self.assertEqual("google/gemini-3.5-flash", rows[1]["model"])
        self.assertNotEqual("openai/flex", rows[0]["provider_slug"])
        self.assertLess(rows[0]["estimated_cost"], 0.02)
        policy_evidence = result["execution_graph"]["metadata"]["recovery_pool_policy"]
        self.assertTrue(policy_evidence["provider_diversity_first"])
        self.assertFalse(policy_evidence["same_endpoint_empty_response_retry"])
        self.assertFalse(policy_evidence["cross_task_history_used"])

    def test_execution_preserves_frozen_effective_value_recovery_order(self) -> None:
        runtime = build_production_runtime(RuntimeConfig(
            total_call_limit=3,
            recovery_call_limit=2,
            cost_anomaly_usd=0.25,
            quality_tier="value",
            tools_allowed=False,
            provider_lock_required=True,
        ))
        selected = selected_node()
        low_value_cost = candidate(
            "node-value",
            "z-ai/glm-5.2",
            "decart/fp4",
            0.02,
            0.68,
            0.05,
        )
        expensive_low_failure = candidate(
            "node-expensive",
            "anthropic/claude-opus-5",
            "anthropic",
            0.15,
            0.90,
            0.01,
        )
        graph = ExecutionGraph(
            nodes=(selected,),
            edges=(),
            execution_stages=((selected.node_id,),),
            entry_nodes=(selected.node_id,),
            final_nodes=(selected.node_id,),
            required_work=("work-decision",),
            estimated_quality=0.7,
            quality_floor=0.6,
            estimated_total_cost=0.003,
            metadata={
                "interpretation_id": "interpretation-real",
                "recovery_pool": {
                    selected.node_id: [low_value_cost, expensive_low_failure]
                },
            },
        )
        observed_models: list[str] = []

        def call_fn(_run, payload):
            model = str(payload["model"])
            observed_models.append(model)
            if model == selected.model:
                return {
                    "id": "initial-empty",
                    "model": model,
                    "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                    "usage": {},
                }, 0.01
            return {
                "id": "replacement-success",
                "model": model,
                "provider": payload["provider"]["only"][0],
                "choices": [{"message": {"content": VALID_ANSWER}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 700, "cost": 0.01},
            }, 0.01

        with tempfile.TemporaryDirectory() as directory:
            result = runtime.execute_graph(
                graph,
                SimpleNamespace(api_key="fixture", model_timeout_seconds=30, parallel_workers=1),
                "比较方案。",
                call_fn=call_fn,
                output_dir=directory,
                limits=GraphLimits(
                    max_nodes=1,
                    max_model_calls=3,
                    max_retries=0,
                    max_replacements=2,
                    max_budget_usd=0.25,
                    cost_risk_multiplier=1.18,
                ),
            )
        self.assertEqual(
            [selected.model, "z-ai/glm-5.2"],
            observed_models,
        )
        self.assertEqual("z-ai/glm-5.2", result["node_results"][0]["resolved_model"])

    def test_real_empty_response_fixture_recovers_once_across_endpoints(self) -> None:
        runtime = build_production_runtime(self.config())
        selected = selected_node()
        replacement = candidate(
            "node-glm",
            "z-ai/glm-5.2",
            "decart/fp4",
            0.0111,
            0.63,
            0.0235,
        )
        graph = ExecutionGraph(
            nodes=(selected,),
            edges=(),
            execution_stages=((selected.node_id,),),
            entry_nodes=(selected.node_id,),
            final_nodes=(selected.node_id,),
            required_work=("work-decision",),
            estimated_quality=0.7,
            quality_floor=0.6,
            estimated_total_cost=0.003,
            metadata={
                "interpretation_id": "interpretation-real",
                "recovery_pool": {selected.node_id: [replacement]},
            },
        )
        observed_models: list[str] = []

        def call_fn(_run, payload):
            model = str(payload["model"])
            observed_models.append(model)
            if model == "openai/gpt-5.6-luna":
                return {
                    "id": "empty-real-fixture",
                    "model": model,
                    "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                    "usage": {},
                }, 0.01
            return {
                "id": "replacement-success",
                "model": model,
                "provider": "decart/fp4",
                "choices": [{"message": {"content": VALID_ANSWER}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 700,
                    "completion_tokens": 900,
                    "cost": 0.009,
                },
            }, 0.02

        run = SimpleNamespace(
            api_key="fixture",
            model_timeout_seconds=30,
            parallel_workers=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            result = runtime.execute_graph(
                graph,
                run,
                "比较手机热点与随身Wi-Fi并给出七天试用计划。",
                call_fn=call_fn,
                output_dir=directory,
                limits=GraphLimits(
                    max_nodes=1,
                    max_model_calls=2,
                    max_retries=1,
                    max_replacements=1,
                    max_budget_usd=0.25,
                    cost_risk_multiplier=1.18,
                ),
            )
        self.assertEqual("success", result["status"])
        node_result = result["node_results"][0]
        self.assertEqual("success_recovered", node_result["status"])
        self.assertEqual("z-ai/glm-5.2", node_result["resolved_model"])
        self.assertEqual(["openai/gpt-5.6-luna", "z-ai/glm-5.2"], observed_models)
        self.assertEqual(
            ["initial", "replacement"],
            [row["attempt_kind"] for row in node_result["attempts"]],
        )
        budget = result["execution_budget"]
        self.assertEqual(2, budget["calls_reserved"])
        self.assertEqual(1, budget["recovery_calls_reserved"])
        self.assertEqual(0, budget["retries_reserved"])
        self.assertEqual(1, budget["replacements_reserved"])
        self.assertAlmostEqual(0.009, result["actual_cost_usd"])

    def test_report_publisher_accepts_production_workflow_arguments(self) -> None:
        args = SimpleNamespace(
            report=None,
            output_dir="ticket-artifacts",
            comments_dir="ticket-artifacts/report-comments",
        )
        report, comments = resolve_paths(args)
        self.assertEqual(Path("ticket-artifacts/v5-final-report.md"), report)
        self.assertEqual(Path("ticket-artifacts/report-comments"), comments)


if __name__ == "__main__":
    unittest.main()
