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
import v5_live_benchmark_economy as economy  # noqa: E402
import v5_live_benchmark_economy_verified as verified  # noqa: E402
import v5_live_benchmark_final as final  # noqa: E402
import v5_value_optimizer  # noqa: E402


class TestFinalBenchmarkAlignment(unittest.TestCase):
    def test_final_alignment_uses_active_cost_performance_v5(self):
        original = base.compile_and_optimize_v5
        try:
            final.install_final_alignment()
            self.assertIs(
                base.compile_and_optimize_v5,
                v5_value_optimizer.compile_and_optimize_v5,
            )
        finally:
            base.compile_and_optimize_v5 = original

    def test_legacy_full_preflight_still_requires_verified_account_reserve(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "benchmark-config.json"
            config.write_text(
                json.dumps({"max_cost_usd": 20.0, "max_calls": 200}),
                encoding="utf-8",
            )
            payload = {
                "data": {
                    "label": "unbounded-key",
                    "limit": None,
                    "limit_remaining": None,
                    "usage": 1.0,
                    "is_free_tier": False,
                    "is_management_key": False,
                }
            }
            with patch.object(final.hardened, "request_json", return_value=payload), patch.dict(
                os.environ,
                {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MANAGEMENT_KEY": ""},
                clear=False,
            ):
                code = final.credit_preflight(config, root)
            report = json.loads((root / "credit-preflight.json").read_text(encoding="utf-8"))
            self.assertEqual(code, 3)
            self.assertIn("verified-account-credit-reserve-required", report["blockers"])
            self.assertFalse(report["production_entrypoint_changed"])
            self.assertFalse(report["v3_deleted"])


class TestEconomyProgressiveBenchmark(unittest.TestCase):
    def test_prepare_defaults_to_three_tasks_and_verified_hard_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = root / "event.json"
            event.write_text(json.dumps({"issue": {"number": 39, "body": ""}}), encoding="utf-8")
            with patch.object(
                economy,
                "DEFAULT_MAX_CALLS",
                verified.VERIFIED_DEFAULT_MAX_CALLS,
            ):
                code = economy.prepare(event, root / "out")
            config = json.loads((root / "out" / "benchmark-config.json").read_text(encoding="utf-8"))
            self.assertEqual(code, 0)
            self.assertEqual(config["mode"], "economy-cutover")
            self.assertEqual(config["task_ids"], list(economy.DEFAULT_TASK_IDS))
            self.assertEqual(config["strategies"], ["v5_joint_graph", "v3"])
            self.assertEqual(config["max_cost_usd"], 1.5)
            self.assertLessEqual(config["max_cost_usd"], economy.HARD_MAX_COST_USD)
            self.assertEqual(config["max_calls"], 46)
            self.assertEqual(config["output_allowance_tokens"], 1800)
            self.assertFalse(config["production_entrypoint_changed"])
            self.assertFalse(config["v3_deleted"])

    def test_verified_graph_limits_match_zero_call_evidence(self):
        limits = verified.verified_graph_limits(
            max_nodes=16,
            max_edges=64,
            max_stages=8,
            max_model_calls=16,
            max_retries=2,
            max_replacements=2,
            max_budget_usd=0.25,
        )
        self.assertEqual(limits.max_nodes, 10)
        self.assertEqual(limits.max_model_calls, 10)
        self.assertEqual(limits.max_retries, 0)
        self.assertEqual(limits.max_replacements, 0)
        self.assertEqual(limits.max_budget_usd, 0.25)

    def test_verified_price_caps_match_zero_call_feasible_tier(self):
        self.assertEqual(verified.VERIFIED_MAX_PROMPT_PPM, 5.0)
        self.assertEqual(verified.VERIFIED_MAX_COMPLETION_PPM, 15.0)
        self.assertTrue(
            verified._within_verified_price_cap(
                {
                    "prompt_price_per_million": 5.0,
                    "completion_price_per_million": 15.0,
                    "reliability": 0.80,
                }
            )
        )
        self.assertFalse(
            verified._within_verified_price_cap(
                {
                    "prompt_price_per_million": 5.01,
                    "completion_price_per_million": 15.0,
                    "reliability": 0.99,
                }
            )
        )

    def test_verified_market_filters_provider_rows_not_whole_model(self):
        raw_market = {
            "endpoints": [
                {
                    "endpoint_id": "cheap-endpoint",
                    "model_id": "vendor/model-a",
                    "provider_slug": "provider-cheap",
                    "prompt_price_per_million": 2.0,
                    "completion_price_per_million": 10.0,
                    "reliability": 0.95,
                    "synthetic_fixture_only": False,
                },
                {
                    "endpoint_id": "expensive-endpoint",
                    "model_id": "vendor/model-a",
                    "provider_slug": "provider-expensive",
                    "prompt_price_per_million": 5.0,
                    "completion_price_per_million": 25.0,
                    "reliability": 0.99,
                    "synthetic_fixture_only": False,
                },
                {
                    "endpoint_id": "unreliable-endpoint",
                    "model_id": "vendor/model-b",
                    "provider_slug": "provider-b",
                    "prompt_price_per_million": 1.0,
                    "completion_price_per_million": 2.0,
                    "reliability": 0.50,
                    "synthetic_fixture_only": False,
                },
            ],
            "rejected": [],
        }
        filtered = verified.filter_verified_endpoint_market(raw_market)
        self.assertEqual(filtered["endpoint_count"], 1)
        self.assertEqual(filtered["endpoints"][0]["endpoint_id"], "cheap-endpoint")
        self.assertEqual(
            filtered["verified_economy_market_policy"]["scope"],
            "concrete-provider-endpoint-not-model-catalog-aggregate",
        )
        rejected = {
            row["endpoint_id"]: row["reason"] for row in filtered["rejected"]
        }
        self.assertEqual(
            rejected["expensive-endpoint"],
            "outside-verified-economy-provider-endpoint-cap",
        )
        self.assertEqual(
            rejected["unreliable-endpoint"],
            "outside-verified-economy-provider-endpoint-cap",
        )

    def test_verified_alignment_restores_ranker_and_patches_endpoint_compiler(self):
        source = (
            ROOT
            / "open-model-market"
            / "v5_live_benchmark_economy_verified.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "endpoint_agnostic_rank = economy.base._rank_v5_models",
            source,
        )
        self.assertIn(
            "economy.base._rank_v5_models = endpoint_agnostic_rank",
            source,
        )
        self.assertIn(
            "v5_value_optimizer.compile_model_endpoint_market =",
            source,
        )

    def test_prepare_rejects_more_than_three_tasks(self):
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
                                    "task_ids": [
                                        "municipal-investment-portfolio",
                                        "retail-expansion-unit-economics",
                                        "software-job-runner-security",
                                        "dual-source-supply-chain",
                                    ]
                                }
                            ),
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(base.LiveBenchmarkError):
                economy.prepare(event, root / "out")

    def test_unbounded_key_is_accepted_under_two_dollar_hard_ceiling(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "benchmark-config.json"
            config.write_text(
                json.dumps({"max_cost_usd": 1.5, "max_calls": 46, "output_allowance_tokens": 1800}),
                encoding="utf-8",
            )
            key_payload = {
                "data": {
                    "label": "unbounded-key",
                    "limit": None,
                    "limit_remaining": None,
                    "usage": 1.0,
                }
            }
            with patch.object(economy.hardened, "request_json", return_value=key_payload), patch.dict(
                os.environ,
                {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MANAGEMENT_KEY": ""},
                clear=False,
            ):
                code = economy.credit_preflight(config, root)
            report = json.loads((root / "credit-preflight.json").read_text(encoding="utf-8"))
            self.assertEqual(code, 0)
            self.assertEqual(report["status"], "bounded-key-accepted")
            self.assertEqual(report["hard_reserve_ceiling_usd"], 2.0)
            self.assertFalse(report["blockers"])

    def test_known_low_finite_limit_is_rejected_before_model_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "benchmark-config.json"
            config.write_text(
                json.dumps({"max_cost_usd": 1.5, "max_calls": 46, "output_allowance_tokens": 1800}),
                encoding="utf-8",
            )
            key_payload = {
                "data": {
                    "label": "bounded-key",
                    "limit": 2.0,
                    "limit_remaining": 0.25,
                    "usage": 1.75,
                }
            }
            with patch.object(economy.hardened, "request_json", return_value=key_payload), patch.dict(
                os.environ,
                {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MANAGEMENT_KEY": ""},
                clear=False,
            ):
                code = economy.credit_preflight(config, root)
            report = json.loads((root / "credit-preflight.json").read_text(encoding="utf-8"))
            self.assertEqual(code, 3)
            self.assertIn("api-key-limit-remaining-below-economy-reserve", report["blockers"])
            self.assertEqual(report["model_inference_calls"], 0)

    @staticmethod
    def _record(task_id: str, strategy: str, quality: float, cost: float) -> dict:
        return {
            "task_id": task_id,
            "strategy": strategy,
            "status": "success",
            "safety_failure": False,
            "blind_fatal_error": False,
            "blind_quality_score": quality,
            "actual_cost_usd": cost,
            "blind_judge_count": 2,
            "blind_judge_models": ["judge-model-a", "judge-model-b"],
            "blind_judge_providers": ["judge-provider-a", "judge-provider-b"],
            "blind_judge_disagreement_points": 0.0,
            "blind_decisive_single_judge": True,
            "blind_primary_margin_points": 10.0,
        }

    def test_three_task_v5_v3_evidence_can_authorize_cutover(self):
        records = []
        for task_id in ("t1", "t2", "t3"):
            records.append(self._record(task_id, "v5_joint_graph", 0.84, 0.10))
            records.append(self._record(task_id, "v3", 0.80, 0.11))
        gate = economy.economy_cutover_gate(records)
        self.assertTrue(gate["production_cutover_allowed"])
        self.assertEqual(gate["task_wins_v5"], 3)
        self.assertFalse(gate["blockers"])
        self.assertEqual(gate["cutover_policy"]["minimum_tasks"], 3)
        self.assertEqual(gate["cutover_policy"]["required_strategies"], ["v5_joint_graph", "v3"])

    def test_single_judge_evidence_blocks_cutover(self):
        records = []
        for task_id in ("t1", "t2", "t3"):
            v5 = self._record(task_id, "v5_joint_graph", 0.84, 0.10)
            v3 = self._record(task_id, "v3", 0.80, 0.11)
            for row in (v5, v3):
                row["blind_judge_count"] = 1
                row["blind_judge_models"] = ["judge-model-a"]
                row["blind_judge_providers"] = ["judge-provider-a"]
            records.extend([v5, v3])
        gate = economy.economy_cutover_gate(records)
        self.assertFalse(gate["production_cutover_allowed"])
        self.assertIn("v5_joint_graph:invalid-independent-blind-judging", gate["blockers"])

    def test_v3_production_entry_is_not_replaced_or_deleted(self):
        execution = (ROOT / ".github" / "workflows" / "execution-ticket.yml").read_text(encoding="utf-8")
        benchmark_entry = (ROOT / "open-model-market" / "v3_benchmark_entry_final.py").read_text(encoding="utf-8")
        self.assertIn("python open-model-market/expert_team_hardened.py", execution)
        self.assertIn("max_completion_tokens", benchmark_entry)
        self.assertIn("max_tokens", benchmark_entry)
        self.assertIn('provider["require_parameters"] = False', benchmark_entry)

    def test_default_cutover_workflow_is_economical_and_benchmark_only(self):
        workflow = (ROOT / ".github" / "workflows" / "v5-live-benchmark-final.yml").read_text(encoding="utf-8")
        self.assertIn("[v5-benchmark-economy]", workflow)
        self.assertIn("v5_live_benchmark_economy_r6.py run", workflow)
        self.assertIn("zero-call run 30536650572", workflow)
        self.assertIn('V5_BENCHMARK_OUTPUT_ALLOWANCE_TOKENS: "10000"', workflow)
        self.assertNotIn("secrets.OPENROUTER_MANAGEMENT_KEY", workflow)
        self.assertNotIn("v5_live_benchmark_final.py run", workflow)
        self.assertNotIn("update_ref", workflow)
        self.assertNotIn("merge_pull_request", workflow)


if __name__ == "__main__":
    unittest.main()
