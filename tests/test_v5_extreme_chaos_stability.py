from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import GraphLimits, SelectedNode  # noqa: E402
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
WIFI_TASK = (
    "比较手机流量和随身WiFi。A每月39元含60GB，超出5元每GB；B设备99元，"
    "每月20元含120GB。计算12个月和18个月总成本、盈亏平衡时间，并在80GB、"
    "100GB、140GB下做敏感性分析，给出30天试用建议。"
)
JOB_CHOICE_TASK = (
    "比较夜班保安与网约车。保安月收入4200元，网约车流水12000元、成本7800元。"
    "计算净收入、单位工时收入、三年收入，做悲观基准乐观情景、现金流风险分析，"
    "并给出转岗门槛和90天行动方案。"
)


class TestV5ExtremeChaosStability(unittest.TestCase):
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

    def test_16384_way_call_and_recovery_contention_never_oversubscribes(self) -> None:
        budget = self._new_budget(
            maximum_total_calls=512,
            maximum_recovery_calls=64,
            maximum_budget_usd=None,
        )
        attempts = (
            [("initial", index) for index in range(14336)]
            + [("retry", index) for index in range(1024)]
            + [("replacement", index) for index in range(1024)]
        )

        def reserve(value: tuple[str, int]) -> tuple[str, bool, str]:
            kind, index = value
            accepted, reason = budget.reserve(kind, 0.0, f"node-{index % 128}")
            return kind, accepted, reason

        with ThreadPoolExecutor(max_workers=128) as pool:
            results = list(pool.map(reserve, attempts))

        accepted = [result for result in results if result[1]]
        accepted_initial = sum(1 for kind, ok, _ in accepted if kind == "initial" and ok)
        accepted_recovery = sum(1 for kind, ok, _ in accepted if kind != "initial" and ok)
        snapshot = budget.snapshot()

        self.assertEqual(512, len(accepted))
        self.assertEqual(448, accepted_initial)
        self.assertEqual(64, accepted_recovery)
        self.assertEqual(512, snapshot["calls_reserved"])
        self.assertEqual(448, snapshot["initial_calls_reserved"])
        self.assertEqual(64, snapshot["recovery_calls_reserved"])
        self.assertLessEqual(snapshot["calls_reserved"], snapshot["maximum_total_calls"])
        self.assertGreaterEqual(len(snapshot["denials"]), len(attempts) - 512)

    def test_context_local_limits_remain_isolated_across_512_concurrent_runs(self) -> None:
        def build(index: int) -> tuple[float, int, float, int]:
            risk_multiplier = 1.0 + ((index % 31) / 100.0)
            max_provider_failures = 1 + (index % 11)
            budget = self._new_budget(
                maximum_total_calls=4,
                maximum_recovery_calls=1,
                maximum_budget_usd=0.25,
                risk_multiplier=risk_multiplier,
                max_provider_failures=max_provider_failures,
            )
            accepted, reason = budget.reserve("initial", 0.001, f"node-{index}")
            self.assertTrue(accepted, reason)
            snapshot = budget.snapshot()
            return (
                risk_multiplier,
                max_provider_failures,
                snapshot["risk_multiplier"],
                snapshot["provider_circuit"]["max_failures"],
            )

        with ThreadPoolExecutor(max_workers=128) as pool:
            results = list(pool.map(build, range(512)))

        for expected_risk, expected_failures, actual_risk, actual_failures in results:
            self.assertEqual(expected_risk, actual_risk)
            self.assertEqual(expected_failures, actual_failures)

    def test_16384_provider_failures_are_atomic_and_endpoint_isolated(self) -> None:
        budget = self._new_budget(
            maximum_total_calls=16,
            maximum_recovery_calls=2,
            maximum_budget_usd=None,
            max_provider_failures=5,
        )
        endpoint_count = 32
        event_count = 16384

        def fail(index: int) -> None:
            endpoint = f"provider-{index % endpoint_count}"
            budget.fail_endpoint(endpoint, f"failure-{index}")

        with ThreadPoolExecutor(max_workers=128) as pool:
            list(pool.map(fail, range(event_count)))

        circuit = budget.snapshot()["provider_circuit"]
        expected_per_endpoint = event_count // endpoint_count
        self.assertEqual(endpoint_count, len(circuit["failures"]))
        for index in range(endpoint_count):
            endpoint = f"provider-{index}"
            self.assertEqual(expected_per_endpoint, circuit["failures"][endpoint])
            self.assertEqual(expected_per_endpoint, len(circuit["reasons"][endpoint]))
            self.assertFalse(budget.endpoint_available(endpoint))

    def test_4096_parallel_reconciliations_have_no_lost_updates_or_leaks(self) -> None:
        budget = self._new_budget(
            maximum_total_calls=4096,
            maximum_recovery_calls=0,
            maximum_budget_usd=None,
        )

        with ThreadPoolExecutor(max_workers=128) as pool:
            reservations = list(
                pool.map(
                    lambda index: budget.reserve(
                        "initial", 0.000001, f"node-{index % 256}"
                    ),
                    range(4096),
                )
            )
        self.assertTrue(all(ok for ok, _ in reservations))

        with ThreadPoolExecutor(max_workers=128) as pool:
            reconciliations = list(pool.map(budget.reconcile, [0.000001] * 4096))

        snapshot = budget.snapshot()
        self.assertFalse(any(reconciliations))
        self.assertEqual(0.0, snapshot["estimated_cost_reserved_usd"])
        self.assertEqual(0.004096, snapshot["actual_cost_usd"])
        self.assertEqual(4096, snapshot["calls_reserved"])

    def test_16384_mixed_failure_classifications_remain_deterministic(self) -> None:
        node = SelectedNode(
            node_id="node-strict",
            assigned_work=("analysis",),
            professional_capabilities={"analysis": 1.0},
            functions=("analysis",),
            prompt_profile={},
            reasoning_profile={},
            parameter_profile={},
            model="fixture/model",
            provider_endpoint="fixture/provider",
            output_contract={
                "machine_readable_required": True,
                "required_fields": ["result"],
            },
            estimated_quality=0.9,
            quality_uncertainty=0.1,
            estimated_cost=0.001,
        )
        cases = (
            ("budget_denied", None),
            (
                "rate_limited",
                SimpleNamespace(error="429 rate limit", gate_reasons=(), answer="x"),
            ),
            (
                "transient_provider",
                SimpleNamespace(error="upstream 503", gate_reasons=(), answer="x"),
            ),
            (
                "empty_output",
                SimpleNamespace(error="", gate_reasons=(), answer=""),
            ),
            (
                "truncated_output",
                SimpleNamespace(
                    error="truncated-output", gate_reasons=(), answer="partial"
                ),
            ),
            (
                "invalid_json",
                SimpleNamespace(error="", gate_reasons=(), answer="{"),
            ),
            (
                "invalid_json",
                SimpleNamespace(error="", gate_reasons=(), answer='{"other": 1}'),
            ),
            (
                "quality_failure",
                SimpleNamespace(error="", gate_reasons=(), answer='{"result": "ok"}'),
            ),
        )
        workload = [case for _ in range(2048) for case in cases]

        def classify(case: tuple[str, Any]) -> tuple[str, str]:
            expected, attempt = case
            return expected, r8._failure_class(attempt, node)

        with ThreadPoolExecutor(max_workers=128) as pool:
            results = list(pool.map(classify, workload))

        self.assertTrue(all(expected == actual for expected, actual in results))
        counts = Counter(actual for _, actual in results)
        self.assertEqual(2048, counts["budget_denied"])
        self.assertEqual(2048, counts["rate_limited"])
        self.assertEqual(2048, counts["transient_provider"])
        self.assertEqual(2048, counts["empty_output"])
        self.assertEqual(2048, counts["truncated_output"])
        self.assertEqual(4096, counts["invalid_json"])
        self.assertEqual(2048, counts["quality_failure"])

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

    def test_60_parallel_success_and_fail_closed_dry_runs_are_isolated(self) -> None:
        scenarios = (
            (
                "public-investment-quality",
                PUBLIC_INVESTMENT_TASK,
                16,
                2,
                "quality",
                "success",
            ),
            (
                "public-investment-underprovisioned",
                PUBLIC_INVESTMENT_TASK,
                4,
                1,
                "value",
                "failed-closed",
            ),
            (
                "closed-book-tabletop",
                FAILED_PRODUCTION_TASK,
                4,
                1,
                "value",
                "success",
            ),
            ("supply-chain", SUPPLY_CHAIN_TASK, 8, 1, "value", "success"),
            ("wifi-decision", WIFI_TASK, 4, 1, "value", "success"),
            ("job-choice", JOB_CHOICE_TASK, 4, 1, "value", "success"),
        )
        cases = [
            (
                name,
                task,
                total_calls,
                recovery_calls,
                quality_tier,
                expected_status,
                iteration,
            )
            for (
                name,
                task,
                total_calls,
                recovery_calls,
                quality_tier,
                expected_status,
            ) in scenarios
            for iteration in range(10)
        ]
        env = os.environ.copy()
        env.pop("OPENROUTER_API_KEY", None)

        with tempfile.TemporaryDirectory(prefix="v5-extreme-chaos-") as directory:
            root = Path(directory)

            def run_case(
                case: tuple[str, str, int, int, str, str, int]
            ) -> tuple[str, int, str, str, str]:
                (
                    name,
                    task,
                    total_calls,
                    recovery_calls,
                    quality_tier,
                    expected_status,
                    iteration,
                ) = case
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
                    quality_tier,
                    "--output-dir",
                    str(output_dir),
                ]
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=240,
                    check=False,
                )
                diagnostics = completed.stdout + "\n" + completed.stderr
                if expected_status == "failed-closed":
                    if completed.returncode == 0:
                        return name, iteration, "unexpected-success", "", diagnostics
                    if "BUDGET_INSUFFICIENT_NODES" not in diagnostics:
                        return name, iteration, "wrong-failure", "", diagnostics
                    self.assertFalse((output_dir / "v5-execution-graph.json").exists())
                    return (
                        name,
                        iteration,
                        "failed-closed",
                        "BUDGET_INSUFFICIENT_NODES",
                        "",
                    )
                if completed.returncode != 0:
                    return name, iteration, "unexpected-failure", "", diagnostics

                dry = json.loads((output_dir / "v5-dry-run.json").read_text())
                graph = json.loads(
                    (output_dir / "v5-execution-graph.json").read_text()
                )
                expected_files = (
                    "v5-dry-run.json",
                    "v5-execution-graph.json",
                    "v5-optimization.json",
                    "v5-planning-benchmark.json",
                    "v5-runtime-config.json",
                    "task-resource-matrix.json",
                    "artifact-manifest.json",
                )
                self.assertEqual("planned-not-executed", dry["status"])
                self.assertFalse(dry["production_entrypoint_changed"])
                self.assertTrue(
                    all((output_dir / expected).is_file() for expected in expected_files)
                )
                return (
                    name,
                    iteration,
                    "success",
                    self._graph_signature(graph),
                    "",
                )

            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(run_case, cases))

        unexpected = [
            result
            for result in results
            if result[2]
            in {"unexpected-success", "unexpected-failure", "wrong-failure"}
        ]
        self.assertFalse(unexpected, unexpected[0][4] if unexpected else "")
        for name, _, _, _, _, expected_status in scenarios:
            observed_statuses = {result[2] for result in results if result[0] == name}
            signatures = {result[3] for result in results if result[0] == name}
            self.assertEqual({expected_status}, observed_statuses, name)
            self.assertEqual(1, len(signatures), name)


if __name__ == "__main__":
    unittest.main()
