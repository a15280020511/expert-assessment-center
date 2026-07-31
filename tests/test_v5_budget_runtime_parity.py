import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import GraphLimits  # noqa: E402
import v5_budget_runtime_parity as parity  # noqa: E402
from v5_planning_runtime import PlannerPolicy  # noqa: E402


class TestV5BudgetRuntimeParity(unittest.TestCase):
    def test_optimizer_receives_hard_budget_divided_by_risk_multiplier(self):
        captured = {}

        def fake_optimize(candidate_bundle, *, limits, **kwargs):
            captured["budget"] = limits.max_budget_usd
            return {
                "execution_graph": {
                    "estimated_total_cost": 0.19,
                    "metadata": {},
                },
                "selected_candidate_ids": ["n1"],
            }

        with patch.object(parity, "_ORIGINAL_OPTIMIZE", side_effect=fake_optimize):
            result = parity.risk_budgeted_optimize_execution_graph(
                {"candidates": [{}] * 20},
                limits=GraphLimits(max_budget_usd=0.25, cost_risk_multiplier=1.25),
                solver_timeout_seconds=12.0,
            )

        self.assertAlmostEqual(captured["budget"], 0.20)
        evidence = result["budget_preflight_parity"]
        self.assertAlmostEqual(evidence["selected_risk_adjusted_cost_usd"], 0.2375)
        self.assertGreaterEqual(evidence["adaptive_ratio_iterations"], 4)
        self.assertLessEqual(evidence["adaptive_ratio_iterations"], 18)

    def test_explicit_planner_policy_calls_parity_policy_directly(self):
        captured = {}

        def fake_optimize(candidate_bundle, *, limits, **kwargs):
            captured["budget"] = limits.max_budget_usd
            return {
                "execution_graph": {
                    "estimated_total_cost": 0.211,
                    "metadata": {},
                },
                "selected_candidate_ids": ["n1"],
            }

        policy = PlannerPolicy(runtime_config=None)
        with patch.object(parity, "_ORIGINAL_OPTIMIZE", side_effect=fake_optimize):
            result = policy.optimize_execution_graph(
                {"candidates": [{}] * 8},
                limits=GraphLimits(max_budget_usd=0.25, cost_risk_multiplier=1.18),
                quality_tolerance_pct=2.0,
                solver_timeout_seconds=20.0,
            )

        self.assertAlmostEqual(captured["budget"], 0.25 / 1.18)
        evidence = result["budget_preflight_parity"]
        self.assertAlmostEqual(evidence["selected_raw_cost_usd"], 0.211)
        self.assertAlmostEqual(evidence["selected_risk_adjusted_cost_usd"], 0.24898)
        self.assertEqual(
            result["execution_graph"]["metadata"]["budget_preflight_parity"],
            evidence,
        )

    def test_post_solve_rounding_guard_rejects_runtime_budget_violation(self):
        def fake_optimize(candidate_bundle, *, limits, **kwargs):
            return {
                "execution_graph": {
                    "estimated_total_cost": 0.21,
                    "metadata": {},
                }
            }

        with patch.object(parity, "_ORIGINAL_OPTIMIZE", side_effect=fake_optimize):
            with self.assertRaisesRegex(Exception, "Risk-adjusted"):
                parity.risk_budgeted_optimize_execution_graph(
                    {"candidates": [{}]},
                    limits=GraphLimits(max_budget_usd=0.25, cost_risk_multiplier=1.25),
                )

    def test_unbounded_budget_remains_unbounded(self):
        self.assertIsNone(parity.planning_raw_budget_usd(GraphLimits(max_budget_usd=None)))


if __name__ == "__main__":
    unittest.main()
