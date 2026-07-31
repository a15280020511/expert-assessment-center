import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_benchmark import _metrics  # noqa: E402


def row(candidate_id, copy_index, *, model="a/model", provider="p1"):
    return {
        "candidate_id": candidate_id,
        "coverage_keys": [f"w1#{copy_index}"],
        "model": model,
        "provider_endpoint": f"{model}@{provider}",
        "estimated_quality": 0.8,
        "quality_uncertainty": 0.05,
        "estimated_cost": 0.01,
        "failure_probability": 0.05,
    }


class TestV5PlanningBenchmarkPolicy(unittest.TestCase):
    def metrics(self, rows, *, distinct_model=False, distinct_provider=False):
        return _metrics(
            rows,
            {"w1#0", "w1#1"},
            {"w1": 2},
            {
                "w1": {
                    "different_model_required": distinct_model,
                    "different_provider_required": distinct_provider,
                }
            },
            0.8,
        )

    def test_ordinary_redundancy_reusing_model_is_feasible(self):
        result = self.metrics([row("c0", 0), row("c1", 1)])
        self.assertTrue(result["feasible"])
        self.assertFalse(result["hard_constraint_violations"])

    def test_explicit_independence_reusing_model_is_rejected(self):
        result = self.metrics(
            [row("c0", 0), row("c1", 1)],
            distinct_model=True,
        )
        self.assertFalse(result["feasible"])
        self.assertIn(
            "w1:independent-copies-reuse-model",
            result["hard_constraint_violations"],
        )

    def test_provider_reuse_is_not_an_undeclared_hard_gate(self):
        result = self.metrics(
            [
                row("c0", 0, model="a/model", provider="shared"),
                row("c1", 1, model="b/model", provider="shared"),
            ],
            distinct_model=True,
            distinct_provider=False,
        )
        self.assertTrue(result["feasible"])
        self.assertEqual(len(result["provider_endpoints"]), 2)


if __name__ == "__main__":
    unittest.main()
