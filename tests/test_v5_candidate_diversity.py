import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_candidate_diversity as diversity  # noqa: E402
import v5_planner  # noqa: E402
from v5_planner import CandidateNode  # noqa: E402


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

    def test_install_replaces_runtime_pruner(self):
        original = v5_planner.pareto_prune
        try:
            diversity._INSTALLED = False
            diversity.install()
            self.assertIs(v5_planner.pareto_prune, diversity.diversity_preserving_pareto_prune)
        finally:
            v5_planner.pareto_prune = original
            diversity._INSTALLED = False

    def test_pilot_entrypoint_reaches_candidate_diversity_through_tiered_layer(self):
        entry = (ROOT / "open-model-market" / "v5_low_cost_pilot_entry.py").read_text(encoding="utf-8")
        tiered = (ROOT / "open-model-market" / "v5_low_cost_pilot_v4.py").read_text(encoding="utf-8")
        self.assertIn("import v5_low_cost_pilot_v4 as pilot_v4", entry)
        self.assertIn("v5_candidate_diversity.install()", tiered)


if __name__ == "__main__":
    unittest.main()
