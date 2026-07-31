import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from tests.test_v5_planner_executor import TestV5PlannerExecutor  # noqa: E402
from execution_graph import GraphLimits  # noqa: E402
from v5_benchmark import planning_benchmark  # noqa: E402
import v5_value_optimizer as value_optimizer  # noqa: E402


class V5ValueRatioUnitTests(unittest.TestCase):
    def test_public_optimizer_ratio_matches_benchmark_units(self):
        fixture = TestV5PlannerExecutor()
        base = fixture.planner()
        optimization = value_optimizer.optimize_execution_graph(
            base["candidate_graph"],
            limits=GraphLimits(
                max_nodes=16,
                max_edges=64,
                max_stages=8,
                max_model_calls=16,
                max_retries=0,
                max_replacements=2,
            ),
            quality_tolerance_pct=2.0,
            solver_timeout_seconds=10.0,
            require_distinct_model_companies=False,
        )
        planner = {**base, "optimization": optimization}
        benchmark = planning_benchmark(planner)
        benchmark_ratio = benchmark["strategies"]["v5_joint_graph"][
            "cost_performance_ratio"
        ]

        self.assertEqual(
            optimization["cost_performance_ratio_unit"],
            "risk_adjusted_utility_per_effective_expected_usd",
        )
        self.assertEqual(optimization["quality_scale"], 100_000)
        self.assertEqual(optimization["cost_scale"], 1_000_000)

        relative_error = abs(
            optimization["cost_performance_ratio"] - benchmark_ratio
        ) / benchmark_ratio
        self.assertLessEqual(relative_error, 0.02)
        self.assertAlmostEqual(
            optimization["cost_performance_ratio"],
            optimization["scaled_objective_ratio"]
            * optimization["cost_scale"]
            / optimization["quality_scale"],
            places=5,
        )


if __name__ == "__main__":
    unittest.main()
