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
import v5_live_benchmark_final as final  # noqa: E402
import v5_value_optimizer  # noqa: E402


class TestFinalFiveTaskBenchmark(unittest.TestCase):
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

    def test_unbounded_key_is_controlled_by_runtime_global_ledger(self):
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
            self.assertEqual(code, 0)
            self.assertEqual(report["status"], "runtime-ledger-bounded")
            self.assertFalse(report["blockers"])
            self.assertEqual(report["runtime_global_cost_ceiling_usd"], 20.0)
            self.assertEqual(report["runtime_global_call_ceiling"], 200)

    def test_known_low_finite_limit_remains_a_hard_blocker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "benchmark-config.json"
            config.write_text(
                json.dumps({"max_cost_usd": 20.0, "max_calls": 200}),
                encoding="utf-8",
            )
            payload = {
                "data": {
                    "label": "bounded-key",
                    "limit": 20.0,
                    "limit_remaining": 0.5,
                    "usage": 19.5,
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
            self.assertEqual(report["status"], "insufficient")
            self.assertIn(
                "api-key-limit-remaining-below-benchmark-reserve",
                report["blockers"],
            )

    def test_v3_production_entry_is_not_replaced_or_deleted(self):
        execution = (ROOT / ".github" / "workflows" / "execution-ticket.yml").read_text(encoding="utf-8")
        benchmark_entry = (ROOT / "open-model-market" / "v3_benchmark_entry_final.py").read_text(encoding="utf-8")
        self.assertIn("python open-model-market/expert_team_hardened.py", execution)
        self.assertIn("max_completion_tokens", benchmark_entry)
        self.assertIn("max_tokens", benchmark_entry)
        self.assertIn('provider["require_parameters"] = False', benchmark_entry)

    def test_final_workflow_is_benchmark_only(self):
        workflow = (ROOT / ".github" / "workflows" / "v5-live-benchmark-final.yml").read_text(encoding="utf-8")
        self.assertIn("[v5-benchmark-final]", workflow)
        self.assertIn("v5_live_benchmark_final.py run", workflow)
        self.assertNotIn("update_ref", workflow)
        self.assertNotIn("merge_pull_request", workflow)


if __name__ == "__main__":
    unittest.main()
