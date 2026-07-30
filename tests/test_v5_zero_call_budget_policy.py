import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_economy_zero_call_diagnostic as base  # noqa: E402
import v5_zero_call_budget_policy as policy  # noqa: E402


class TestV5ZeroCallBudgetPolicy(unittest.TestCase):
    def test_prepare_binds_readiness_to_requested_strategy_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = root / "event.json"
            event.write_text(
                json.dumps(
                    {
                        "issue": {
                            "number": 73,
                            "body": json.dumps(
                                {
                                    "diagnostic_id": "r8-stage-d-test",
                                    "max_strategy_cost_usd": 0.25,
                                    "task_ids": list(base.DEFAULT_TASK_IDS),
                                }
                            ),
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(policy.prepare(event, root / "out"), 0)
            config = json.loads(
                (root / "out" / "zero-call-diagnostic-config.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(config["requested_v5_budget_ceiling_usd"], 0.25)
            self.assertIn(0.25, config["budget_grid_usd"])
            self.assertIn(0.2525, config["budget_grid_usd"])
            self.assertIn(0.255, config["budget_grid_usd"])
            self.assertEqual(config["model_inference_calls_allowed"], 0)
            self.assertEqual(config["paid_model_calls_allowed"], 0)

    def test_requested_cap_above_diagnostic_max_is_rejected(self):
        config = {
            "context_from_paid_benchmark": {"max_strategy_cost_usd": 0.31}
        }
        with self.assertRaises(base.DiagnosticError):
            policy._requested_budget(config)

    def test_missing_request_cap_uses_bounded_fallback(self):
        self.assertEqual(policy._requested_budget({}), 0.30)

    def test_audited_attempt_persists_selected_plan_and_budget_slack(self):
        optimized = {
            "selected_interpretation": "interpretation-a",
            "selected_candidate_ids": ["candidate-a", "candidate-b"],
            "cost_performance_ratio": 2.4,
            "solver_status": "OPTIMAL",
            "execution_graph": {
                "nodes": [
                    {
                        "node_id": "n1",
                        "assigned_work": ["w1"],
                        "copy_indices": [0],
                        "model": "vendor/model-a",
                        "provider_endpoint": "vendor/model-a@provider-a",
                        "estimated_cost": 0.10,
                        "failure_probability": 0.04,
                        "functions": ["analysis"],
                        "reasoning_profile": {"effort": "medium", "depth": 0.64},
                        "parameter_profile": {
                            "recommended_output_allowance_tokens": 2400,
                            "max_tokens": 10000,
                            "cost_estimation_policy": "reasoning-inclusive-p95-envelope-r8",
                        },
                    },
                    {
                        "node_id": "n2",
                        "assigned_work": ["w2"],
                        "copy_indices": [0],
                        "model": "vendor/model-b",
                        "provider_endpoint": "vendor/model-b@provider-b",
                        "estimated_cost": 0.13,
                        "failure_probability": 0.05,
                        "functions": ["synthesis"],
                        "reasoning_profile": {"effort": "high", "depth": 0.82},
                        "parameter_profile": {
                            "recommended_output_allowance_tokens": 4096,
                            "max_tokens": 10000,
                            "cost_estimation_policy": "reasoning-inclusive-p95-envelope-r8",
                        },
                    },
                ],
                "execution_stages": [["n1"], ["n2"]],
                "estimated_total_cost": 0.23,
                "metadata": {
                    "independence_policy": {
                        "hard_model_diversity_scope": "explicit-independence-groups-only"
                    }
                },
            },
        }
        with patch.object(
            policy.v5_value_optimizer,
            "optimize_execution_graph",
            return_value=optimized,
        ):
            result = policy.audited_attempt(
                {}, max_nodes=9, max_budget_usd=0.25
            )
        self.assertTrue(result["feasible"])
        self.assertEqual(result["selected_candidate_ids"], ["candidate-a", "candidate-b"])
        self.assertEqual(result["selected_node_count"], 2)
        self.assertEqual(result["selected_model_count"], 2)
        self.assertEqual(result["selected_provider_endpoint_count"], 2)
        self.assertEqual(result["estimated_total_cost_usd"], 0.23)
        self.assertEqual(result["selected_node_cost_sum_usd"], 0.23)
        self.assertEqual(result["budget_slack_usd"], 0.02)
        self.assertEqual(
            result["selected_nodes"][1]["recommended_output_allowance_tokens"],
            4096,
        )

    def test_wrapper_contains_no_model_execution_entrypoint(self):
        source = (
            ROOT / "open-model-market" / "v5_zero_call_budget_policy.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("CHAT_URL", "execute_v5_graph", "_direct_call", "_v3_strategy"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
