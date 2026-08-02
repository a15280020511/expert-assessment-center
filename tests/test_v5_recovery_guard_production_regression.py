from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph  # noqa: E402
from v5_cross_endpoint_planner import CrossEndpointPlannerPolicy  # noqa: E402
from v5_operational_resilience import contract_visible_token_floor  # noqa: E402
from v5_runtime import BudgetController, RuntimeConfig  # noqa: E402


def row(candidate_id: str, model: str, company: str, cost: float, quality: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "interpretation_id": "i",
        "coverage_keys": ["final#0"],
        "assigned_work": ["final"],
        "functions": ["synthesis"],
        "model": model,
        "model_company": company,
        "provider_endpoint": f"{model}@{company}",
        "provider_slug": company,
        "estimated_cost": cost,
        "estimated_quality": quality,
        "quality_uncertainty": 0.10,
        "failure_probability": 0.025,
        "parameter_profile": {
            "model_company": company,
            "p95_token_usage_multiplier": 1.18,
            "structured_p95_token_usage_multiplier": 1.22,
            "operational_serviceability": {"estimated_deadline_ratio": 0.66},
        },
        "professional_capabilities": {},
        "prompt_profile": {},
        "reasoning_profile": {},
        "output_contract": {},
        "request_config": {},
        "independence_groups": [],
    }


class RecoveryGuardProductionRegressionTests(unittest.TestCase):
    def test_expensive_recovery_is_excluded_and_cheaper_value_candidate_ranks_first(self) -> None:
        config = RuntimeConfig(5, 1, 0.35, "value")
        policy = CrossEndpointPlannerPolicy(config)
        selected = row("selected", "z-ai/glm", "zhipu", 0.015, 0.80)
        anthropic = row("anthropic", "anthropic/opus", "anthropic", 0.27289742, 0.851337)
        google = row("google", "google/flash", "google", 0.04837589, 0.773448)
        qwen = row("qwen", "qwen/max", "alibaba", 0.05178836, 0.73699)
        optimization = {
            "selected_initial_cost_usd": 0.041668,
            "execution_graph": {
                "nodes": [{**selected, "node_id": "selected"}],
                "final_nodes": ["selected"],
                "metadata": {"interpretation_id": "i"},
            },
        }
        result = policy.rebalance_recovery_pool(
            optimization,
            {"candidates": [selected, anthropic, google, qwen]},
        )
        pool = result["execution_graph"]["metadata"]["recovery_pool"]["selected"]
        models = [candidate["model"] for candidate in pool]
        self.assertNotIn("anthropic/opus", models)
        self.assertEqual("google/flash", models[0])
        self.assertGreater(
            result["recovery_pool_policy"]["budget_excluded_by_node"]["selected"],
            0,
        )

    def test_runtime_revalidates_frozen_recovery_multiplier(self) -> None:
        config = RuntimeConfig(2, 1, 0.35, "value")
        graph = ExecutionGraph(
            nodes=(), edges=(), execution_stages=(), entry_nodes=(), final_nodes=(),
            required_work=(), estimated_quality=0.0, quality_floor=0.0,
            estimated_total_cost=0.0, metadata={},
        )
        budget = BudgetController(config, graph)
        ok, reason = budget.reserve("initial", 0.01, "initial")
        self.assertTrue(ok, reason)
        budget.reconcile(0.01)
        multiplier = 1.18 * 1.22
        ok, reason = budget.reserve(
            "replacement",
            0.27289742,
            "final",
            risk_multiplier=multiplier,
        )
        self.assertFalse(ok)
        self.assertEqual("risk-adjusted-cost-anomaly-limit-exhausted", reason)
        denial = budget.snapshot()["denials"][-1]
        self.assertAlmostEqual(multiplier, denial["risk_multiplier"], places=8)
        self.assertAlmostEqual(
            0.27289742 * multiplier,
            denial["risk_adjusted_cost_usd"],
            places=8,
        )

    def test_explicit_contract_uses_completion_token_floor_for_deadline(self) -> None:
        candidate = SimpleNamespace(
            parameter_profile={
                "explicit_output_contract_expected": True,
                "estimated_completion_usage_tokens": 9665,
            }
        )
        tokens, applied, floor = contract_visible_token_floor(candidate, 2746)
        self.assertEqual(9665, tokens)
        self.assertTrue(applied)
        self.assertEqual(9665, floor)


if __name__ == "__main__":
    unittest.main()
