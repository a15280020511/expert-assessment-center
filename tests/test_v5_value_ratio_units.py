import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import GraphLimits  # noqa: E402
from v5_benchmark import planning_benchmark  # noqa: E402
import v5_value_optimizer as value_optimizer  # noqa: E402


def candidate(candidate_id, coverage_key, model, quality, cost):
    work_id = coverage_key.split("#", 1)[0]
    provider = f"provider-{candidate_id}"
    return {
        "candidate_id": candidate_id,
        "interpretation_id": "i1",
        "coverage_keys": [coverage_key],
        "assigned_work": [work_id],
        "copy_indices": [0],
        "professional_capabilities": {"analysis": quality},
        "functions": ["analysis"],
        "prompt_profile": {"profile_id": f"prompt-{candidate_id}"},
        "reasoning_profile": {
            "reasoning_enabled": True,
            "effort": "medium",
        },
        "parameter_profile": {"profile_id": f"params-{candidate_id}"},
        "model": model,
        "provider_endpoint": f"{model}@{provider}",
        "provider_slug": provider,
        "output_contract": {
            "required_fields": [],
            "machine_readable_required": False,
        },
        "estimated_quality": quality,
        "quality_uncertainty": 0.08,
        "estimated_cost": cost,
        "failure_probability": 0.04,
        "request_config": {
            "provider": {
                "order": [provider],
                "only": [provider],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
        "independence_groups": [],
    }


def candidate_graph():
    rows = [
        candidate("oa-w1", "w1#0", "openai/gpt-a", 0.92, 0.012),
        candidate("an-w1", "w1#0", "anthropic/claude-a", 0.86, 0.009),
        candidate("go-w2", "w2#0", "google/gemini-a", 0.90, 0.011),
        candidate("mi-w2", "w2#0", "mistralai/mistral-a", 0.84, 0.008),
    ]
    return {
        "version": 5,
        "candidates": rows,
        "interpretations": {
            "i1": {
                "metrics": {"interpretation_score": 0.8},
                "work_ids": ["w1", "w2"],
                "copies_by_work": {"w1": 1, "w2": 1},
                "atomic_edges": [],
            }
        },
    }


class V5ValueRatioUnitTests(unittest.TestCase):
    def test_public_optimizer_ratio_matches_benchmark_units(self):
        candidates = candidate_graph()
        optimization = value_optimizer.optimize_execution_graph(
            candidates,
            limits=GraphLimits(
                max_nodes=2,
                max_edges=4,
                max_stages=2,
                max_model_calls=2,
                max_retries=0,
                max_replacements=1,
            ),
            quality_tolerance_pct=2.0,
            solver_timeout_seconds=10.0,
        )
        planner = {
            "candidate_graph": candidates,
            "optimization": optimization,
        }
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
        self.assertEqual(
            len(optimization["selected_model_companies"]),
            2,
        )

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
