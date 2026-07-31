import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_live_benchmark as base  # noqa: E402
import v5_live_benchmark_r8i as r8i  # noqa: E402


class TestV5R8IBudgetIntegrity(unittest.TestCase):
    def test_external_usage_is_accounted_before_limit_failure(self):
        ledger = base.GlobalLedger(max_cost_usd=1.0, max_calls=3)
        with self.assertRaises(base.BenchmarkLimitExceeded):
            r8i._truthful_add_external(
                ledger,
                task_id="task",
                strategy="v3",
                calls=4,
                cost_usd=0.2,
            )
        self.assertEqual(ledger.calls, 4)
        self.assertAlmostEqual(ledger.actual_cost_usd, 0.2)
        self.assertTrue(ledger.events[-1]["call_ceiling_exceeded"])

    def test_external_cost_is_accounted_before_cost_failure(self):
        ledger = base.GlobalLedger(max_cost_usd=0.25, max_calls=10)
        with self.assertRaises(base.BenchmarkLimitExceeded):
            r8i._truthful_add_external(
                ledger,
                task_id="task",
                strategy="v3",
                calls=2,
                cost_usd=0.30,
            )
        self.assertEqual(ledger.calls, 2)
        self.assertAlmostEqual(ledger.actual_cost_usd, 0.30)
        self.assertTrue(ledger.events[-1]["cost_ceiling_exceeded"])

    def test_global_call_limit_covers_provable_worst_case(self):
        # 3 * (V5 9 + V3 7 + blind judges 3) = 57.
        self.assertEqual(r8i.MAX_GLOBAL_CALLS, 57)
        self.assertEqual(3 * (9 + r8i.V3_MAX_PAID_CALLS + 3), 57)

    def test_r8j_workflow_is_exact_manual_unlock_single_key_and_cancels_stale_run(self):
        workflow = (ROOT / ".github/workflows/v5-r8-stage-d-paid-blind-r8i.yml").read_text(encoding="utf-8")
        self.assertIn("github.event.issue.number == 64", workflow)
        self.assertIn("RUN_V5_R8_STAGE_D_20260731_R8J", workflow)
        self.assertNotIn("RUN_V5_R8_STAGE_D_20260731_R8I'", workflow)
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertIn("V5_R8J_STAGE_D_TRIGGERED", workflow)
        self.assertIn("Model calls at receipt: `0`", workflow)
        self.assertIn("max_calls\": 57", workflow)
        self.assertIn("v5_live_benchmark_r8i.py", workflow)
        self.assertIn("secrets.OPENROUTER_API_KEY", workflow)
        self.assertNotIn("OPENROUTER_MANAGEMENT_KEY", workflow)
        self.assertIn("Production default switch allowed by this workflow: `false`", workflow)
        self.assertIn("V3 deletion allowed: `false`", workflow)

    def test_v3_runner_has_pre_call_budget_and_no_replacements(self):
        source = (ROOT / "open-model-market/v3_stage_d_bounded.py").read_text(encoding="utf-8")
        wrapper = (ROOT / "open-model-market/v5_live_benchmark_r8i.py").read_text(encoding="utf-8")
        self.assertIn("projected conservative spend", source)
        self.assertIn("denied before call", source)
        self.assertIn('os.environ["EXPERT_MAX_REPLACEMENTS"] = "0"', source)
        self.assertIn('env["EXPERT_MAX_REPLACEMENTS"] = "0"', wrapper)
        self.assertIn("OUTPUT_ALLOWANCE_TOKENS = 10_000", source)


if __name__ == "__main__":
    unittest.main()
