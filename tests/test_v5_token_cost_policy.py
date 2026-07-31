import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_cost_reliability_hardening as legacy_cost  # noqa: E402
import v5_planner  # noqa: E402
import v5_production_hardening  # noqa: E402
import v5_token_cost_policy as token_cost  # noqa: E402
import v5_truncation_budget_policy as truncation  # noqa: E402
from v5_planning_runtime import PlannerPolicy  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402


def work(work_id="w1", *, machine=False):
    return {
        "work_id": work_id,
        "importance": 0.9,
        "domain_requirements": {"research": 0.9},
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
        "supported_parameters": [
            "reasoning", "temperature", "max_completion_tokens"
        ],
        "capability_scores": {"evidence_validation": 0.8},
        "benchmark_score": 0.8,
        "benchmark_confidence": 0.95,
        "reliability": 1.0,
        "synthetic_fixture_only": False,
    }


class TestV5TokenCostPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = PlannerPolicy(RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            quality_tier="value",
        ))

    def test_p95_usage_is_below_allowance_and_includes_reasoning(self):
        row = work()
        allowance = truncation.completion_envelope(row, 10000)
        usage = truncation.estimated_completion_usage(row, 10000)
        self.assertEqual(allowance, 8331)
        self.assertEqual(usage, 7620)
        self.assertLess(usage, allowance)
        self.assertGreater(usage, 2500 + 1700)

    def test_structured_output_receives_larger_usage_reserve(self):
        plain = truncation.estimated_completion_usage(work(machine=False), 10000)
        structured = truncation.estimated_completion_usage(work(machine=True), 10000)
        self.assertEqual(structured, 7878)
        self.assertGreater(structured, plain)
        self.assertLessEqual(
            structured,
            truncation.completion_envelope(work(machine=True), 10000),
        )

    def test_cost_estimate_uses_truncation_aware_p95_usage_not_allowance(self):
        row = work()
        usage = truncation.estimated_completion_usage(row, 10000)
        expected = round((1086 * 1.25 + usage * 7.50) / 1_000_000, 8)
        actual = self.policy._p95_cost(endpoint(), [row], 1.0)
        full_allowance = round(
            (1086 * 1.25 + truncation.completion_envelope(row, 10000) * 7.50)
            / 1_000_000,
            8,
        )
        self.assertEqual(actual, expected)
        self.assertLess(actual, full_allowance)

    def test_candidate_keeps_allowance_and_records_separate_usage(self):
        candidate = self.policy.candidate_factory(
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
        self.assertEqual(profile["recommended_output_allowance_tokens"], 8331)
        self.assertEqual(profile["estimated_completion_usage_tokens"], 7620)
        self.assertGreater(
            profile["recommended_output_allowance_tokens"],
            profile["estimated_completion_usage_tokens"],
        )
        self.assertFalse(profile["output_allowance_is_cost_assumption"])
        self.assertEqual(
            profile["cost_estimation_policy"],
            "reasoning-inclusive-p95-usage-not-max-allowance-r8",
        )
        self.assertEqual(
            profile["truncation_pressure_policy"],
            "reasoning-depth-contract-breadth-aware",
        )

    def test_bundle_discount_is_applied_to_usage_audit(self):
        candidate = self.policy.candidate_factory(
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
        expected = math.ceil(2 * 7620 * 0.84)
        self.assertEqual(
            candidate.parameter_profile["estimated_completion_usage_tokens"],
            expected,
        )
        self.assertEqual(
            candidate.parameter_profile["bundle_discount_applied_to_usage_estimate"],
            0.84,
        )

    def test_compatibility_install_does_not_modify_formal_planner(self):
        original_candidate = v5_planner._candidate_for
        original_cost = v5_planner._estimated_cost
        v5_production_hardening.install()
        self.assertIs(v5_planner._candidate_for, original_candidate)
        self.assertIs(v5_planner._estimated_cost, original_cost)
        self.assertIs(
            v5_production_hardening.conservative_estimated_cost,
            token_cost.p95_usage_estimated_cost,
        )
        source = (ROOT / "open-model-market" / "v5_planning_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("truncation_policy.completion_envelope", source)
        self.assertIn("truncation_policy.estimated_completion_usage", source)
        self.assertNotIn("truncation_policy.install()", source)
        self.assertIsNot(legacy_cost.completion_envelope, truncation.completion_envelope)


if __name__ == "__main__":
    unittest.main()
