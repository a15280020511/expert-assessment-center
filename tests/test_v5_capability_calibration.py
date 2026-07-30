import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_capability_calibration as calibration  # noqa: E402
import v5_candidate_diversity  # noqa: E402
import v5_planner  # noqa: E402
import v5_value_optimizer  # noqa: E402
from execution_graph import GraphLimits  # noqa: E402


def _resource_bundle(required_copies: int = 2, *, different_model_required=True):
    interpretation_id = "interpretation-test"
    work = {
        "work_id": "work-1",
        "importance": 0.9,
        "dependencies": [],
        "context_requirements": {
            "required_context_tokens": 4096,
            "system_prompt_tokens": 400,
            "original_task_tokens": 600,
            "visible_upstream_tokens": 0,
            "expected_output_tokens": 900,
        },
        "prompt_requirements": {"task": 1.0, "evidence": 0.8},
        "reasoning_requirements": {"reasoning_enabled": True, "depth": 0.7},
        "operation_requirements": {"analysis": 1.0},
        "output_contract": {
            "required_fields": ["analysis", "recommendation"],
            "machine_readable_required": False,
        },
        "independence_requirements": {
            "independent_execution_preferred": bool(different_model_required),
            "different_model_required": bool(different_model_required),
            "different_provider_preferred": bool(different_model_required),
        },
    }
    return {
        "task_semantics": {
            "interpretations": [
                {
                    "interpretation_id": interpretation_id,
                    "metrics": {"interpretation_score": 0.8},
                    "atomic_work": [work],
                }
            ]
        },
        "resource_matrices": {
            "matrices": [
                {
                    "interpretation_id": interpretation_id,
                    "capability_labels": ["domain:business"],
                    "work_index": [
                        {
                            "work_id": "work-1",
                            "minimum_independent_copies": required_copies,
                        }
                    ],
                    "task_resource_matrix": [[1.0]],
                    "hard_requirement_matrix": [[1]],
                }
            ]
        },
        "atomic_work_graphs": {
            "graphs": [
                {
                    "interpretation_id": interpretation_id,
                    "execution_stages": [["work-1"]],
                    "edges": [],
                }
            ]
        },
    }


def _endpoint(
    model: str,
    provider: str,
    score: float,
    cost: float,
    *,
    benchmark_score: float = 0.8,
    benchmark_confidence: float = 0.9,
):
    return {
        "endpoint_id": f"endpoint-{model}-{provider}",
        "model_id": model,
        "provider_slug": provider,
        "provider_endpoint": f"{model}@{provider}",
        "author": model.split("/", 1)[0],
        "context_length": 131072,
        "max_completion_tokens": 8192,
        "prompt_price_per_million": cost,
        "completion_price_per_million": cost,
        "supported_parameters": ["reasoning", "temperature"],
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "capability_scores": {"domain:business": score},
        "benchmark_score": benchmark_score,
        "benchmark_confidence": benchmark_confidence,
        "reliability": 0.98,
        "synthetic_fixture_only": False,
    }


