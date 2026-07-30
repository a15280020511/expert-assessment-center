import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_planning_benchmark_policy import explicit_independence_metrics  # noqa: E402


def row(candidate_id, copy_index, *, model="a/model", provider="p1", independent=False):
    return {
        "candidate_id": candidate_id,
        "coverage_keys": [f"w1#{copy_index}"],
        "model": model,
        "provider_endpoint": f"{model}@{provider}",
        "estimated_quality": 0.8,
        "estimated_cost": 0.01,
        "independence_groups": ["w1"] if independent else [],
    }


class TestV5PlanningBenchmarkPolicy(unittest.TestCase):
    def test_ordinary_redundancy_reusing_model_is_feasible(self):
        result = explicit_independence_metrics(
            [row("c0", 0), row("c1", 1)],
            {"w1#0", "w1#1"},
            {"w1": 2},
        )
        self.assertTrue(result["feasible"])
        self.assertFalse(result["hard_constraint_violations"])
        self.assertFalse(result["independence_policy"][0]["different_model_required"])

    def test_explicit_independence_reusing_model_is_rejected(self):
        result = explicit_independence_metrics(
            [row("c0", 0, independent=True), row("c1", 1, independent=True)],
            {"w1#0", "w1#1"},
            {"w1": 2},
        )
        self.assertFalse(result["feasible"])
        self.assertIn(
            "w1:independent-copies-reuse-model",
            result["hard_constraint_violations"],
        )

    def test_provider_reuse_is_not_an_undeclared_hard_gate(self):
        result = explicit_independence_metrics(
            [
                row("c0", 0, model="a/model", provider="shared", independent=True),
                row("c1", 1, model="b/model", provider="shared", independent=True),
            ],
            {"w1#0", "w1#1"},
            {"w1": 2},
        )
        self.assertTrue(result["feasible"])
        self.assertEqual(len(result["provider_endpoints"]), 2)
        self.assertFalse(result["independence_policy"][0]["different_provider_required"])


if __name__ == "__main__":
    unittest.main()
