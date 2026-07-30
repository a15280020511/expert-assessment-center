import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_live_benchmark import (  # noqa: E402
    BenchmarkLimitExceeded,
    GlobalLedger,
    _extract_json_object,
    _safe_payload,
    _select_distinct,
    prepare,
)


class TestV5LiveBenchmark(unittest.TestCase):
    @staticmethod
    def endpoints():
        rows = []
        for index in range(6):
            rows.append({
                "endpoint_id": f"e-{index}",
                "model_id": f"vendor-{index}/model-{index}",
                "provider_slug": f"provider-{index}",
                "provider_endpoint": f"vendor-{index}/model-{index}@provider-{index}",
                "context_length": 131072,
                "max_completion_tokens": 16000,
                "prompt_price_per_million": 1.0 + index,
                "completion_price_per_million": 2.0 + index,
                "supported_parameters": ["reasoning", "temperature"],
                "capability_scores": {
                    "general_analysis": 0.7 + index * 0.02,
                    "decision_comparison": 0.7,
                    "adversarial_reasoning": 0.6 + index * 0.03,
                    "risk_discovery": 0.65,
                    "synthesis": 0.72 + index * 0.02,
                    "delivery": 0.8,
                },
                "benchmark_score": 0.9 - index * 0.05,
                "reliability": 0.99 - index * 0.01,
            })
        return rows

    def test_suite_contains_five_independent_tasks(self):
        suite = json.loads((ROOT / "open-model-market" / "v5_live_benchmark_suite.json").read_text(encoding="utf-8"))
        task_ids = [row["task_id"] for row in suite["tasks"]]
        self.assertEqual(len(task_ids), 5)
        self.assertEqual(len(set(task_ids)), 5)
        self.assertTrue(all(row["rubric"]["criteria"] for row in suite["tasks"]))
        self.assertTrue(all(row["rubric"]["fatal_errors"] for row in suite["tasks"]))

    def test_prepare_accepts_only_bounded_known_configuration(self):
        event = {
            "issue": {
                "number": 42,
                "body": json.dumps({
                    "benchmark_id": "bench-20260730",
                    "max_cost_usd": 12,
                    "max_calls": 180,
                    "max_strategy_cost_usd": 3,
                }),
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            event_path = Path(temp) / "event.json"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            output = Path(temp) / "out"
            old = os.environ.pop("GITHUB_OUTPUT", None)
            try:
                self.assertEqual(prepare(event_path, output), 0)
            finally:
                if old is not None:
                    os.environ["GITHUB_OUTPUT"] = old
            config = json.loads((output / "benchmark-config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["max_cost_usd"], 12.0)
            self.assertEqual(config["max_calls"], 180)
            self.assertEqual(len(config["task_ids"]), 5)

    def test_direct_payload_is_explicit_and_tool_free(self):
        endpoint = self.endpoints()[0]
        payload = _safe_payload(endpoint, "system", "user")
        self.assertEqual(payload["provider"]["order"], [endpoint["provider_slug"]])
        self.assertEqual(payload["provider"]["only"], [endpoint["provider_slug"]])
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertNotIn("tools", payload)
        self.assertNotIn("models", payload)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("max_completion_tokens", payload)

    def test_distinct_team_selection_uses_distinct_models_and_providers(self):
        selected = _select_distinct(
            self.endpoints(),
            (("general_analysis",), ("decision_comparison",), ("adversarial_reasoning",), ("synthesis",)),
        )
        self.assertEqual(len(selected), 4)
        self.assertEqual(len({row["model_id"] for row in selected}), 4)
        self.assertEqual(len({row["provider_slug"] for row in selected}), 4)

    def test_global_ledger_fails_closed_on_call_or_cost_limit(self):
        ledger = GlobalLedger(max_cost_usd=0.02, max_calls=2)
        ledger.before_call(task_id="t", strategy="s", model="m1")
        ledger.charge(task_id="t", strategy="s", model="m1", cost_usd=0.01)
        ledger.before_call(task_id="t", strategy="s", model="m2")
        with self.assertRaises(BenchmarkLimitExceeded):
            ledger.charge(task_id="t", strategy="s", model="m2", cost_usd=0.02)

    def test_judge_json_extraction_accepts_fenced_json(self):
        value = _extract_json_object('```json\n{"scores":{"C1":{"total_score":88}}}\n```')
        self.assertIn("scores", value)


if __name__ == "__main__":
    unittest.main()
