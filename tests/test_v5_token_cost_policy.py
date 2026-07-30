import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_cost_reliability_hardening as legacy_cost  # noqa: E402
import v5_dynamic_configuration as dynamic_configuration  # noqa: E402
import v5_planner  # noqa: E402
import v5_production_hardening  # noqa: E402
import v5_token_cost_policy as token_cost  # noqa: E402


def work(work_id="w1", *, machine=False):
    return {
        "work_id": work_id,
        "importance": 0.9,
        "context_requirements": {
            "system_prompt_tokens": 830,
            "original_task_tokens": 256,
            "visible_upstream_tokens": 0,
            "required_context_tokens": 6078,
            "expected_output_tokens": 2500,
            "expected_reasoning_tokens": 1700,
        },
        "prompt_requirements": {"evidence": 0.9},
        "reasoning_requirements": {
            "reasoning_enabled": True,
            "depth": 0.86,
        },
        "operation_requirements": {"evidence_validation": 1.0},
        "output_contract": {
            "required_fields": ["verified_claims", "uncertainties"],
            "machine_readable_required": machine,
        },
    }


def endpoint():
    return {
        "endpoint_id": "endpoint-a",
        "model_id": "vendor/model-a",
        "provider_slug": "provider-a",
        "provider_endpoint": "vendor/model-a@provider-a",
        "author": "vendor",
        "context_length": 131072,
        "max_completion_tokens": 10000,
        "prompt_price_per_million": 1.25,
        "completion_price_per_million": 7.50,
        "supported_parameters": ["reasoning", "temperature"],
        "capability_scores": {"evidence_validation": 0.8},
        "benchmark_score": 0.8,
        "benchmark_confidence": 0.95,
        "reliability": 1.0,
        "synthetic_fixture_only": False,
    }


class TestV5TokenCostPolicy(unittest.TestCase):
    def test_p95_usage_is_below_allowance_and_includes_reasoning(self):
        row = work()
        allowance = legacy_cost.completion_envelope(row, 10000)
        usage = token_cost.estimated_completion_usage(row, 10000)
        self.assertEqual(allowance, 6290)
        self.assertEqual(usage, math.ceil((2500 + 1700) * 1.18))
        self.assertLess(usage, allowance)
        self.assertGreater(usage, 2500 + 1700)

    def test_structured_output_receives_larger_usage_reserve(self):
        plain = token_cost.estimated_completion_usage(work(machine=False), 10000)
        structured = token_cost.estimated_completion_usage(work(machine=True), 10000)
        self.assertEqual(structured, math.ceil((2500 + 1700) * 1.22))
        self.assertGreater(structured, plain)
        self.assertLessEqual(
            structured,
            legacy_cost.completion_envelope(work(machine=True), 10000),
        )

    def test_cost_estimate_uses_p95_usage_not_max_allowance(self):
        row = work()
        usage = token_cost.estimated_completion_usage(row, 10000)
        expected = round((1086 * 1.25 + usage * 7.50) / 1_000_000, 8)
        actual = token_cost.p95_usage_estimated_cost(endpoint(), [row])
        old = legacy_cost.conservative_estimated_cost(endpoint(), [row])
        self.assertEqual(actual, expected)
        self.assertLess(actual, old)

    def test_candidate_keeps_allowance_and_records_separate_usage(self):
        token_cost.install()
        candidate = v5_planner._candidate_for(
            "interpretation-a",
            ["w1#0"],
            [work()],
            [0],
            endpoint(),
            {"w1": {"evidence_validation": 0.8}},
            {"w1": set()},
            ["w1"],
        )
        self.assertIsNotNone(candidate)
        profile = candidate.parameter_profile
        self.assertEqual(profile["recommended_output_allowance_tokens"], 6290)
        self.assertEqual(
            profile["estimated_completion_usage_tokens"],
            math.ceil((2500 + 1700) * 1.18),
        )
        self.assertGreater(
            profile["recommended_output_allowance_tokens"],
            profile["estimated_completion_usage_tokens"],
        )
        self.assertFalse(profile["output_allowance_is_cost_assumption"])
        self.assertEqual(
            profile["cost_estimation_policy"],
            "reasoning-inclusive-p95-usage-not-max-allowance-r8",
        )

    def test_bundle_discount_is_applied_to_usage_audit(self):
        token_cost.install()
        candidate = v5_planner._candidate_for(
            "interpretation-a",
            ["w1#0", "w2#0"],
            [work("w1"), work("w2")],
            [0, 0],
            endpoint(),
            {
                "w1": {"evidence_validation": 0.8},
                "w2": {"evidence_validation": 0.8},
            },
            {"w1": set(), "w2": set()},
            [],
            bundle_discount=0.84,
        )
        expected = math.ceil(
            2 * math.ceil((2500 + 1700) * 1.18) * 0.84
        )
        self.assertEqual(
            candidate.parameter_profile["estimated_completion_usage_tokens"],
            expected,
        )
        self.assertEqual(
            candidate.parameter_profile[
                "bundle_discount_applied_to_usage_estimate"
            ],
            0.84,
        )

    def test_production_hardening_installs_usage_policy_then_dynamic_layer(self):
        v5_production_hardening.install()
        self.assertIs(
            v5_planner._estimated_cost,
            token_cost.p95_usage_estimated_cost,
        )
        self.assertIs(
            v5_planner._candidate_for,
            dynamic_configuration.dynamic_candidate_for,
        )
        self.assertIs(
            dynamic_configuration._ORIGINAL_CANDIDATE_FOR,
            token_cost.usage_audited_candidate_for,
        )
        self.assertIs(
            v5_production_hardening.conservative_estimated_cost,
            token_cost.p95_usage_estimated_cost,
        )


if __name__ == "__main__":
    unittest.main()
