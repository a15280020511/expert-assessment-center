import sys
import unittest
from pathlib import Path

from ortools.sat.python import cp_model

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from cost_performance_solver import solve_cost_performance  # noqa: E402
from execution_graph import GraphLimits  # noqa: E402
from v5_value_planner import optimize_execution_graph  # noqa: E402


class TestCostPerformancePrinciple(unittest.TestCase):
    def test_fractional_solver_prefers_higher_value_not_highest_quality(self):
        model = cp_model.CpModel()
        value = model.NewBoolVar("value")
        premium = model.NewBoolVar("premium")
        model.Add(value + premium == 1)

        numerator = 80 * value + 95 * premium
        actual_cost = 10 * value + 200 * premium
        calls = value + premium
        solved = solve_cost_performance(
            model,
            numerator_expr=numerator,
            denominator_expr=actual_cost + calls,
            actual_cost_expr=actual_cost,
            call_count_expr=calls,
            tie_break_penalty_expr=actual_cost * 100 + calls,
            timeout_seconds=5,
            workers=1,
        )

        self.assertEqual(solved.solver.Value(value), 1)
        self.assertEqual(solved.solver.Value(premium), 0)
        self.assertEqual(solved.numerator_value, 80)
        self.assertEqual(solved.actual_cost_value, 10)

    @staticmethod
    def candidate(candidate_id, model, provider, quality, cost):
        return {
            "candidate_id": candidate_id,
            "interpretation_id": "interpretation-1",
            "coverage_keys": ["work-1#0"],
            "assigned_work": ["work-1"],
            "copy_indices": [0],
            "professional_capabilities": {"general_analysis": quality},
            "functions": ["analysis"],
            "prompt_profile": {"profile_id": "prompt-1", "modules": ["scope"]},
            "reasoning_profile": {"reasoning_enabled": True, "effort": "medium"},
            "parameter_profile": {"profile_id": "params-1", "parameters": {}},
            "model": model,
            "provider_endpoint": f"{model}@{provider}",
            "provider_slug": provider,
            "output_contract": {
                "required_fields": ["conclusions"],
                "machine_readable_required": False,
                "must_separate_fact_assumption_inference": True,
            },
            "estimated_quality": quality,
            "quality_uncertainty": 0.10,
            "estimated_cost": cost,
            "failure_probability": 0.10,
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

    def test_v5_artifact_has_no_quality_first_phase(self):
        bundle = {
            "version": 5,
            "candidates": [
                self.candidate("node-value", "vendor/value", "provider-value", 0.80, 0.01),
                self.candidate("node-premium", "vendor/premium", "provider-premium", 0.95, 0.20),
            ],
            "interpretations": {
                "interpretation-1": {
                    "metrics": {"interpretation_score": 0.50},
                    "work_ids": ["work-1"],
                    "copies_by_work": {"work-1": 1},
                    "atomic_edges": [],
                }
            },
        }
        result = optimize_execution_graph(
            bundle,
            limits=GraphLimits(max_nodes=4, max_edges=4, max_stages=2, max_model_calls=4),
            solver_timeout_seconds=5,
        )

        self.assertEqual(result["selected_candidate_ids"], ["node-value"])
        self.assertFalse(result["quality_first_phase_used"])
        self.assertFalse(result["quality_tolerance_band_used"])
        self.assertEqual(
            result["objective_order"],
            [
                "hard_constraints",
                "maximum_cost_performance",
                "minimum_cost_calls_failure_as_tiebreakers",
            ],
        )
        self.assertEqual(result["execution_graph"]["quality_floor"], 0.0)
        self.assertNotIn("best_quality_objective_scaled", result)
        self.assertNotIn("quality_floor_objective_scaled", result)

    def test_formal_entrypoints_use_cost_performance_modules(self):
        production = (ROOT / "open-model-market" / "benchmark_selection.py").read_text(encoding="utf-8")
        v5 = (ROOT / "open-model-market" / "v5_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("cost_performance_optimizer.select_team", production)
        self.assertIn("from v5_value_planner import compile_and_optimize_v5", v5)
        self.assertNotIn("--quality-tolerance-pct", v5)


if __name__ == "__main__":
    unittest.main()
