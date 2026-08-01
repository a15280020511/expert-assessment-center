from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from execution_graph import ExecutionGraph  # noqa: E402
from v5_cross_endpoint_planner import CrossEndpointPlannerPolicy  # noqa: E402
from v5_runtime import BudgetController, RuntimeConfig  # noqa: E402


def candidate(
    candidate_id: str,
    model: str,
    provider: str,
    *,
    work_id: str,
    functions: tuple[str, ...],
    cost: float,
    quality: float,
    failure: float,
    uncertainty: float = 0.1,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "interpretation_id": "interpretation-critical",
        "coverage_keys": [f"{work_id}#0"],
        "assigned_work": [work_id],
        "copy_indices": [0],
        "professional_capabilities": {},
        "functions": list(functions),
        "prompt_profile": {},
        "reasoning_profile": {},
        "parameter_profile": {},
        "model": model,
        "provider_endpoint": f"{model}@{provider}",
        "provider_slug": provider,
        "output_contract": {},
        "estimated_quality": quality,
        "quality_uncertainty": uncertainty,
        "estimated_cost": cost,
        "failure_probability": failure,
        "request_config": {},
        "independence_groups": [],
    }


class V5CriticalDeliveryReliabilityTests(unittest.TestCase):
    def config(self) -> RuntimeConfig:
        return RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=0.03,
            quality_tier="value",
            tools_allowed=False,
            provider_lock_required=True,
        )

    def test_structural_filter_rejects_cheap_insufficient_synthesis(self) -> None:
        policy = CrossEndpointPlannerPolicy(self.config())
        upstream = ["work-a", "work-b", "work-c"]
        final = "work-final"
        candidates = [
            candidate(
                f"node-{work_id}",
                f"company-{work_id}/analysis",
                f"provider-{work_id}",
                work_id=work_id,
                functions=("analysis",),
                cost=0.001,
                quality=0.70,
                failure=0.02,
            )
            for work_id in upstream
        ]
        candidates.extend(
            [
                candidate(
                    "node-cheap-synthesis",
                    "cheap/synthesis",
                    "cheap-provider",
                    work_id=final,
                    functions=("synthesis",),
                    cost=0.001,
                    quality=0.45,
                    failure=0.03,
                ),
                candidate(
                    "node-strong-synthesis",
                    "strong/synthesis",
                    "strong-provider",
                    work_id=final,
                    functions=("synthesis",),
                    cost=0.003,
                    quality=0.80,
                    failure=0.02,
                ),
            ]
        )
        bundle = {
            "candidates": candidates,
            "interpretations": {
                "interpretation-critical": {
                    "work_ids": [*upstream, final],
                    "copies_by_work": {
                        work_id: 1 for work_id in [*upstream, final]
                    },
                    "atomic_edges": [
                        {
                            "source": work_id,
                            "target": final,
                            "relation_type": "dependency",
                        }
                        for work_id in upstream
                    ],
                }
            },
        }

        filtered, evidence = policy._filter_critical_delivery_candidates(
            bundle,
            max_budget_usd=0.03,
        )
        ids = {
            str(row["candidate_id"]) for row in filtered["candidates"]
        }
        self.assertNotIn("node-cheap-synthesis", ids)
        self.assertIn("node-strong-synthesis", ids)
        self.assertEqual(1, evidence["removed_candidate_count"])
        self.assertEqual(
            4,
            evidence["critical_work"][0]["structural_leverage"],
        )
        self.assertFalse(evidence["fallback_used"])

    def test_critical_recovery_retains_rows_for_live_budget_admission(self) -> None:
        policy = CrossEndpointPlannerPolicy(self.config())
        final = "work-final"
        selected = candidate(
            "node-selected",
            "openai/selected",
            "coreweave/fp4",
            work_id=final,
            functions=("synthesis",),
            cost=0.004,
            quality=0.60,
            failure=0.03,
        )
        cheap_alibaba = candidate(
            "node-qwen-small",
            "qwen/qwen-small",
            "venice/fp8",
            work_id=final,
            functions=("synthesis",),
            cost=0.0016,
            quality=0.56,
            failure=0.025,
        )
        reliable_alibaba = candidate(
            "node-qwen-plus",
            "qwen/qwen-plus",
            "alibaba/fp8",
            work_id=final,
            functions=("synthesis",),
            cost=0.018,
            quality=0.75,
            failure=0.02,
        )
        reliable_glm = candidate(
            "node-glm",
            "z-ai/glm",
            "decart/fp4",
            work_id=final,
            functions=("synthesis",),
            cost=0.017,
            quality=0.78,
            failure=0.019,
        )
        above_planning_advisory = candidate(
            "node-over-budget",
            "anthropic/opus",
            "anthropic",
            work_id=final,
            functions=("synthesis",),
            cost=0.04,
            quality=0.90,
            failure=0.01,
        )
        optimization = {
            "selected_initial_cost_usd": 0.005,
            "execution_graph": {
                "nodes": [
                    {
                        **selected,
                        "node_id": selected["candidate_id"],
                    }
                ],
                "final_nodes": [selected["candidate_id"]],
                "metadata": {
                    "interpretation_id": "interpretation-critical"
                },
            },
        }
        bundle = {
            "candidates": [
                selected,
                cheap_alibaba,
                reliable_alibaba,
                reliable_glm,
                above_planning_advisory,
            ]
        }

        result = policy.rebalance_recovery_pool(optimization, bundle)
        metadata = result["execution_graph"]["metadata"]
        rows = metadata["recovery_pool"]["node-selected"]
        models = [str(row["model"]) for row in rows]
        self.assertEqual("anthropic/opus", models[0])
        self.assertIn("z-ai/glm", models)
        self.assertIn("qwen/qwen-plus", models)
        self.assertNotIn("qwen/qwen-small", models)
        policy_evidence = metadata["recovery_pool_policy"]
        self.assertTrue(
            policy_evidence["planning_estimated_budget_advisory_only"]
        )
        self.assertTrue(
            policy_evidence["runtime_budget_controller_authoritative"]
        )
        self.assertTrue(
            policy_evidence[
                "recovery_candidates_retained_for_live_ledger_admission"
            ]
        )
        self.assertEqual(
            0,
            policy_evidence["budget_excluded_by_node"]["node-selected"],
        )
        self.assertEqual(
            1,
            policy_evidence[
                "estimated_above_planning_budget_by_node"
            ]["node-selected"],
        )
        retained = next(
            row for row in rows if row["model"] == "anthropic/opus"
        )
        self.assertTrue(
            retained["estimated_cost_above_planning_remaining_budget"]
        )


    def test_v3_regression_live_ledger_can_admit_retained_recovery(self) -> None:
        config = RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=0.008,
            quality_tier="value",
            tools_allowed=False,
            provider_lock_required=True,
        )
        policy = CrossEndpointPlannerPolicy(config)
        selected_rows = [
            candidate(
                "node-qwen",
                "qwen/qwen3.5-9b",
                "siliconflow/fp8",
                work_id="work-qwen",
                functions=("analysis",),
                cost=0.0017,
                quality=0.70,
                failure=0.03,
            ),
            candidate(
                "node-deepseek",
                "deepseek/deepseek-v4-flash",
                "deepinfra/fp4",
                work_id="work-final",
                functions=("synthesis",),
                cost=0.0022,
                quality=0.76,
                failure=0.02,
            ),
            candidate(
                "node-openai",
                "openai/gpt-oss-120b",
                "groq/fp8",
                work_id="work-openai",
                functions=("analysis",),
                cost=0.001897,
                quality=0.74,
                failure=0.02,
            ),
        ]
        recovery = candidate(
            "node-mistral-recovery",
            "mistralai/mistral-small",
            "mistral",
            work_id="work-qwen",
            functions=("analysis",),
            cost=0.003,
            quality=0.72,
            failure=0.02,
        )
        optimization = {
            "selected_initial_cost_usd": 0.005797,
            "execution_graph": {
                "nodes": [
                    {**row, "node_id": row["candidate_id"]}
                    for row in selected_rows
                ],
                "final_nodes": ["node-deepseek"],
                "metadata": {
                    "interpretation_id": "interpretation-critical"
                },
            },
        }
        bundle = {"candidates": [*selected_rows, recovery]}

        result = policy.rebalance_recovery_pool(optimization, bundle)
        pool = result["execution_graph"]["metadata"]["recovery_pool"]
        self.assertEqual(
            "mistralai/mistral-small",
            pool["node-qwen"][0]["model"],
        )
        evidence = result["recovery_pool_policy"]
        self.assertEqual(
            1,
            evidence["estimated_above_planning_budget_by_node"][
                "node-qwen"
            ],
        )
        self.assertEqual(
            0,
            evidence["budget_excluded_by_node"]["node-qwen"],
        )

        empty_graph = ExecutionGraph(
            nodes=(),
            edges=(),
            execution_stages=(),
            entry_nodes=(),
            final_nodes=(),
            required_work=(),
            estimated_quality=0.0,
            quality_floor=0.0,
            estimated_total_cost=0.0,
            metadata={},
        )
        budget = BudgetController(config, empty_graph)
        for estimated, actual, node_id in (
            (0.0017, 0.0, "node-qwen"),
            (0.0022, 0.00047194, "node-deepseek"),
            (0.001897, 0.0002683, "node-openai"),
        ):
            allowed, reason = budget.reserve("initial", estimated, node_id)
            self.assertTrue(allowed, reason)
            self.assertFalse(budget.reconcile(actual))
        allowed, reason = budget.reserve(
            "replacement",
            recovery["estimated_cost"],
            "node-qwen",
        )
        self.assertTrue(allowed, reason)
        snapshot = budget.snapshot()
        self.assertEqual(4, snapshot["calls_reserved"])
        self.assertEqual(1, snapshot["recovery_calls_reserved"])
        self.assertEqual(0.00074024, snapshot["actual_cost_usd"])

    def test_global_recovery_company_allocation_prioritizes_final_node(self) -> None:
        policy = CrossEndpointPlannerPolicy(self.config())
        analysis = candidate(
            "node-analysis",
            "deepseek/analysis",
            "deepinfra/fp4",
            work_id="work-analysis",
            functions=("analysis",),
            cost=0.001,
            quality=0.70,
            failure=0.02,
        )
        final = candidate(
            "node-final",
            "openai/final",
            "coreweave/fp4",
            work_id="work-final",
            functions=("synthesis",),
            cost=0.002,
            quality=0.65,
            failure=0.03,
        )
        analysis_glm = candidate(
            "node-analysis-glm",
            "z-ai/analysis",
            "decart/fp4",
            work_id="work-analysis",
            functions=("analysis",),
            cost=0.003,
            quality=0.90,
            failure=0.01,
        )
        analysis_qwen = candidate(
            "node-analysis-qwen",
            "qwen/analysis",
            "alibaba/fp8",
            work_id="work-analysis",
            functions=("analysis",),
            cost=0.004,
            quality=0.75,
            failure=0.02,
        )
        final_glm = candidate(
            "node-final-glm",
            "z-ai/final",
            "decart/fp4",
            work_id="work-final",
            functions=("synthesis",),
            cost=0.004,
            quality=0.82,
            failure=0.01,
        )
        final_minimax = candidate(
            "node-final-minimax",
            "minimax/final",
            "gmicloud/fp8",
            work_id="work-final",
            functions=("synthesis",),
            cost=0.005,
            quality=0.78,
            failure=0.02,
        )
        optimization = {
            "selected_initial_cost_usd": 0.003,
            "execution_graph": {
                "nodes": [
                    {**analysis, "node_id": analysis["candidate_id"]},
                    {**final, "node_id": final["candidate_id"]},
                ],
                "final_nodes": [final["candidate_id"]],
                "metadata": {
                    "interpretation_id": "interpretation-critical"
                },
            },
        }
        bundle = {
            "candidates": [
                analysis,
                final,
                analysis_glm,
                analysis_qwen,
                final_glm,
                final_minimax,
            ]
        }

        result = policy.rebalance_recovery_pool(optimization, bundle)
        metadata = result["execution_graph"]["metadata"]
        pool = metadata["recovery_pool"]
        self.assertEqual("z-ai/final", pool["node-final"][0]["model"])
        self.assertEqual(
            "qwen/analysis",
            pool["node-analysis"][0]["model"],
        )
        self.assertTrue(
            metadata["recovery_pool_policy"][
                "critical_nodes_allocated_first"
            ]
        )


if __name__ == "__main__":
    unittest.main()
