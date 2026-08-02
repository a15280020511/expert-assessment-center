from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph  # noqa: E402
from v5_runtime import (  # noqa: E402
    BudgetController,
    FailureCategory,
    RuntimeConfig,
)

PUBLIC_INVESTMENT_TASK = (
    "比较三个城市公共投资方案，完成财务建模、政策与法律合规、证据核验、"
    "预测推演、独立红队反证和最终决策。"
)
CLOSED_WORLD_TASK = (
    "仅依据题面，不得调用外部工具，不联网，不得编造。"
    "比较方案A与方案B，只接受完整交付。"
)
SUPPLY_CHAIN_TASK = (
    "比较四种供应链方案，考虑现金流、交付中断、质量、合同、最坏情景、"
    "红队反证和逐日切换计划，最终给出有条件选择规则。"
)


def _empty_graph() -> ExecutionGraph:
    return ExecutionGraph(
        nodes=(),
        edges=(),
        execution_stages=(),
        entry_nodes=(),
        final_nodes=(),
        required_work=(),
        estimated_quality=0.0,
        quality_floor=0.0,
        estimated_total_cost=0.0,
        metadata={},
    )


def _budget(*, cost: float | None, failures: int = 3) -> BudgetController:
    config = RuntimeConfig(
        total_call_limit=16,
        recovery_call_limit=2,
        cost_anomaly_usd=cost,
        quality_tier="value",
        max_provider_failures=failures,
    )
    return BudgetController(config, _empty_graph())


class TestV5FullLoadStability(unittest.TestCase):
    def test_concurrent_reservations_never_exceed_total_or_recovery_caps(self) -> None:
        budget = _budget(cost=None)
        attempts = (
            [("initial", index) for index in range(960)]
            + [("retry", index) for index in range(32)]
            + [("replacement", index) for index in range(32)]
        )

        def reserve(value: tuple[str, int]) -> tuple[str, bool, str]:
            kind, index = value
            accepted, reason = budget.reserve(kind, 0.0, f"node-{index % 16}")
            return kind, accepted, reason

        with ThreadPoolExecutor(max_workers=64) as pool:
            results = list(pool.map(reserve, attempts))
        accepted = [item for item in results if item[1]]
        snapshot = budget.snapshot()
        self.assertEqual(16, len(accepted))
        self.assertEqual(14, snapshot["initial_calls_reserved"])
        self.assertEqual(2, snapshot["recovery_calls_reserved"])
        self.assertEqual(16, snapshot["calls_reserved"])
        self.assertTrue(snapshot["denials"])

    def test_concurrent_cost_reservations_fail_closed(self) -> None:
        budget = _budget(cost=0.01)
        with ThreadPoolExecutor(max_workers=64) as pool:
            results = list(
                pool.map(
                    lambda index: budget.reserve(
                        "initial", 0.001, f"node-{index}"
                    ),
                    range(1000),
                )
            )
        snapshot = budget.snapshot()
        accepted = sum(1 for ok, _ in results if ok)
        self.assertLessEqual(accepted, 8)
        self.assertLessEqual(snapshot["estimated_cost_reserved_usd"], 0.01)
        self.assertTrue(snapshot["denials"])

    def test_concurrent_reconciliation_has_no_lost_updates(self) -> None:
        budget = _budget(cost=None)
        reservations = [
            budget.reserve("initial", 0.0001, f"node-{index}")
            for index in range(14)
        ]
        self.assertTrue(all(ok for ok, _ in reservations))
        with ThreadPoolExecutor(max_workers=14) as pool:
            reconciliations = list(pool.map(budget.reconcile, [0.0001] * 14))
        snapshot = budget.snapshot()
        self.assertFalse(any(reconciliations))
        self.assertEqual(0.0, snapshot["estimated_cost_reserved_usd"])
        self.assertEqual(0.0014, snapshot["actual_cost_usd"])

    def test_provider_circuit_updates_are_atomic(self) -> None:
        budget = _budget(cost=None, failures=3)
        with ThreadPoolExecutor(max_workers=64) as pool:
            list(
                pool.map(
                    lambda _: budget.fail_endpoint(
                        "provider-a", FailureCategory.PROVIDER_TIMEOUT
                    ),
                    range(512),
                )
            )
        snapshot = budget.snapshot()["provider_circuit"]
        self.assertFalse(budget.endpoint_available("provider-a"))
        self.assertEqual(512, snapshot["failures"]["provider-a"])
        self.assertEqual(512, len(snapshot["reasons"]["provider-a"]))

    def test_parallel_advisory_dry_runs_are_isolated_and_deterministic(self) -> None:
        scenarios = (
            ("public", PUBLIC_INVESTMENT_TASK, 16, 2),
            ("closed", CLOSED_WORLD_TASK, 8, 1),
            ("supply", SUPPLY_CHAIN_TASK, 8, 1),
        )
        cases = [(*row, iteration) for row in scenarios for iteration in range(4)]
        env = os.environ.copy()
        env.pop("OPENROUTER_API_KEY", None)
        with tempfile.TemporaryDirectory(prefix="v5-advisory-load-") as directory:
            root = Path(directory)

            def run_case(case: tuple[str, str, int, int, int]):
                name, task, total, recovery, iteration = case
                output = root / f"{name}-{iteration}"
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "open-model-market" / "v5_pipeline.py"),
                        "--task",
                        task,
                        "--catalog-file",
                        str(ROOT / "tests/fixtures/models.json"),
                        "--endpoint-file",
                        str(ROOT / "tests/fixtures/endpoints.json"),
                        "--dry-run",
                        "--maximum-total-calls",
                        str(total),
                        "--maximum-recovery-calls",
                        str(recovery),
                        "--output-dir",
                        str(output),
                    ],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                if completed.returncode:
                    return name, completed.returncode, completed.stdout + completed.stderr
                dry = json.loads((output / "v5-dry-run.json").read_text())
                self.assertEqual("validated-not-executed", dry["status"])
                self.assertEqual(0, dry["model_calls"])
                self.assertTrue(dry["claude_is_advisory_only"])
                self.assertFalse(dry["claude_gatekeeping_allowed"])
                self.assertEqual(1, dry["gpt_synthesis_calls"])
                self.assertFalse((output / "v5-execution-graph.json").exists())
                signature = json.dumps(dry, sort_keys=True, ensure_ascii=False)
                return name, 0, signature

            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(run_case, cases))
        failures = [row for row in results if row[1]]
        self.assertFalse(failures, failures[0][2] if failures else "")
        for name, *_ in scenarios:
            signatures = {row[2] for row in results if row[0] == name}
            self.assertEqual(1, len(signatures), name)


if __name__ == "__main__":
    unittest.main()
