import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_live_benchmark_r8 as stage_d
import v5_stage_d_ranking_parity as parity


class V5StageDRankingParityTests(unittest.TestCase):
    def test_stage_d_strategy_uses_configured_production_breadth(self):
        source = inspect.getsource(parity.production_parity_v5_strategy)
        self.assertIn("ranking_limit=50", source)
        self.assertIn("int(run.ranking_limit)", source)
        self.assertIn("maximum_models=candidate_model_limit", source)
        self.assertIn("ranking_limit=candidate_model_limit", source)
        self.assertNotIn("ranked[:24]", source)
        self.assertNotIn("maximum_models=24", source)
        self.assertNotIn("ranking_limit=24", source)

    def test_parity_strategy_is_installed_before_stage_d_annotation(self):
        source = inspect.getsource(stage_d.install_r8_stage_d)
        self.assertLess(source.index("ranking_parity.install()"), source.index("_annotate_v5_strategy()"))

    def test_stage_d_keeps_original_cost_and_call_safety_bounds(self):
        self.assertEqual(stage_d.MAX_STRATEGY_COST_USD, 0.25)
        self.assertEqual(stage_d.MAX_GLOBAL_COST_USD, 1.50)
        self.assertEqual(stage_d.MAX_GLOBAL_CALLS, 45)
        limits = stage_d._r8_limits(
            max_nodes=16,
            max_edges=64,
            max_stages=8,
            max_model_calls=16,
            max_retries=5,
            max_replacements=5,
            max_budget_usd=0.25,
        )
        self.assertEqual(limits.max_nodes, 9)
        self.assertEqual(limits.max_model_calls, 9)
        self.assertEqual(limits.max_retries, 1)
        self.assertEqual(limits.max_replacements, 2)


if __name__ == "__main__":
    unittest.main()