class TestV5CapabilityCalibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Formal V5 always installs calibration and diversity as one safety unit.
        v5_candidate_diversity.install()

    def test_rank_backed_adaptive_proxy_restores_required_models(self):
        resources = _resource_bundle(required_copies=2)
        market = {
            "endpoints": [
                _endpoint("vendor/model-a", "provider-a", 0.55, 1.0),
                _endpoint("vendor/model-b", "provider-b", 0.52, 1.1),
            ]
        }
        bundle = calibration.generate_calibrated_candidate_graph(resources, market)
        audit = bundle["hard_capability_calibration"]["interpretations"][
            "interpretation-test"
        ]["work_calibrations"][0]

        self.assertTrue(audit["calibration_applied"])
        self.assertEqual(
            audit["calibration_status"],
            "rank-backed-adaptive-proxy-calibrated",
        )
        self.assertEqual(audit["static_eligible_model_count"], 0)
        self.assertEqual(audit["adaptive_eligible_model_count"], 2)
        self.assertEqual(audit["adaptive_proxy_floor"], 0.52)
        self.assertEqual(audit["proxy_capability_floor"], 0.30)
        self.assertEqual(audit["required_distinct_models"], 2)
        self.assertFalse(audit["capability_scores_modified"])
        self.assertFalse(audit["task_demands_modified"])
        self.assertFalse(
            audit["catalog_description_proxy_treated_as_measured_benchmark"]
        )

        candidate_models = {row["model"] for row in bundle["candidates"]}
        self.assertEqual(candidate_models, {"vendor/model-a", "vendor/model-b"})
        optimized = v5_value_optimizer.optimize_execution_graph(
            bundle,
            limits=GraphLimits(
                max_nodes=2,
                max_edges=4,
                max_stages=2,
                max_model_calls=2,
                max_retries=0,
                max_replacements=0,
                max_budget_usd=0.30,
            ),
        )
        graph = optimized["execution_graph"]
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len({node["model"] for node in graph["nodes"]}), 2)

    def test_proxy_baseline_insufficient_remains_fail_closed(self):
        resources = _resource_bundle(required_copies=2)
        market = {
            "endpoints": [
                _endpoint("vendor/model-a", "provider-a", 0.55, 1.0),
                _endpoint("vendor/model-b", "provider-b", 0.29, 1.1),
            ]
        }
        bundle = calibration.generate_calibrated_candidate_graph(resources, market)
        audit = bundle["hard_capability_calibration"]["interpretations"][
            "interpretation-test"
        ]["work_calibrations"][0]

        self.assertFalse(audit["calibration_applied"])
        self.assertEqual(
            audit["calibration_status"], "rank-backed-proxy-still-insufficient"
        )
        self.assertEqual(audit["adaptive_eligible_model_count"], 1)
        self.assertEqual(audit["adaptive_eligible_models"], ["vendor/model-a"])
        with self.assertRaises(v5_planner.V5PlanningError):
            v5_value_optimizer.optimize_execution_graph(
                bundle,
                limits=GraphLimits(
                    max_nodes=2,
                    max_edges=4,
                    max_stages=2,
                    max_model_calls=2,
                    max_retries=0,
                    max_replacements=0,
                    max_budget_usd=0.30,
                ),
            )

    def test_low_rank_model_cannot_enter_adaptive_calibration(self):
        resources = _resource_bundle(required_copies=2)
        market = {
            "endpoints": [
                _endpoint("vendor/model-a", "provider-a", 0.55, 1.0),
                _endpoint(
                    "vendor/model-b",
                    "provider-b",
                    0.52,
                    1.1,
                    benchmark_score=0.34,
                ),
            ]
        }
        bundle = calibration.generate_calibrated_candidate_graph(resources, market)
        audit = bundle["hard_capability_calibration"]["interpretations"][
            "interpretation-test"
        ]["work_calibrations"][0]
        self.assertEqual(
            audit["calibration_status"], "rank-backed-proxy-still-insufficient"
        )
        self.assertEqual(audit["required_distinct_models"], 2)
        self.assertEqual(audit["adaptive_eligible_models"], ["vendor/model-a"])

    def test_static_threshold_remains_when_market_is_sufficient(self):
        resources = _resource_bundle(required_copies=2)
        market = {
            "endpoints": [
                _endpoint("vendor/model-a", "provider-a", 0.75, 1.0),
                _endpoint("vendor/model-b", "provider-b", 0.70, 1.1),
            ]
        }
        bundle = calibration.generate_calibrated_candidate_graph(resources, market)
        audit = bundle["hard_capability_calibration"]["interpretations"][
            "interpretation-test"
        ]["work_calibrations"][0]
        self.assertFalse(audit["calibration_applied"])
        self.assertEqual(audit["calibration_status"], "static-threshold-sufficient")
        self.assertEqual(audit["static_eligible_model_count"], 2)

    def test_ordinary_redundancy_requires_only_one_qualified_model(self):
        resources = _resource_bundle(
            required_copies=2,
            different_model_required=False,
        )
        market = {
            "endpoints": [
                _endpoint("vendor/model-a", "provider-a", 0.70, 1.0),
            ]
        }
        bundle = calibration.generate_calibrated_candidate_graph(resources, market)
        audit = bundle["hard_capability_calibration"]["interpretations"][
            "interpretation-test"
        ]["work_calibrations"][0]
        self.assertEqual(audit["required_execution_copies"], 2)
        self.assertEqual(audit["required_distinct_models"], 1)
        self.assertFalse(audit["different_model_required"])
        self.assertEqual(audit["calibration_status"], "static-threshold-sufficient")
        optimized = v5_value_optimizer.optimize_execution_graph(
            bundle,
            limits=GraphLimits(
                max_nodes=2,
                max_edges=4,
                max_stages=2,
                max_model_calls=2,
                max_retries=0,
                max_replacements=0,
                max_budget_usd=0.30,
            ),
        )
        self.assertEqual(len(optimized["execution_graph"]["nodes"]), 2)
        self.assertEqual(
            {row["model"] for row in optimized["execution_graph"]["nodes"]},
            {"vendor/model-a"},
        )

    def test_install_patches_formal_optimizer_and_preserves_diversity(self):
        v5_candidate_diversity.install()
        self.assertIs(
            v5_planner.generate_candidate_graph,
            calibration.generate_calibrated_candidate_graph,
        )
        self.assertIs(
            v5_value_optimizer.generate_candidate_graph,
            calibration.generate_calibrated_candidate_graph,
        )
        self.assertIs(
            v5_planner.pareto_prune,
            v5_candidate_diversity.diversity_preserving_pareto_prune,
        )

    def test_formal_and_live_entries_install_same_policy(self):
        pipeline = (ROOT / "open-model-market" / "v5_pipeline.py").read_text(
            encoding="utf-8"
        )
        final = (
            ROOT / "open-model-market" / "v5_live_benchmark_final.py"
        ).read_text(encoding="utf-8")
        self.assertIn("v5_candidate_diversity.install()", pipeline)
        self.assertIn("v5_candidate_diversity.install()", final)


if __name__ == "__main__":
    unittest.main()
