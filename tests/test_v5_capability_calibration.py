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
from v5_planning_runtime import PlannerPolicy  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402


def _resource_bundle(
    required_copies: int = 2,
    *,
    different_model_required: bool = True,
):
    interpretation_id = "interpretation-test"
    work = {
        "work_id": "work-1",
        "importance": 0.9,
        "dependencies": [],
        "domain_requirements": {"business": 0.9},
        "context_requirements": {
            "required_context_tokens": 4096,
            "system_prompt_tokens": 400,
            "original_task_tokens": 600,
            "visible_upstream_tokens": 0,
            "expected_output_tokens": 900,
            "expected_reasoning_tokens": 600,
        },
        "prompt_requirements": {"task": 1.0, "evidence": 0.8},
        "reasoning_requirements": {
            "reasoning_enabled": True,
            "depth": 0.7,
        },
        "operation_requirements": {"analysis": 1.0},
        "output_contract": {
            "required_fields": ["analysis", "recommendation"],
            "machine_readable_required": False,
        },
        "independence_requirements": {
            "independent_execution_preferred": different_model_required,
            "different_model_required": different_model_required,
            "different_provider_preferred": different_model_required,
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
        "supported_parameters": [
            "reasoning",
            "temperature",
            "max_completion_tokens",
        ],
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
        cls.policy = PlannerPolicy(
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=1,
                cost_anomaly_usd=None,
                quality_tier="value",
            )
        )

    def generate(self, resources, market):
        return self.policy.generate_candidate_graph(
            resources,
            market,
            maximum_per_group=12,
        )

    @staticmethod
    def limits():
        return GraphLimits(
            max_nodes=2,
            max_edges=4,
            max_stages=2,
            max_model_calls=2,
            max_retries=0,
            max_replacements=0,
            max_budget_usd=0.30,
        )

    def test_rank_backed_global_company_calibration_restores_assignment(self):
        resources = _resource_bundle(required_copies=2)
        market = {
            "endpoints": [
                _endpoint("vendor-a/model-a", "provider-a", 0.55, 1.0),
                _endpoint("vendor-b/model-b", "provider-b", 0.52, 1.1),
            ]
        }
        bundle = self.generate(resources, market)
        audit = bundle["hard_capability_calibration"]["interpretations"][
            "interpretation-test"
        ]["work_calibrations"][0]
        self.assertTrue(audit["calibration_applied"])
        self.assertEqual(
            audit["calibration_status"],
            "rank-backed-global-company-breadth-calibrated",
        )
        self.assertEqual(audit["static_eligible_company_count"], 0)
        self.assertEqual(audit["adaptive_eligible_company_count"], 2)
        self.assertEqual(audit["adaptive_proxy_floor"], 0.52)
        self.assertEqual(audit["local_distinct_model_target"], 2)
        self.assertEqual(audit["work_candidate_company_breadth_target"], 2)
        self.assertFalse(audit["capability_scores_modified"])
        self.assertFalse(audit["task_demands_modified"])
        self.assertFalse(audit["proxy_floor_lowered"])
        optimized = self.policy.optimize_execution_graph(
            bundle,
            limits=self.limits(),
            quality_tolerance_pct=2.0,
            solver_timeout_seconds=5.0,
        )
        policy = optimized["execution_graph"]["metadata"][
            "model_company_policy"
        ]
        self.assertEqual(policy["selected_company_count"], 2)

    def test_rank_or_proxy_shortage_remains_fail_closed(self):
        resources = _resource_bundle(required_copies=2)
        market = {
            "endpoints": [
                _endpoint("vendor-a/model-a", "provider-a", 0.55, 1.0),
                _endpoint("vendor-b/model-b", "provider-b", 0.29, 1.1),
            ]
        }
        bundle = self.generate(resources, market)
        audit = bundle["hard_capability_calibration"]["interpretations"][
            "interpretation-test"
        ]["work_calibrations"][0]
        self.assertFalse(audit["calibration_applied"])
        self.assertEqual(
            audit["calibration_status"],
            "rank-backed-global-company-breadth-insufficient",
        )
        self.assertEqual(
            audit["adaptive_eligible_companies"],
            ["vendor-a"],
        )
        with self.assertRaises(v5_planner.V5PlanningError):
            self.policy.optimize_execution_graph(
                bundle,
                limits=self.limits(),
                quality_tolerance_pct=2.0,
                solver_timeout_seconds=5.0,
            )

    def test_static_threshold_is_retained_when_market_is_sufficient(self):
        resources = _resource_bundle(required_copies=2)
        market = {
            "endpoints": [
                _endpoint("vendor-a/model-a", "provider-a", 0.75, 1.0),
                _endpoint("vendor-b/model-b", "provider-b", 0.70, 1.1),
            ]
        }
        bundle = self.generate(resources, market)
        audit = bundle["hard_capability_calibration"]["interpretations"][
            "interpretation-test"
        ]["work_calibrations"][0]
        self.assertFalse(audit["calibration_applied"])
        self.assertEqual(
            audit["calibration_status"],
            "static-threshold-global-company-breadth-sufficient",
        )
        self.assertEqual(audit["static_eligible_company_count"], 2)

    def test_task_global_company_constraint_overrides_local_model_reuse(self):
        resources = _resource_bundle(
            required_copies=2,
            different_model_required=False,
        )
        market = {
            "endpoints": [
                _endpoint("vendor/model-a", "provider-a", 0.70, 1.0),
            ]
        }
        bundle = self.generate(resources, market)
        audit = bundle["hard_capability_calibration"]["interpretations"][
            "interpretation-test"
        ]["work_calibrations"][0]
        self.assertEqual(audit["required_execution_copies"], 2)
        self.assertEqual(audit["local_distinct_model_target"], 1)
        self.assertFalse(audit["different_model_required"])
        with self.assertRaisesRegex(
            v5_planner.V5PlanningError,
            "distinct-model-company hard constraint",
        ):
            self.policy.optimize_execution_graph(
                bundle,
                limits=self.limits(),
                quality_tolerance_pct=2.0,
                solver_timeout_seconds=5.0,
            )

    def test_compatibility_installers_are_no_ops(self):
        original_generator = v5_planner.generate_candidate_graph
        original_pruner = v5_planner.pareto_prune
        original_optimizer_generator = (
            v5_value_optimizer.generate_candidate_graph
        )
        calibration.install()
        v5_candidate_diversity.install()
        self.assertIs(v5_planner.generate_candidate_graph, original_generator)
        self.assertIs(v5_planner.pareto_prune, original_pruner)
        self.assertIs(
            v5_value_optimizer.generate_candidate_graph,
            original_optimizer_generator,
        )

    def test_formal_entries_use_explicit_policy_without_monkey_patching(self):
        constitutional_pipeline = (
            ROOT / "open-model-market" / "v5_constitutional_pipeline.py"
        ).read_text(encoding="utf-8")
        production = (
            ROOT / "open-model-market" / "v5_production_ticket.py"
        ).read_text(encoding="utf-8")
        recovery = (
            ROOT / "open-model-market" / "v5_recovery_runtime.py"
        ).read_text(encoding="utf-8")
        planner_source = (
            ROOT / "open-model-market" / "v5_planning_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "runtime.planner_policy.generate_candidate_graph",
            constitutional_pipeline,
        )
        self.assertIn(
            "import v5_constitutional_pipeline as v5_pipeline",
            production,
        )
        self.assertIn("RuntimeConfig(", production)
        self.assertIn("build_production_runtime(", production)
        self.assertIn("return build_runtime(", recovery)
        self.assertIn(
            "planner_policy=CrossEndpointPlannerPolicy(config)",
            recovery,
        )
        self.assertIn(
            "generate_calibrated_candidate_graph",
            planner_source,
        )
        self.assertIn(
            "diversity_preserving_pareto_prune",
            planner_source,
        )
        self.assertNotIn(".install()", constitutional_pipeline)
        self.assertNotIn("v5_production_hardening.install()", production)
        self.assertNotIn(".install()", recovery)
        native_runtime = (
            ROOT / "open-model-market" / "v5_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import v5_executor", native_runtime)
        self.assertNotIn("legacy_executor", native_runtime)


if __name__ == "__main__":
    unittest.main()
