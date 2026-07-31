from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import GraphLimits  # noqa: E402
import v5_r8_executor as r8  # noqa: E402
import v5_total_call_cap as total_call_cap  # noqa: E402
from tests.test_v5_tabletop_production_semantics import (  # noqa: E402
    FAILED_PRODUCTION_TASK,
)


PUBLIC_INVESTMENT_TASK = (
    "比较三个城市公共投资方案，完成财务建模、政策与法律合规、证据核验、"
    "预测推演、独立红队反证和最终决策。"
)
SUPPLY_CHAIN_TASK = (
    "为一家年营收3000万元、现金储备有限的小型制造企业比较继续现供应商、"
    "供应商A、供应商B和混合采购四种方案。必须同时考虑14天现金流、交付中断、"
    "质量风险、合同约束、最坏情景、红队反证和可执行的逐日切换计划，不能假设"
    "题面之外的数据，最终给出有条件的选择规则。"
)


class TestV5FullLoadStability(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        total_call_cap.install()

    @staticmethod
    def _new_budget(
        *,
        maximum_total_calls: int,
        maximum_recovery_calls: int,
        maximum_budget_usd: float | None,
        risk_multiplier: float = 1.18,
        max_provider_failures: int = 3,
    ) -> r8.R8ExecutionBudget:
        limits = GraphLimits(
            cost_risk_multiplier=risk_multiplier,
            max_provider_failures=max_provider_failures,
        )
        token = r8._ACTIVE_LIMITS.set(limits)
        try:
            return r8.R8ExecutionBudget(
                max_planned_calls=maximum_total_calls,
                max_retries=maximum_recovery_calls,
                max_replacements=maximum_recovery_calls,
                max_budget_usd=maximum_budget_usd,
            )
        finally:
            r8._ACTIVE_LIMITS.reset(token)

    def test_concurrent_reservations_never_exceed_total_or_recovery_caps(self) -> None:
        budget = self._new_budget(
            maximum_total_calls=64,
            maximum_recovery_calls=8,
            maximum_budget_usd=None,
        )
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
        accepted_initial = [item for item in accepted if item[0] == "initial"]
        accepted_recovery = [item for item in accepted if item[0] != "initial"]
        snapshot = budget.snapshot()

        self.assertEqual(64, len(accepted))
        self.assertEqual(56, len(accepted_initial))
        self.assertEqual(8, len(accepted_recovery))
        self.assertEqual(64, snapshot["calls_reserved"])
        self.assertEqual(56, snapshot["initial_calls_reserved"])
        self.assertEqual(8, snapshot["recovery_calls_reserved"])
        self.assertEqual(64, snapshot["maximum_total_calls"])
        self.assertEqual(56, snapshot["maximum_initial_calls"])
        self.assertTrue(snapshot["denials"])

    def test_concurrent_budget_reservations_fail_closed_at_risk_boundary(self) -> None:
        budget = self._new_budget(
            maximum_total_calls=1000,
            maximum_recovery_calls=0,
            maximum_budget_usd=0.25,
        )

        def reserve(index: int) -> tuple[bool, str]:
            return budget.reserve("initial", 0.001, f"node-{index % 32}")

        with ThreadPoolExecutor(max_workers=64) as pool:
            results = list(pool.map(reserve, range(1000)))

        snapshot = budget.snapshot()
        accepted = sum(1 for ok, _ in results if ok)
        reasons = {reason for ok, reason in results if not ok}

        self.assertEqual(211, accepted)
        self.assertEqual(211, snapshot["calls_reserved"])
        self.assertLessEqual(snapshot["estimated_cost_reserved_usd"], 0.25)
        self.assertIn("global-risk-adjusted-budget-exhausted", reasons)

    def test_concurrent_reconciliation_has_no_lost_updates_or_pending_leaks(self) -> None:
        budget = self._new_budget(
            maximum_total_calls=256,
            maximum_recovery_calls=0,
            maximum_budget_usd=None,
        )

        with ThreadPoolExecutor(max_workers=64) as pool:
            reservations = list(
                pool.map(
                    lambda index: budget.reserve(
                        "initial", 0.0001, f"node-{index % 16}"
                    ),
                    range(256),
                )
            )
        self.assertTrue(all(ok for ok, _ in reservations))

        with ThreadPoolExecutor(max_workers=64) as pool:
            reconciliations = list(pool.map(budget.reconcile, [0.0001] * 256))

        snapshot = budget.snapshot()
        self.assertFalse(any(reconciliations))
        self.assertEqual(0.0, snapshot["estimated_cost_reserved_usd"])
        self.assertEqual(0.0256, snapshot["actual_cost_usd"])
        self.assertEqual(256, snapshot["calls_reserved"])

    def test_provider_circuit_updates_are_atomic_under_contention(self) -> None:
        budget = self._new_budget(
            maximum_total_calls=16,
            maximum_recovery_calls=2,
            maximum_budget_usd=None,
            max_provider_failures=3,
        )
        endpoint = "provider-a"

        with ThreadPoolExecutor(max_workers=64) as pool:
            list(
                pool.map(
                    lambda index: budget.fail_endpoint(endpoint, f"failure-{index}"),
                    range(512),
                )
            )

        snapshot = budget.snapshot()["provider_circuit"]
        self.assertFalse(budget.endpoint_available(endpoint))
        self.assertEqual(512, snapshot["failures"][endpoint])
        self.assertEqual(512, len(snapshot["reasons"][endpoint]))
        self.assertEqual(3, snapshot["max_failures"])

    @staticmethod
    def _graph_signature(graph: dict[str, Any]) -> str:
        stable = {
            "nodes": [
                {
                    "node_id": node["node_id"],
                    "assigned_work": node["assigned_work"],
                    "functions": node["functions"],
                    "model": node["model"],
                    "provider_endpoint": node["provider_endpoint"],
                    "output_contract": node["output_contract"],
                    "estimated_cost": node["estimated_cost"],
                }
                for node in graph["nodes"]
            ],
            "edges": graph["edges"],
            "execution_stages": graph["execution_stages"],
            "entry_nodes": graph["entry_nodes"],
            "final_nodes": graph["final_nodes"],
            "required_work": graph["required_work"],
            "estimated_total_cost": graph["estimated_total_cost"],
        }
        return json.dumps(stable, ensure_ascii=False, sort_keys=True)

    def test_parallel_full_pipeline_dry_runs_are_isolated_and_deterministic(self) -> None:
        scenarios = (
            ("public-investment", PUBLIC_INVESTMENT_TASK, 16, 2),
            ("closed-book-tabletop", FAILED_PRODUCTION_TASK, 4, 1),
            ("supply-chain", SUPPLY_CHAIN_TASK, 8, 1),
        )
        cases = [
            (name, task, total_calls, recovery_calls, iteration)
            for name, task, total_calls, recovery_calls in scenarios
            for iteration in range(8)
        ]
        env = os.environ.copy()
        env.pop("OPENROUTER_API_KEY", None)

        with tempfile.TemporaryDirectory(prefix="v5-full-load-") as directory:
            root = Path(directory)

            def run_case(
                case: tuple[str, str, int, int, int]
            ) -> tuple[str, int, int, str, str]:
                name, task, total_calls, recovery_calls, iteration = case
                output_dir = root / f"{name}-{iteration}"
                command = [
                    sys.executable,
                    str(ROOT / "open-model-market" / "v5_pipeline.py"),
                    "--task",
                    task,
                    "--catalog-file",
                    str(ROOT / "tests" / "fixtures" / "models.json"),
                    "--endpoint-file",
                    str(ROOT / "tests" / "fixtures" / "endpoints.json"),
                    "--dry-run",
                    "--maximum-total-calls",
                    str(total_calls),
                    "--maximum-recovery-calls",
                    str(recovery_calls),
                    "--quality-tier",
                    "quality",
                    "--output-dir",
                    str(output_dir),
                ]
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                if completed.returncode != 0:
                    return (
                        name,
                        iteration,
                        completed.returncode,
                        "",
                        completed.stdout + "\n" + completed.stderr,
                    )
                dry = json.loads((output_dir / "v5-dry-run.json").read_text())
                graph = json.loads(
                    (output_dir / "v5-execution-graph.json").read_text()
                )
                self.assertEqual("planned-not-executed", dry["status"])
                self.assertFalse(dry["production_entrypoint_changed"])
                self.assertTrue((output_dir / "artifact-manifest.json").is_file())
                return (
                    name,
                    iteration,
                    completed.returncode,
                    self._graph_signature(graph),
                    "",
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(run_case, cases))

        failures = [result for result in results if result[2] != 0]
        self.assertFalse(failures, failures[0][4] if failures else "")
        for name, _, _, _ in scenarios:
            signatures = {result[3] for result in results if result[0] == name}
            self.assertEqual(1, len(signatures), name)


if __name__ == "__main__":
    unittest.main()
