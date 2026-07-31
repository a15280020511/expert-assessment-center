import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from tests.test_v5_planner_executor import TestV5PlannerExecutor  # noqa: E402
from v5_benchmark import planning_benchmark  # noqa: E402


class V5ValueRatioUnitTests(unittest.TestCase):
    def test_public_optimizer_ratio_matches_benchmark_ratio(self):
        fixture = TestV5PlannerExecutor()
        planner = fixture.planner()
        optimization = planner["optimization"]
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
        self.assertAlmostEqual(
            optimization["cost_performance_ratio"],
            benchmark_ratio,
            places=5,
        )
        self.assertAlmostEqual(
            optimization["cost_performance_ratio"],
            optimization["scaled_objective_ratio"]
            * optimization["cost_scale"]
            / optimization["quality_scale"],
            places=5,
        )


if __name__ == "__main__":
    unittest.main()
