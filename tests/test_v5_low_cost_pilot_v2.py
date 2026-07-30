import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_low_cost_pilot_v2 as pilot_v2  # noqa: E402


class TestV5LowCostPilotV2(unittest.TestCase):
    def test_budget_plan_reserves_thirty_percent_for_other_strategies(self):
        plan = pilot_v2.budget_plan(0.50, 0.12)
        self.assertEqual(plan["initial_v5_planning_cap_usd"], 0.30)
        self.assertEqual(plan["maximum_v5_planning_cap_usd"], 0.35)
        self.assertEqual(plan["minimum_reserved_for_other_strategies_usd"], 0.15)
        self.assertEqual(plan["generic_strategy_cap_usd"], 0.12)

    def test_budget_plan_never_exceeds_total_ceiling(self):
        plan = pilot_v2.budget_plan(0.20, 0.50)
        self.assertEqual(plan["initial_v5_planning_cap_usd"], 0.20)
        self.assertEqual(plan["maximum_v5_planning_cap_usd"], 0.20)
        self.assertEqual(plan["minimum_reserved_for_other_strategies_usd"], 0.0)

    def test_annotation_keeps_production_cutover_forbidden(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = {
                "status": "technical_failure",
                "production_cutover_allowed": False,
                "production_entrypoint_changed": False,
            }
            (root / "v5-low-cost-pilot-result.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            (root / "v5-low-cost-pilot-summary.md").write_text(
                "# Pilot\n", encoding="utf-8"
            )
            pilot_v2._PLANNING_DIAGNOSTIC.clear()
            pilot_v2._PLANNING_DIAGNOSTIC.update({
                "budget_allocation": pilot_v2.budget_plan(0.5, 0.12),
                "structural_feasibility": "feasible-without-budget",
                "quality_band_minimum_estimated_cost_usd": 0.31,
                "adaptive_retry_attempted": True,
                "final_v5_planning_cap_usd": 0.32,
            })
            pilot_v2._annotate(root)
            updated = json.loads(
                (root / "v5-low-cost-pilot-result.json").read_text(encoding="utf-8")
            )
            self.assertFalse(updated["v5_budget_policy"]["production_cutover_allowed"])
            self.assertFalse(updated["v5_budget_policy"]["independence_constraints_relaxed"])
            self.assertFalse(updated["v5_budget_policy"]["quality_requirements_relaxed"])
            summary = (root / "v5-low-cost-pilot-summary.md").read_text(encoding="utf-8")
            self.assertIn("Reserved for other strategies: `$0.150000`", summary)
            self.assertIn("Production cutover allowed: `false`", summary)

    def test_entrypoint_uses_dynamic_budget_and_diversity_layers(self):
        entry = (ROOT / "open-model-market" / "v5_low_cost_pilot_entry.py").read_text(encoding="utf-8")
        layer = (ROOT / "open-model-market" / "v5_low_cost_pilot_v3.py").read_text(encoding="utf-8")
        self.assertIn("import v5_low_cost_pilot_v3 as pilot_v3", entry)
        self.assertIn("return pilot_v3.run(config, suite, output)", entry)
        self.assertIn("import v5_low_cost_pilot_v2", layer)
        self.assertIn("return v5_low_cost_pilot_v2.run", layer)


if __name__ == "__main__":
    unittest.main()
