import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import GraphLimits  # noqa: E402
from v5_live_benchmark_resilient import planning_limits  # noqa: E402


class TestV5ResilientEntry(unittest.TestCase):
    def test_planner_uses_risk_discounted_budget_without_mutating_runtime_limit(self):
        runtime = GraphLimits(max_budget_usd=0.25, cost_risk_multiplier=4.0)
        planned = planning_limits(runtime)
        self.assertEqual(runtime.max_budget_usd, 0.25)
        self.assertEqual(planned.max_budget_usd, 0.0625)
        self.assertEqual(planned.cost_risk_multiplier, 4.0)

    def test_no_budget_remains_unbounded(self):
        runtime = GraphLimits(max_budget_usd=None, cost_risk_multiplier=4.0)
        self.assertIs(planning_limits(runtime), runtime)


if __name__ == "__main__":
    unittest.main()
