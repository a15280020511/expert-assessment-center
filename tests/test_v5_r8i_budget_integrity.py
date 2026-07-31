import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

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
        self.assertEqual(r8i.MAX_GLOBAL_CALLS, 57)
        self.assertEqual(3 * (9 + r8i.V3_MAX_PAID_CALLS + 3), 57)

    def test_v5_node_allowance_uses_dynamic_recommendation_under_maximum(self):
        node = SimpleNamespace(parameter_profile={
            "recommended_output_allowance_tokens": 5743,
            "estimated_completion_usage_tokens": 4523,
        })
        self.assertEqual(r8i._dynamic_node_allowance(node), 5743)
        node.parameter_profile["recommended_output_allowance_tokens"] = 15000
        self.assertEqual(r8i._dynamic_node_allowance(node), 10000)

    def test_judge_allowance_is_dynamic_and_bounded(self):
        endpoint = {
            "completion_price_per_million": 7.5,
            "max_completion_tokens": 128000,
        }
        short_value = r8i._dynamic_judge_allowance(endpoint, "system", "short")
        long_value = r8i._dynamic_judge_allowance(endpoint, "system", "x" * 20000)
        self.assertGreaterEqual(short_value, 1024)
        self.assertGreater(long_value, short_value)
        self.assertLessEqual(long_value, 10000)

    def test_consumed_r8j_workflow_is_closed(self):
        workflow = (ROOT / ".github/workflows/v5-r8-stage-d-paid-blind-r8i.yml").read_text(encoding="utf-8")
        self.assertIn("Stage-D Paid Blind Benchmark (Closed)", workflow)
        self.assertIn("if: false", workflow)
        self.assertNotIn("issue_comment", workflow)
        self.assertNotIn("OPENROUTER_API_KEY", workflow)
        self.assertNotIn("RUN_V5_R8_STAGE_D_20260731_R8J", workflow)
        self.assertIn("Run 30593349545", workflow)

    def test_v3_runner_has_dynamic_pre_call_budget_and_no_replacements(self):
        source = (ROOT / "open-model-market/v3_stage_d_bounded.py").read_text(encoding="utf-8")
        wrapper = (ROOT / "open-model-market/v5_live_benchmark_r8i.py").read_text(encoding="utf-8")
        self.assertIn("def _budgeted_allowance", source)
        self.assertIn("dynamic-equal-share-budgeted-per-call", source)
        self.assertIn("projected conservative spend", source)
        self.assertIn("denied before call", source)
        self.assertIn('os.environ["EXPERT_MAX_REPLACEMENTS"] = "0"', source)
        self.assertIn('env["EXPERT_MAX_REPLACEMENTS"] = "0"', wrapper)
        self.assertIn("OUTPUT_ALLOWANCE_TOKENS = 10_000", source)
        self.assertNotIn('payload["max_tokens"] = _allowance(model)', source)


if __name__ == "__main__":
    unittest.main()
