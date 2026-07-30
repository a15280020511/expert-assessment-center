import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_live_benchmark as base  # noqa: E402
import v5_r8_fail_fast_benchmark as fail_fast  # noqa: E402


class TestV5R8FailFastBenchmark(unittest.TestCase):
    def test_zero_call_v5_failure_skips_v3_and_blind_judges(self):
        failed = base.StrategyOutcome(
            task_id="task-1",
            strategy="v5_joint_graph",
            status="failed",
            answer=None,
            actual_cost_usd=0.0,
            latency_seconds=0.1,
            call_count=0,
            models=["vendor/model"],
            providers=["vendor/model@provider"],
            safety_failure=True,
            error="preflight-risk-adjusted-cost-above-hard-budget",
            artifacts={"executor": "v5-r8-fault-aware"},
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.json"
            suite = root / "suite.json"
            output = root / "output"
            config.write_text(
                json.dumps(
                    {
                        "benchmark_id": "test",
                        "max_cost_usd": 1.5,
                        "max_calls": 45,
                        "max_strategy_cost_usd": 0.25,
                        "task_ids": ["task-1", "task-2", "task-3"],
                    }
                ),
                encoding="utf-8",
            )
            suite.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {"task_id": f"task-{index}", "question": "q", "requirements": []}
                            for index in range(1, 4)
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), patch.object(
                fail_fast.market,
                "build_run_config",
                return_value=SimpleNamespace(),
            ), patch.object(
                fail_fast.market,
                "fetch_catalog",
                return_value=({}, "fixture"),
            ), patch.object(
                base,
                "_v5_strategy",
                return_value=(failed, {"endpoints": []}),
            ) as v5_call, patch.object(
                base,
                "_v3_strategy",
            ) as v3_call, patch.object(
                base,
                "_evaluate_task",
            ) as judge_call:
                code = fail_fast.run_benchmark(config, suite, output)

            result = json.loads(
                (output / "v5-live-benchmark-results.json").read_text(encoding="utf-8")
            )

        self.assertEqual(code, 2)
        self.assertEqual(v5_call.call_count, 1)
        v3_call.assert_not_called()
        judge_call.assert_not_called()
        self.assertEqual(result["status"], "v5_fail_fast_stopped")
        self.assertTrue(result["v3_and_judges_skipped_due_to_v5_failure"])
        self.assertFalse(result["v3_executed"])
        self.assertFalse(result["blind_judges_executed"])
        self.assertEqual(result["ledger"]["calls"], 0)
        self.assertEqual(result["ledger"]["actual_cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
