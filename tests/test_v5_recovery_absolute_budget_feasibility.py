from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_cross_endpoint_planner import CrossEndpointPlannerPolicy  # noqa: E402
from v5_planner import V5PlanningError  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402


def candidate(candidate_id: str, model: str, company: str, cost: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "interpretation_id": "interpretation-budget",
        "coverage_keys": ["work-a#0"],
        "assigned_work": ["work-a"],
        "copy_indices": [0],
        "professional_capabilities": {},
        "functions": ["analysis"],
        "prompt_profile": {},
        "reasoning_profile": {},
        "parameter_profile": {"model_company": company},
        "model_company": company,
        "model": model,
        "provider_endpoint": f"{model}@provider-{company}",
        "provider_slug": f"provider-{company}",
        "output_contract": {},
        "estimated_quality": 0.8,
        "quality_uncertainty": 0.1,
        "estimated_cost": cost,
        "failure_probability": 0.02,
        "request_config": {},
        "independence_groups": [],
    }


class V5RecoveryAbsoluteBudgetFeasibilityTests(unittest.TestCase):
    def test_planning_fails_when_every_recovery_candidate_exceeds_absolute_cap(self) -> None:
        selected = candidate("node-selected", "google/selected", "google", 0.05)
        openai = candidate("node-openai", "openai/recovery", "openai", 0.25986871)
        anthropic = candidate("node-anthropic", "anthropic/recovery", "anthropic", 0.27162259)
        optimization = {
            "selected_initial_cost_usd": 0.05,
            "execution_graph": {
                "nodes": [{**selected, "node_id": "node-selected"}],
                "final_nodes": [],
                "metadata": {"interpretation_id": "interpretation-budget"},
            },
        }
        policy = CrossEndpointPlannerPolicy(
            RuntimeConfig(2, 1, 0.25, "value")
        )
        with self.assertRaisesRegex(
            V5PlanningError,
            "Recovery reserve is not executable",
        ):
            policy.rebalance_recovery_pool(
                optimization,
                {"candidates": [selected, openai, anthropic]},
            )

    def test_risk_adjusted_remaining_guard_excludes_unexecutable_candidate(self) -> None:
        selected = candidate("node-selected", "google/selected", "google", 0.12)
        recovery = candidate("node-openai", "openai/recovery", "openai", 0.25986871)
        optimization = {
            "selected_initial_cost_usd": 0.12,
            "execution_graph": {
                "nodes": [{**selected, "node_id": "node-selected"}],
                "final_nodes": [],
                "metadata": {"interpretation_id": "interpretation-budget"},
            },
        }
        policy = CrossEndpointPlannerPolicy(
            RuntimeConfig(2, 1, 0.35, "value")
        )
        with self.assertRaisesRegex(
            V5PlanningError,
            "Recovery reserve is not executable",
        ):
            policy.rebalance_recovery_pool(
                optimization,
                {"candidates": [selected, recovery]},
            )

    def test_risk_adjusted_candidate_within_remaining_guard_is_frozen(self) -> None:
        selected = candidate("node-selected", "google/selected", "google", 0.12)
        recovery = candidate("node-openai", "openai/recovery", "openai", 0.10)
        optimization = {
            "selected_initial_cost_usd": 0.12,
            "execution_graph": {
                "nodes": [{**selected, "node_id": "node-selected"}],
                "final_nodes": [],
                "metadata": {"interpretation_id": "interpretation-budget"},
            },
        }
        policy = CrossEndpointPlannerPolicy(
            RuntimeConfig(2, 1, 0.35, "value")
        )
        result = policy.rebalance_recovery_pool(
            optimization,
            {"candidates": [selected, recovery]},
        )
        row = result["execution_graph"]["metadata"]["recovery_pool"][
            "node-selected"
        ][0]
        self.assertEqual("openai/recovery", row["model"])
        self.assertFalse(row["planning_budget_advisory_only"])
        self.assertLessEqual(row["recovery_risk_adjusted_cost_usd"], 0.23)
        self.assertTrue(
            result["recovery_pool_policy"][
                "risk_adjusted_remaining_budget_enforced_at_planning"
            ]
        )


if __name__ == "__main__":
    unittest.main()
