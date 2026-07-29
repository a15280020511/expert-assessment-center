import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import runtime_guards  # noqa: E402


class RuntimeGuardTests(unittest.TestCase):
    @staticmethod
    def run_config(output_dir, legacy_limit=0.01):
        return model_market.RunConfig(
            task="test",
            output_dir=Path(output_dir),
            api_key="x",
            quality_tier="value",
            ranking_limit=50,
            minimum_context_length=16384,
            candidate_pool_per_seat=3,
            catalog_sorts=["intelligence-high-to-low", "pricing-low-to-high"],
            weights={"quality": 0.35, "popularity": 0.0, "cost": 0.45, "speed": 0.0, "fit": 0.15, "context": 0.05},
            soft_price_cap=15.0,
            catalog_file=None,
            max_estimated_cost_usd=legacy_limit,
            budget_safety_factor=1.25,
            history_weight=0.0,
            history_path=Path(output_dir) / "history.json",
            max_completion_tokens=5000,
            judge_max_completion_tokens=6000,
            reasoning_effort="high",
            temperature=0.2,
            catalog_timeout_seconds=30,
            catalog_max_retries=1,
            model_timeout_seconds=240,
            model_max_retries=0,
            maximum_replacements=0,
            parallel_workers=3,
            judge_context_budget_chars=120000,
            require_all_experts=True,
            provider={},
            dry_run=False,
            require_live_catalog=False,
        )

    @staticmethod
    def response(cost):
        return {
            "id": "judge-1",
            "model": "judge/model",
            "choices": [{"finish_reason": "stop", "message": {"content": "complete"}}],
            "usage": {"cost": cost, "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def test_judge_output_contract_has_no_character_or_token_bound(self):
        payload = {"messages": [{"role": "system", "content": "judge"}, {"role": "user", "content": "task"}]}
        returned = runtime_guards.apply_judge_output_contract(payload, 4200)
        self.assertIs(returned, payload)
        text = payload["messages"][0]["content"]
        self.assertNotIn("4200个中文字符以内", text)
        self.assertIn("不得设置固定字符或Token上限", text)
        self.assertIn("不要复述题目", text)

    def test_legacy_limit_is_ignored_and_all_costs_are_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            run = self.run_config(temp, 0.01)
            results = [
                SimpleNamespace(seat_key="core", usage={"cost": 0.1}, attempts=[]),
                SimpleNamespace(seat_key="cross", usage={"cost": 0.2}, attempts=[]),
            ]
            actual = runtime_guards.enforce_post_judge_actual_budget(run, results, self.response(0.3))
            self.assertAlmostEqual(actual, 0.3)
            self.assertFalse((Path(temp) / "actual-cost-breach.json").exists())
            evidence = json.loads((Path(temp) / "cost-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["policy"], "no-hard-monetary-ceiling")
            self.assertIsNone(evidence["hard_cost_limit_usd"])

    def test_actual_team_cost_remains_available_as_diagnostic_helper(self):
        results = [SimpleNamespace(usage={"cost": 0.1}), SimpleNamespace(usage={"total_cost": 0.2})]
        self.assertAlmostEqual(runtime_guards.actual_team_cost(results, self.response(0.3)), 0.6)


if __name__ == "__main__":
    unittest.main()
