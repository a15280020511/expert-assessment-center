import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_planner import CandidateNode  # noqa: E402
import v5_dynamic_configuration as dynamic  # noqa: E402


def candidate():
    return CandidateNode(
        candidate_id="node-1",
        interpretation_id="i1",
        coverage_keys=("w1#0",),
        assigned_work=("w1",),
        copy_indices=(0,),
        professional_capabilities={"domain:business": 0.8},
        functions=("decision_comparison",),
        prompt_profile={"profile_id": "p1", "modules": ["decision_comparison"]},
        reasoning_profile={"reasoning_enabled": True, "depth": 0.8, "effort": "high"},
        parameter_profile={"profile_id": "x1", "parameters": {}},
        model="vendor/model",
        provider_endpoint="vendor/model@provider",
        provider_slug="provider",
        output_contract={"machine_readable_required": False, "required_fields": []},
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.02,
        failure_probability=0.05,
        request_config={
            "provider": {
                "order": ["provider"],
                "only": ["provider"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
        independence_groups=(),
    )


class TestV5DynamicConfiguration(unittest.TestCase):
    def test_role_and_parameters_derive_from_work_and_endpoint_support(self):
        works = [
            {
                "importance": 0.9,
                "error_cost": 0.85,
                "domain_requirements": {"business": 0.9, "legal": 0.6},
                "operation_requirements": {"decision_comparison": 0.9},
            }
        ]
        endpoint = {
            "supported_parameters": ["reasoning", "temperature", "top_p"],
        }
        args = ("i1", ["w1#0"], works, [0], endpoint, {}, {}, [])
        with patch.object(dynamic, "_ORIGINAL_CANDIDATE_FOR", return_value=candidate()):
            result = dynamic.dynamic_candidate_for(*args)

        self.assertIn("商业与财务", result.prompt_profile["professional_role"])
        self.assertFalse(result.prompt_profile["fixed_profession_used"])
        self.assertEqual(result.request_config["reasoning"]["effort"], "high")
        self.assertIn("temperature", result.request_config)
        self.assertIn("top_p", result.request_config)
        self.assertNotIn("presence_penalty", result.request_config)
        self.assertFalse(result.parameter_profile["fixed_request_parameter_profile_used"])

    def test_unsupported_parameters_are_not_sent(self):
        works = [
            {
                "importance": 0.5,
                "error_cost": 0.4,
                "domain_requirements": {"creative": 1.0},
                "operation_requirements": {"creative_generation": 1.0},
            }
        ]
        endpoint = {"supported_parameters": []}
        args = ("i1", ["w1#0"], works, [0], endpoint, {}, {}, [])
        with patch.object(dynamic, "_ORIGINAL_CANDIDATE_FOR", return_value=candidate()):
            result = dynamic.dynamic_candidate_for(*args)
        self.assertNotIn("temperature", result.request_config)
        self.assertNotIn("top_p", result.request_config)
        self.assertNotIn("presence_penalty", result.request_config)

    def test_audit_catalog_keeps_safety_ceiling_distinct_from_dynamic_choices(self):
        rows = {row["parameter"]: row for row in dynamic.parameter_audit_catalog()}
        self.assertEqual(rows["external_tools"]["classification"], "hard_invariant")
        self.assertEqual(rows["model"]["classification"], "dynamic")
        self.assertEqual(rows["professional_role"]["classification"], "dynamic")
        self.assertEqual(rows["hard_budget"]["classification"], "governance_invariant")
        self.assertGreaterEqual(len(rows), 25)


if __name__ == "__main__":
    unittest.main()
