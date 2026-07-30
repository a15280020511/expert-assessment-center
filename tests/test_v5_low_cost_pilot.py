import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_live_benchmark as base  # noqa: E402
import v5_low_cost_pilot as pilot  # noqa: E402


class TestV5LowCostPilot(unittest.TestCase):
    def test_prepare_clamps_cost_calls_and_allowance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = root / "event.json"
            event.write_text(json.dumps({
                "issue": {
                    "number": 99,
                    "body": json.dumps({
                        "pilot_id": "pilot-test",
                        "max_cost_usd": 9,
                        "max_calls": 999,
                        "max_strategy_cost_usd": 2,
                        "output_allowance_tokens": 9000,
                    }),
                }
            }), encoding="utf-8")
            self.assertEqual(pilot.prepare(event, root), 0)
            config = json.loads((root / "pilot-config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["max_cost_usd"], 0.5)
            self.assertEqual(config["max_calls"], 40)
            self.assertEqual(config["max_strategy_cost_usd"], 0.12)
            self.assertEqual(config["output_allowance_tokens"], 2500)
            self.assertFalse(config["production_cutover_eligible"])

    def test_credit_preflight_allows_unlimited_key_for_strict_pilot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "pilot-config.json"
            config.write_text(json.dumps({"max_cost_usd": 0.5}), encoding="utf-8")
            payload = {"data": {"label": "unlimited", "limit": None, "limit_remaining": None, "usage": 2.0}}
            with patch.object(pilot.hardened, "request_json", return_value=payload), patch.dict(
                os.environ,
                {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MANAGEMENT_KEY": ""},
                clear=False,
            ):
                code = pilot.credit_preflight(config, root)
            self.assertEqual(code, 0)
            report = json.loads((root / "pilot-credit-preflight.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "bounded-pilot-key-accepted")
            self.assertFalse(report["blockers"])
            self.assertEqual(report["model_inference_calls"], 0)

    def test_credit_preflight_rejects_known_low_remaining_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "pilot-config.json"
            config.write_text(json.dumps({"max_cost_usd": 0.5}), encoding="utf-8")
            payload = {"data": {"limit": 1.0, "limit_remaining": 0.2, "usage": 0.8}}
            with patch.object(pilot.hardened, "request_json", return_value=payload), patch.dict(
                os.environ,
                {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MANAGEMENT_KEY": ""},
                clear=False,
            ):
                code = pilot.credit_preflight(config, root)
            self.assertEqual(code, 3)

    def test_worst_case_cost_uses_prompt_and_completion_allowance(self):
        endpoint = {
            "prompt_price_per_million": 1.0,
            "completion_price_per_million": 4.0,
        }
        payload = {
            "messages": [{"role": "user", "content": "x" * 1000}],
            "max_tokens": 2000,
        }
        estimate = pilot._worst_case_direct_cost(endpoint, payload)
        self.assertGreater(estimate, 0.01)
        self.assertLess(estimate, 0.02)

    def test_affordable_endpoint_filter_enforces_price_and_reliability(self):
        market = {"endpoints": [
            {
                "endpoint_id": "good",
                "model_id": "a/model",
                "prompt_price_per_million": 1.0,
                "completion_price_per_million": 3.0,
                "reliability": 0.95,
                "benchmark_score": 0.8,
            },
            {
                "endpoint_id": "expensive",
                "model_id": "b/model",
                "prompt_price_per_million": 2.0,
                "completion_price_per_million": 10.0,
                "reliability": 0.99,
                "benchmark_score": 0.9,
            },
            {
                "endpoint_id": "unstable",
                "model_id": "c/model",
                "prompt_price_per_million": 0.2,
                "completion_price_per_million": 0.5,
                "reliability": 0.50,
                "benchmark_score": 0.7,
            },
        ]}
        rows = pilot._affordable_endpoints(market)
        self.assertEqual([row["endpoint_id"] for row in rows], ["good"])

    def test_pilot_gate_never_authorizes_cutover(self):
        outcomes = []
        for strategy in base.STRATEGIES:
            outcomes.append(base.StrategyOutcome(
                task_id="task",
                strategy=strategy,
                status="success",
                answer="answer" * 50,
                actual_cost_usd=0.01,
                latency_seconds=1.0,
                call_count=1,
                models=[f"model-{strategy}"],
                providers=[f"provider-{strategy}"],
                safety_failure=False,
            ))
        ledger = base.GlobalLedger(0.5, 40)
        decision = pilot._pilot_gate(outcomes, {"judge_count": 2}, ledger)
        self.assertTrue(decision["pilot_gate_passed"])
        self.assertFalse(decision["production_cutover_allowed"])


if __name__ == "__main__":
    unittest.main()
