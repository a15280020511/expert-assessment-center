#!/usr/bin/env python3
"""R8 Stage-D paid blind benchmark: three tasks, V5 R8 versus preserved V3 only."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_live_benchmark as base
import v5_live_benchmark_economy as economy
import v5_live_benchmark_economy_r6 as r6
import v5_production_hardening as production
from execution_graph import GraphLimits as OriginalGraphLimits
from v5_r8_single_key_preflight import check_single_api_key

TASK_IDS = (
    "retail-expansion-unit-economics",
    "software-job-runner-security",
    "public-health-rumor-response",
)
MAX_STRATEGY_COST_USD = 0.25
MAX_GLOBAL_COST_USD = 1.50
MAX_GLOBAL_CALLS = 45
OUTPUT_ALLOWANCE_TOKENS = 10_000
_INSTALLED = False


def credit_preflight(config_path: str | Path, output_dir: str | Path) -> int:
    """Use only the ordinary inference key and make zero model calls."""
    config = base._load_json(config_path)
    required = float(config.get("max_cost_usd", MAX_GLOBAL_COST_USD))
    strategy_cap = float(config.get("max_strategy_cost_usd", MAX_STRATEGY_COST_USD))
    task_ids = tuple(str(value) for value in config.get("task_ids", ()))
    if required > MAX_GLOBAL_COST_USD + 1e-12:
        raise base.LiveBenchmarkError("R8 global reserve exceeds the 1.50 USD hard ceiling")
    if strategy_cap > MAX_STRATEGY_COST_USD + 1e-12:
        raise base.LiveBenchmarkError("R8 per-strategy task cap exceeds 0.25 USD")
    if task_ids != TASK_IDS:
        raise base.LiveBenchmarkError("R8 Stage-D must run the fixed three-task suite in fixed order")

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    report = check_single_api_key(
        os.getenv("OPENROUTER_API_KEY", ""),
        required,
        output_path=root / "credit-preflight.json",
    )
    report.update({
        "version": 2,
        "mode": "v5-r8-stage-d",
        "task_ids": list(TASK_IDS),
        "per_strategy_task_hard_cap_usd": MAX_STRATEGY_COST_USD,
        "global_actual_cost_hard_cap_usd": MAX_GLOBAL_COST_USD,
        "global_paid_call_hard_cap": MAX_GLOBAL_CALLS,
        "output_allowance_tokens": OUTPUT_ALLOWANCE_TOKENS,
        "management_key_used": False,
        "production_entrypoint_changed": False,
        "v3_deleted": False,
    })
    (root / "credit-preflight.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 3 if report.get("blockers") else 0


def _r8_limits(**kwargs: Any) -> OriginalGraphLimits:
    kwargs["max_nodes"] = min(int(kwargs.get("max_nodes", 9)), 9)
    kwargs["max_edges"] = min(int(kwargs.get("max_edges", 32)), 32)
    kwargs["max_stages"] = min(int(kwargs.get("max_stages", 6)), 6)
    kwargs["max_model_calls"] = min(int(kwargs.get("max_model_calls", 9)), 9)
    kwargs["max_retries"] = 1
    kwargs["max_replacements"] = 2
    kwargs["max_output_allowance_tokens"] = OUTPUT_ALLOWANCE_TOKENS
    return OriginalGraphLimits(**kwargs)


def _annotate_v5_strategy() -> None:
    original = base._v5_strategy

    def r8_v5_strategy(
        task: Mapping[str, Any],
        root: Path,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        outcome, market = original(task, root, *args, **kwargs)
        summary_path = Path(root) / "v5-execution-summary.json"
        summary: Mapping[str, Any] = {}
        if summary_path.exists():
            try:
                loaded = json.loads(summary_path.read_text(encoding="utf-8"))
                summary = loaded if isinstance(loaded, Mapping) else {}
            except (OSError, json.JSONDecodeError):
                summary = {}
        artifacts = dict(outcome.artifacts)
        artifacts.update({
            "executor": summary.get("executor"),
            "completion_mode": summary.get("completion_mode"),
            "degradation": summary.get("degradation"),
            "work_coverage": summary.get("work_coverage"),
            "cost_preflight": summary.get("cost_preflight"),
        })
        outcome.artifacts = artifacts
        if summary.get("executor") != "v5-r8-fault-aware":
            outcome.status = "failed"
            outcome.error = "R8 executor evidence missing"
        if float(outcome.actual_cost_usd) > MAX_STRATEGY_COST_USD + 1e-12:
            outcome.status = "failed"
            outcome.error = "V5 actual cost exceeded the 0.25 USD per-task strategy cap"
        return outcome, market

    base._v5_strategy = r8_v5_strategy


def stage_d_gate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Apply Issue #64 Stage-D gates without granting production cutover."""
    gate = economy.economy_cutover_gate(records)
    blockers = set(gate.get("blockers") or [])
    blockers.discard("v5-quality-improvement-below-2-percent")
    blockers.discard("v5-cost-regression-above-policy")
    blockers.discard("v5-won-fewer-than-2-of-3-tasks")

    summaries = gate.get("summaries") if isinstance(gate.get("summaries"), Mapping) else {}
    v5 = summaries.get("v5_joint_graph") if isinstance(summaries.get("v5_joint_graph"), Mapping) else {}
    v3 = summaries.get("v3") if isinstance(summaries.get("v3"), Mapping) else {}
    v5_quality = float(v5.get("mean_blind_quality", 0.0))
    v3_quality = float(v3.get("mean_blind_quality", 0.0))
    v5_cost = float(v5.get("mean_cost_usd", 0.0))
    v3_cost = float(v3.get("mean_cost_usd", 0.0))

    if v5_quality + 1e-12 < v3_quality:
        blockers.add("v5-anonymous-quality-below-v3")
    if v5_cost > v3_cost + 1e-12:
        if v5_cost <= 0.0 or v3_cost <= 0.0:
            blockers.add("v5-higher-cost-without-verifiable-value-gain")
        else:
            v5_value = v5_quality / v5_cost
            v3_value = v3_quality / v3_cost
            if v5_value + 1e-12 < v3_value * 1.10:
                blockers.add("v5-cost-performance-improvement-below-10-percent")

    v5_rows = [row for row in records if row.get("strategy") == "v5_joint_graph"]
    degraded = 0
    for row in v5_rows:
        if float(row.get("actual_cost_usd", 0.0)) > MAX_STRATEGY_COST_USD + 1e-12:
            blockers.add(f"v5-per-task-cost-cap-exceeded:{row.get('task_id')}")
        artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), Mapping) else {}
        if artifacts.get("executor") != "v5-r8-fault-aware":
            blockers.add(f"v5-r8-executor-evidence-missing:{row.get('task_id')}")
        if artifacts.get("completion_mode") == "degraded":
            degraded += 1
    if degraded > 1:
        blockers.add("v5-degraded-results-exceeded-one-of-three")

    blockers = sorted(blockers)
    gate.update({
        "version": 2,
        "benchmark_type": "v5-r8-stage-d-live-blind-comparison",
        "stage_d_paid_blind_passed": not blockers,
        "canary_allowed": not blockers,
        "production_cutover_allowed": False,
        "production_cutover_reason": "20-task Canary and 30-task default observation are not complete",
        "v3_deletion_allowed": False,
        "v5_degraded_results": degraded,
        "blockers": blockers,
        "stage_d_policy": {
            "tasks": list(TASK_IDS),
            "strategies": ["v5_joint_graph", "v3"],
            "minimum_v5_success_rate": 1.0,
            "anonymous_quality_not_below_v3": True,
            "maximum_v5_degraded_results": 1,
            "per_strategy_task_hard_cap_usd": MAX_STRATEGY_COST_USD,
            "higher_cost_requires_cost_performance_improvement": 0.10,
            "production_switch_in_this_run": False,
            "v3_deletion_in_this_run": False,
        },
    })
    return gate


def install_r8_stage_d() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    economy._install_economy_controls()
    r6.install_r6_alignment()
    production.install()
    base.GraphLimits = _r8_limits
    economy.economy_cutover_gate = stage_d_gate
    economy.hardened.credit_preflight = credit_preflight
    os.environ.setdefault("V5_BENCHMARK_OUTPUT_ALLOWANCE_TOKENS", str(OUTPUT_ALLOWANCE_TOKENS))
    _annotate_v5_strategy()


def main(argv: Sequence[str] | None = None) -> int:
    install_r8_stage_d()
    return economy.hardened.main(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
