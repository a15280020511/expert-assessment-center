import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_low_cost_pilot as pilot  # noqa: E402
import v5_low_cost_pilot_v4 as tiered  # noqa: E402
import v5_planner  # noqa: E402


class TestV5LowCostPilotV4(unittest.TestCase):
    def setUp(self):
        self.original_prompt = pilot.MAX_PROMPT_PPM
        self.original_completion = pilot.MAX_COMPLETION_PPM

    def tearDown(self):
        pilot.MAX_PROMPT_PPM = self.original_prompt
        pilot.MAX_COMPLETION_PPM = self.original_completion

    @staticmethod
    def market():
        return {
            "endpoints": [
                {
                    "model_id": "a/model",
                    "provider_slug": "p1",
                    "provider_endpoint": "a/model@p1",
                    "prompt_price_per_million": 1.0,
                    "completion_price_per_million": 3.0,
                    "reliability": 0.95,
                    "synthetic_fixture_only": False,
                },
                {
                    "model_id": "b/model",
                    "provider_slug": "p2",
                    "provider_endpoint": "b/model@p2",
                    "prompt_price_per_million": 2.5,
                    "completion_price_per_million": 8.0,
                    "reliability": 0.96,
                    "synthetic_fixture_only": False,
                },
                {
                    "model_id": "c/model",
                    "provider_slug": "p3",
                    "provider_endpoint": "c/model@p3",
                    "prompt_price_per_million": 4.5,
                    "completion_price_per_million": 14.0,
                    "reliability": 0.97,
                    "synthetic_fixture_only": False,
                },
                {
                    "model_id": "d/model",
                    "provider_slug": "p4",
                    "provider_endpoint": "d/model@p4",
                    "prompt_price_per_million": 0.2,
                    "completion_price_per_million": 0.5,
                    "reliability": 0.70,
                    "synthetic_fixture_only": False,
                },
            ],
            "rejected": [],
        }

    def test_price_tiers_expand_monotonically_and_remain_bounded(self):
        self.assertEqual([row["name"] for row in tiered.PRICE_TIERS], [
            "strict-low-cost", "expanded-value", "bounded-capability"
        ])
        prompts = [float(row["prompt"]) for row in tiered.PRICE_TIERS]
        completions = [float(row["completion"]) for row in tiered.PRICE_TIERS]
        self.assertEqual(prompts, sorted(prompts))
        self.assertEqual(completions, sorted(completions))
        self.assertLessEqual(prompts[-1], 5.0)
        self.assertLessEqual(completions[-1], 15.0)

    def test_strict_tier_filters_real_provider_rows(self):
        pilot.MAX_PROMPT_PPM = 1.5
        pilot.MAX_COMPLETION_PPM = 4.0
        result = tiered.filter_market_for_active_tier(self.market())
        self.assertEqual([row["model_id"] for row in result["endpoints"]], ["a/model"])
        self.assertEqual(result["endpoint_count"], 1)
        self.assertEqual(result["real_endpoint_count"], 1)
        self.assertEqual(result["pilot_active_price_tier"]["minimum_reliability"], 0.80)

    def test_expanded_and_capability_tiers_admit_additional_models(self):
        pilot.MAX_PROMPT_PPM = 3.0
        pilot.MAX_COMPLETION_PPM = 10.0
        expanded = tiered.filter_market_for_active_tier(self.market())
        self.assertEqual({row["model_id"] for row in expanded["endpoints"]}, {"a/model", "b/model"})
        pilot.MAX_PROMPT_PPM = 5.0
        pilot.MAX_COMPLETION_PPM = 15.0
        capability = tiered.filter_market_for_active_tier(self.market())
        self.assertEqual(
            {row["model_id"] for row in capability["endpoints"]},
            {"a/model", "b/model", "c/model"},
        )
        self.assertNotIn("d/model", {row["model_id"] for row in capability["endpoints"]})

    def test_empty_active_tier_fails_closed(self):
        pilot.MAX_PROMPT_PPM = 0.01
        pilot.MAX_COMPLETION_PPM = 0.01
        with self.assertRaises(v5_planner.V5PlanningError):
            tiered.filter_market_for_active_tier(self.market())

    def test_annotation_never_authorizes_production_cutover(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "v5-low-cost-pilot-result.json").write_text(
                json.dumps({"status": "technical_failure"}), encoding="utf-8"
            )
            (root / "v5-low-cost-pilot-summary.md").write_text("# Pilot\n", encoding="utf-8")
            tiered._TIER_HISTORY.clear()
            tiered._TIER_HISTORY.extend([
                {
                    "tier_name": "strict-low-cost",
                    "prompt_usd_per_million": 1.5,
                    "completion_usd_per_million": 4.0,
                    "status": "planning-infeasible",
                    "model_calls": 0,
                },
                {
                    "tier_name": "expanded-value",
                    "prompt_usd_per_million": 3.0,
                    "completion_usd_per_million": 10.0,
                    "status": "planning-feasible",
                    "model_calls": 0,
                },
            ])
            tiered._annotate(root)
            result = json.loads(
                (root / "v5-low-cost-pilot-result.json").read_text(encoding="utf-8")
            )
            policy = result["candidate_market_policy"]
            self.assertFalse(policy["production_cutover_allowed"])
            self.assertFalse(policy["capability_thresholds_relaxed"])
            self.assertFalse(policy["independence_constraints_relaxed"])
            self.assertFalse(policy["quality_requirements_relaxed"])
            self.assertTrue(policy["expansion_occurs_before_model_calls"])
            self.assertTrue(policy["endpoint_tier_enforced_on_real_provider_rows"])
            summary = (root / "v5-low-cost-pilot-summary.md").read_text(encoding="utf-8")
            self.assertIn("calls before expansion `0`", summary)
            self.assertIn("Production cutover allowed: `false`", summary)


if __name__ == "__main__":
    unittest.main()
