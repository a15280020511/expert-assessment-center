import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_candidate_diversity as diversity  # noqa: E402
import v5_planner  # noqa: E402
from v5_planner import CandidateNode  # noqa: E402
from v5_planning_runtime import PlannerPolicy  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402


class TestV5CandidateDiversity(unittest.TestCase):
    @staticmethod
    def candidate(model: str, quality: float, cost: float, failure: float) -> CandidateNode:
        return CandidateNode(
            candidate_id=f"node-{model}",
            interpretation_id="interpretation-test",
            coverage_keys=("work-1#0",),
            assigned_work=("work-1",),
            copy_indices=(0,),
            professional_capabilities={"analysis": quality},
            functions=("analysis",),
            prompt_profile={"profile_id": "prompt-test", "modules": []},
            reasoning_profile={"reasoning_enabled": True, "effort": "low"},
            parameter_profile={"profile_id": "params-test", "parameters": {}},
            model=model,
            provider_endpoint=f"{model}@provider",
            provider_slug="provider",
            output_contract={"required_fields": []},
            estimated_quality=quality,
            quality_uncertainty=0.1,
            estimated_cost=cost,
            failure_probability=failure,
            request_config={"provider": {"only": ["provider"]}},
            independence_groups=("work-1",),
        )

    def test_dominated_models_are_preserved_for_independent_copies(self):
        rows = [
            self.candidate("model-a", 0.95, 0.01, 0.01),
            self.candidate("model-b", 0.85, 0.02, 0.02),
            self.candidate("model-c", 0.75, 0.03, 0.03),
        ]
        self.assertTrue(v5_planner._dominates(rows[0], rows[1]))
        self.assertTrue(v5_planner._dominates(rows[0], rows[2]))
        kept = diversity.diversity_preserving_pareto_prune(rows, maximum_per_group=3)
        self.assertEqual({row.model for row in kept}, {"model-a", "model-b", "model-c"})

    def test_group_limit_keeps_distinct_models_before_duplicate_endpoints(self):
        rows = [
            self.candidate("model-a", 0.95, 0.01, 0.01),
            self.candidate("model-b", 0.85, 0.02, 0.02),
            self.candidate("model-c", 0.75, 0.03, 0.03),
        ]
        kept = diversity.diversity_preserving_pareto_prune(rows, maximum_per_group=2)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len({row.model for row in kept}), 2)

    def test_compatibility_install_is_no_op(self):
        original = v5_planner.pareto_prune
        diversity.install()
        self.assertIs(v5_planner.pareto_prune, original)

    def test_formal_runtime_composes_diversity_explicitly(self):
        policy = PlannerPolicy(RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            quality_tier="value",
        ))
        self.assertIsNotNone(policy)
        source = (ROOT / "open-model-market" / "v5_planning_runtime.py").read_text(encoding="utf-8")
        pipeline = (ROOT / "open-model-market" / "v5_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("diversity_preserving_pareto_prune", source)
        self.assertIn("runtime.planner_policy.generate_candidate_graph", pipeline)
        self.assertNotIn("v5_candidate_diversity.install()", pipeline)


if __name__ == "__main__":
    unittest.main()
