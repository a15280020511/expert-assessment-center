import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import GraphLimits  # noqa: E402
import v5_budget_runtime_parity as parity  # noqa: E402
import v5_planning_runtime as planning_runtime  # noqa: E402


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

    def test_explicit_production_planner_applies_budget_parity_without_install(self):
        captured = {}

        def fake_optimize(candidate_bundle, *, limits, **kwargs):
            captured["budget"] = limits.max_budget_usd
            return {
                "execution_graph": {
                    "estimated_total_cost": 0.21,
                    "metadata": {},
                },
                "selected_candidate_ids": ["n1"],
            }

        policy = planning_runtime.PlannerPolicy(SimpleNamespace())
        limits = GraphLimits(max_budget_usd=0.25, cost_risk_multiplier=1.18)
        with patch.object(
            planning_runtime.value_optimizer,
            "optimize_execution_graph",
            side_effect=fake_optimize,
        ):
            result = policy.optimize_execution_graph(
                {"candidates": [{}]},
                limits=limits,
                quality_tolerance_pct=2.0,
                solver_timeout_seconds=20.0,
            )

        self.assertAlmostEqual(captured["budget"], 0.25 / 1.18)
        evidence = result["budget_preflight_parity"]
        self.assertAlmostEqual(evidence["selected_raw_cost_usd"], 0.21)
        self.assertAlmostEqual(evidence["selected_risk_adjusted_cost_usd"], 0.2478)
        self.assertFalse(evidence["global_monkey_patching"])
        self.assertEqual(
            result["execution_graph"]["metadata"]["budget_preflight_parity"],
            evidence,
        )

    def test_explicit_production_planner_rejects_post_solve_parity_violation(self):
        def fake_optimize(candidate_bundle, *, limits, **kwargs):
            return {
                "execution_graph": {
                    "estimated_total_cost": 0.22,
                    "metadata": {},
                }
            }

        policy = planning_runtime.PlannerPolicy(SimpleNamespace())
        with patch.object(
            planning_runtime.value_optimizer,
            "optimize_execution_graph",
            side_effect=fake_optimize,
        ):
            with self.assertRaisesRegex(Exception, "Risk-adjusted"):
                policy.optimize_execution_graph(
                    {"candidates": [{}]},
                    limits=GraphLimits(
                        max_budget_usd=0.25,
                        cost_risk_multiplier=1.18,
                    ),
                    quality_tolerance_pct=2.0,
                    solver_timeout_seconds=20.0,
                )

    def test_boundary_matrix_never_exposes_runtime_rejected_raw_budget(self):
        for hard_budget in (0.01, 0.10, 0.25, 1.0, 10.0):
            for multiplier in (1.0, 1.01, 1.18, 1.25, 2.0):
                with self.subTest(hard_budget=hard_budget, multiplier=multiplier):
                    limits = GraphLimits(
                        max_budget_usd=hard_budget,
                        cost_risk_multiplier=multiplier,
                    )
                    raw_budget = parity.planning_raw_budget_usd(limits)
                    self.assertIsNotNone(raw_budget)
                    self.assertLessEqual(raw_budget * multiplier, hard_budget + 1e-12)

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
