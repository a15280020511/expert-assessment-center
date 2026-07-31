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


def alternative(candidate_id, work_id, model, provider, cost):
    return {
        "candidate_id": candidate_id,
        "interpretation_id": "i1",
        "coverage_keys": [f"{work_id}#0"],
        "assigned_work": [work_id],
        "copy_indices": [0],
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
                alternative("w1-openai", "w1", "openai/gpt-b", "p2", 0.001),
                alternative("w1-google", "w1", "google/gemini-a", "p2", 0.002),
                alternative("w1-mistral", "w1", "mistralai/mistral-a", "p3", 0.003),
                alternative("w2-google", "w2", "google/gemini-b", "p2", 0.001),
                alternative("w2-zhipu", "w2", "z-ai/glm-a", "p3", 0.002),
                alternative("w2-mistral", "w2", "mistralai/mistral-b", "p4", 0.003),
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
        self.assertTrue(
            metadata["recovery_pool_policy"][
                "recovery_companies_globally_unique"
            ]
        )
        self.assertEqual(
            sorted(set(recovery_companies)),
            metadata["recovery_pool_policy"][
                "reserved_recovery_companies"
            ],
        )


if __name__ == "__main__":
    unittest.main()
