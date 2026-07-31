import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_planner  # noqa: E402
import v5_value_optimizer as optimizer  # noqa: E402
from execution_graph import GraphLimits  # noqa: E402
from v5_planning_runtime import PlannerPolicy  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402


def candidate(
    candidate_id,
    coverage_key,
    *,
    model="a/model",
    provider="shared-provider",
    independence=False,
    cost=0.01,
):
    return {
        "candidate_id": candidate_id,
        "interpretation_id": "i1",
        "coverage_keys": [coverage_key],
        "assigned_work": ["w1"],
        "copy_indices": [int(coverage_key.rsplit("#", 1)[1])],
        "professional_capabilities": {"analysis": 0.8},
        "functions": ["evidence_validation" if independence else "analysis"],
        "prompt_profile": {"evidence_discipline": 0.8},
        "reasoning_profile": {"reasoning_enabled": True, "effort": "medium"},
        "parameter_profile": {"max_tokens": 1000},
        "model": model,
        "provider_endpoint": f"{model}@{provider}",
        "provider_slug": provider,
        "output_contract": {"machine_readable_required": False},
        "estimated_quality": 0.8,
        "quality_uncertainty": 0.1,
        "estimated_cost": cost,
        "failure_probability": 0.05,
        "request_config": {"model": model, "max_tokens": 1000},
        "independence_groups": ["w1"] if independence else [],
    }


def bundle(rows):
    return {
        "version": 5,
        "candidates": rows,
        "interpretations": {
            "i1": {
                "metrics": {"interpretation_score": 0.8},
                "work_ids": ["w1"],
                "copies_by_work": {"w1": 2},
                "atomic_edges": [],
            }
        },
    }


class TestV5ExplicitIndependencePolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = PlannerPolicy(RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            quality_tier="value",
        ))

    def limits(self):
        return GraphLimits(
            max_nodes=4,
            max_edges=4,
            max_stages=2,
            max_model_calls=4,
            max_retries=0,
            max_replacements=0,
            max_budget_usd=0.10,
        )

    def optimize(self, rows):
        return self.policy.optimize_execution_graph(
            bundle(rows),
            limits=self.limits(),
            quality_tolerance_pct=2.0,
            solver_timeout_seconds=2,
        )

    def test_ordinary_redundant_copies_can_use_same_model_and_provider(self):
        result = self.optimize([
            candidate("c0", "w1#0"),
            candidate("c1", "w1#1"),
        ])
        graph = result["execution_graph"]
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual({row["model"] for row in graph["nodes"]}, {"a/model"})
        self.assertEqual(
            graph["metadata"]["independence_policy"]["hard_model_diversity_scope"],
            "explicit-independence-groups-only",
        )
        policy = result["hard_independence_constraints"][0]
        self.assertFalse(policy["different_model_required"])
        self.assertFalse(policy["different_provider_required"])

    def test_explicit_independence_group_rejects_same_model(self):
        with self.assertRaisesRegex(
            v5_planner.V5PlanningError,
            "No feasible V5 execution graph",
        ):
            self.optimize([
                candidate("c0", "w1#0", independence=True),
                candidate("c1", "w1#1", independence=True),
            ])

    def test_explicit_independence_allows_two_models_on_same_provider(self):
        result = self.optimize([
            candidate("c0", "w1#0", model="a/model", independence=True),
            candidate("c1", "w1#1", model="b/model", independence=True),
        ])
        graph = result["execution_graph"]
        self.assertEqual({row["model"] for row in graph["nodes"]}, {"a/model", "b/model"})
        policy = result["hard_independence_constraints"][0]
        self.assertTrue(policy["different_model_required"])
        self.assertFalse(policy["different_provider_required"])

    def test_formal_runtime_uses_explicit_value_optimizer(self):
        original = v5_planner.optimize_execution_graph
        source = (ROOT / "open-model-market" / "v5_planning_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("value_optimizer.optimize_execution_graph", source)
        self.assertIs(v5_planner.optimize_execution_graph, original)
        self.assertIsNot(v5_planner.optimize_execution_graph, optimizer.optimize_execution_graph)


if __name__ == "__main__":
    unittest.main()
