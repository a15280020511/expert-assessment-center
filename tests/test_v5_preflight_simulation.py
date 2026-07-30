import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_preflight_simulation as simulation  # noqa: E402


class TestV5PreflightSimulation(unittest.TestCase):
    def test_r8h_case_stops_before_downstream_calls(self):
        report = simulation.simulate()
        row = next(x for x in report["scenarios"] if x["name"] == "r8h-parity-gap")
        self.assertFalse(row["paid_execution_allowed"])
        self.assertEqual(row["new_workflow_downstream_calls_after_failed_v5"], 0)
        self.assertEqual(row["new_workflow_wasted_downstream_cost_usd"], 0.0)

    def test_soft_provider_diversity_keeps_original_when_replacement_is_too_expensive(self):
        report = simulation.simulate()
        row = next(
            x for x in report["scenarios"]
            if x["name"] == "soft-diversity-expensive-alternative-keeps-original"
        )
        self.assertFalse(row["provider_rebalance_budget_safe"])
        self.assertTrue(row["provider_policy_pass"])
        self.assertTrue(row["runtime_preflight_pass"])

    def test_strict_provider_diversity_fails_closed(self):
        report = simulation.simulate()
        row = next(
            x for x in report["scenarios"]
            if x["name"] == "strict-diversity-no-budget-safe-alternative"
        )
        self.assertFalse(row["provider_policy_pass"])
        self.assertFalse(row["paid_execution_allowed"])


if __name__ == "__main__":
    unittest.main()
