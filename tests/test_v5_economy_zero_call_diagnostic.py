import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_economy_zero_call_diagnostic as diagnostic  # noqa: E402


class TestV5EconomyZeroCallDiagnostic(unittest.TestCase):
    def test_prepare_reuses_existing_economy_issue_without_paid_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = root / "event.json"
            event.write_text(
                json.dumps(
                    {
                        "issue": {
                            "number": 39,
                            "body": json.dumps(
                                {
                                    "benchmark_id": "v5-economy-live-cutover-20260730",
                                    "max_cost_usd": 1.5,
                                    "max_calls": 45,
                                    "max_strategy_cost_usd": 0.25,
                                    "output_allowance_tokens": 1800,
                                    "task_ids": list(diagnostic.DEFAULT_TASK_IDS),
                                }
                            ),
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(diagnostic.prepare(event, root / "out"), 0)
            config = json.loads(
                (root / "out" / "zero-call-diagnostic-config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(config["task_ids"], list(diagnostic.DEFAULT_TASK_IDS))
            self.assertEqual(config["model_inference_calls_allowed"], 0)
            self.assertEqual(config["paid_model_calls_allowed"], 0)
            self.assertFalse(config["production_entrypoint_changed"])
            self.assertFalse(config["v3_deleted"])

    def test_prepare_requires_exactly_three_distinct_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = root / "event.json"
            event.write_text(
                json.dumps({"issue": {"number": 39, "body": json.dumps({"task_ids": [diagnostic.DEFAULT_TASK_IDS[0]]})}}),
                encoding="utf-8",
            )
            with self.assertRaises(diagnostic.DiagnosticError):
                diagnostic.prepare(event, root / "out")

    def test_price_tier_filters_real_reliable_endpoints(self):
        market = {
            "endpoints": [
                {
                    "model_id": "model/a",
                    "provider_slug": "provider-a",
                    "prompt_price_per_million": 2.0,
                    "completion_price_per_million": 7.0,
                    "reliability": 0.95,
                    "synthetic_fixture_only": False,
                },
                {
                    "model_id": "model/b",
                    "provider_slug": "provider-b",
                    "prompt_price_per_million": 2.0,
                    "completion_price_per_million": 7.0,
                    "reliability": 0.50,
                    "synthetic_fixture_only": False,
                },
                {
                    "model_id": "model/c",
                    "provider_slug": "provider-c",
                    "prompt_price_per_million": 1.0,
                    "completion_price_per_million": 1.0,
                    "reliability": 0.99,
                    "synthetic_fixture_only": True,
                },
            ]
        }
        filtered = diagnostic._filter_market(market, diagnostic.PRICE_TIERS[0])
        self.assertEqual(filtered["endpoint_count"], 1)
        self.assertEqual(filtered["endpoints"][0]["model_id"], "model/a")

    def test_attempt_reports_feasible_plan_without_execution(self):
        optimized = {
            "selected_interpretation": "interpretation-a",
            "cost_performance_ratio": 12.3,
            "solver_status": "OPTIMAL",
            "execution_graph": {
                "nodes": [{"node_id": "n1"}, {"node_id": "n2"}],
                "execution_stages": [["n1"], ["n2"]],
                "estimated_total_cost": 0.123,
            },
        }
        with patch.object(
            diagnostic.v5_value_optimizer,
            "optimize_execution_graph",
            return_value=optimized,
        ):
            result = diagnostic._attempt({}, max_nodes=9, max_budget_usd=0.25)
        self.assertTrue(result["feasible"])
        self.assertEqual(result["selected_node_count"], 2)
        self.assertEqual(result["estimated_total_cost_usd"], 0.123)

    def test_source_contains_no_model_execution_entrypoints(self):
        source = (ROOT / "open-model-market" / "v5_economy_zero_call_diagnostic.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("CHAT_URL", "execute_v5_graph", "_direct_call", "_v3_strategy"):
            self.assertNotIn(forbidden, source)
        workflow = (ROOT / ".github" / "workflows" / "v5-economy-zero-call-diagnostic.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Paid model calls allowed", workflow)
        self.assertNotIn("v5_live_benchmark_economy.py run", workflow)


if __name__ == "__main__":
    unittest.main()
