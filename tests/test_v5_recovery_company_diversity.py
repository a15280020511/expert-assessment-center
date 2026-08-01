import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_model_company import candidate_company  # noqa: E402
from v5_recovery_runtime import CrossEndpointPlannerPolicy  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402


def selected(node_id, work_id, model, provider):
    return {
        "node_id": node_id,
        "candidate_id": node_id,
        "interpretation_id": "i1",
        "assigned_work": [work_id],
        "model": model,
        "provider_slug": provider,
        "provider_endpoint": f"{model}@{provider}",
    }


def alternative(
    candidate_id,
    work_id,
    model,
    provider,
    cost,
    *,
    copy_index=0,
):
    return {
        "candidate_id": candidate_id,
        "interpretation_id": "i1",
        "coverage_keys": [f"{work_id}#{copy_index}"],
        "assigned_work": [work_id],
        "copy_indices": [copy_index],
        "professional_capabilities": {"analysis": 0.8},
        "functions": ["analysis"],
        "prompt_profile": {},
        "reasoning_profile": {},
        "parameter_profile": {},
        "model": model,
        "provider_slug": provider,
        "provider_endpoint": f"{model}@{provider}",
        "output_contract": {},
        "estimated_quality": 0.8,
        "quality_uncertainty": 0.1,
        "estimated_cost": cost,
        "failure_probability": 0.05,
        "request_config": {},
        "independence_groups": [],
    }


class V5RecoveryCompanyDiversityTests(unittest.TestCase):
    def test_recovery_companies_are_globally_unique_and_selected_excluded(self):
        policy = CrossEndpointPlannerPolicy(
            RuntimeConfig(
                total_call_limit=6,
                recovery_call_limit=2,
                cost_anomaly_usd=None,
                quality_tier="value",
                maximum_candidates_per_work=4,
            )
        )
        selected_nodes = [
            selected("n1", "w1", "openai/gpt-a", "p1"),
            selected("n2", "w2", "anthropic/claude-a", "p1"),
        ]
        optimization = {
            "execution_graph": {
                "nodes": selected_nodes,
                "metadata": {"interpretation_id": "i1"},
            }
        }
        candidate_bundle = {
            "candidates": [
                {**selected_nodes[0], "coverage_keys": ["w1#0"]},
                {**selected_nodes[1], "coverage_keys": ["w2#0"]},
                alternative(
                    "w1-openai",
                    "w1",
                    "openai/gpt-b",
                    "p2",
                    0.001,
                ),
                alternative(
                    "w1-google",
                    "w1",
                    "google/gemini-a",
                    "p2",
                    0.002,
                ),
                alternative(
                    "w1-mistral",
                    "w1",
                    "mistralai/mistral-a",
                    "p3",
                    0.003,
                ),
                alternative(
                    "w2-google",
                    "w2",
                    "google/gemini-b",
                    "p2",
                    0.001,
                ),
                alternative(
                    "w2-zhipu",
                    "w2",
                    "z-ai/glm-a",
                    "p3",
                    0.002,
                ),
                alternative(
                    "w2-mistral",
                    "w2",
                    "mistralai/mistral-b",
                    "p4",
                    0.003,
                ),
            ]
        }

        result = policy.rebalance_recovery_pool(
            optimization,
            candidate_bundle,
        )
        metadata = result["execution_graph"]["metadata"]
        pools = metadata["recovery_pool"]
        selected_companies = {
            candidate_company(row) for row in selected_nodes
        }
        recovery_companies = [
            candidate_company(row)
            for rows in pools.values()
            for row in rows
        ]

        self.assertTrue(
            selected_companies.isdisjoint(recovery_companies)
        )
        self.assertEqual(
            len(recovery_companies),
            len(set(recovery_companies)),
        )
        self.assertNotIn("openai", recovery_companies)
        self.assertNotIn("anthropic", recovery_companies)
        policy_evidence = metadata["recovery_pool_policy"]
        self.assertTrue(
            policy_evidence["recovery_companies_globally_unique"]
        )
        self.assertEqual(
            sorted(set(recovery_companies)),
            policy_evidence["reserved_recovery_companies"],
        )
        self.assertEqual(
            policy_evidence["source"],
            "current-run-frozen-candidate-graph",
        )
        self.assertFalse(policy_evidence["cross_task_history_used"])

    def test_recovery_preserves_independent_copy_coverage_key(self):
        policy = CrossEndpointPlannerPolicy(
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=1,
                cost_anomaly_usd=None,
                quality_tier="value",
                maximum_candidates_per_work=4,
            )
        )
        selected_node = selected(
            "selected-copy-1",
            "work-independent",
            "openai/gpt-a",
            "p1",
        )
        optimization = {
            "execution_graph": {
                "nodes": [selected_node],
                "metadata": {"interpretation_id": "i1"},
            }
        }
        candidate_bundle = {
            "candidates": [
                {
                    **selected_node,
                    "coverage_keys": ["work-independent#1"],
                },
                alternative(
                    "wrong-copy",
                    "work-independent",
                    "google/gemini-wrong",
                    "p2",
                    0.001,
                    copy_index=0,
                ),
                alternative(
                    "right-copy",
                    "work-independent",
                    "anthropic/claude-right",
                    "p3",
                    0.002,
                    copy_index=1,
                ),
            ]
        }
        result = policy.rebalance_recovery_pool(
            optimization,
            candidate_bundle,
        )
        pool = result["execution_graph"]["metadata"][
            "recovery_pool"
        ]["selected-copy-1"]
        self.assertEqual(
            [row["candidate_id"] for row in pool],
            ["right-copy"],
        )
        self.assertEqual(
            pool[0]["coverage_keys"],
            ["work-independent#1"],
        )
        policy_evidence = result["recovery_pool_policy"]
        self.assertEqual(
            policy_evidence["source"],
            "current-run-frozen-candidate-graph",
        )
        self.assertTrue(
            policy_evidence["candidate_options_do_not_reserve_paid_calls"]
        )
        self.assertTrue(
            policy_evidence["actual_recovery_calls_remain_budget_limited"]
        )

    def test_zero_recovery_budget_produces_empty_pool(self):
        policy = CrossEndpointPlannerPolicy(
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=0,
                cost_anomaly_usd=None,
                quality_tier="value",
                maximum_candidates_per_work=4,
            )
        )
        selected_node = selected("n1", "w1", "openai/gpt-a", "p1")
        result = policy.rebalance_recovery_pool(
            {
                "execution_graph": {
                    "nodes": [selected_node],
                    "metadata": {"interpretation_id": "i1"},
                }
            },
            {
                "candidates": [
                    {**selected_node, "coverage_keys": ["w1#0"]},
                    alternative(
                        "backup",
                        "w1",
                        "google/gemini-a",
                        "p2",
                        0.001,
                    ),
                ]
            },
        )
        metadata = result["execution_graph"]["metadata"]
        self.assertEqual(metadata["recovery_pool"]["n1"], [])
        self.assertEqual(
            metadata["recovery_pool_policy"][
                "maximum_candidates_per_selected_node"
            ],
            0,
        )


if __name__ == "__main__":
    unittest.main()
