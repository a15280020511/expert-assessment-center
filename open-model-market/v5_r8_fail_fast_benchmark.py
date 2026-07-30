"""Two-phase R8 benchmark orchestration with paid-call fail-fast semantics."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import model_market as market
import v5_live_benchmark as base
import v5_live_benchmark_economy as economy
from artifact_manifest import write_manifest


def _unjudged_record(outcome: base.StrategyOutcome) -> dict[str, Any]:
    row = outcome.record()
    row.update(
        {
            "blind_quality_score": 0.0,
            "blind_judge_count": 0,
            "blind_judge_models": [],
            "blind_judge_providers": [],
            "blind_fatal_error": True,
            "blind_judge_disagreement_points": 100.0,
            "judging_skipped": True,
        }
    )
    return row


def _write_bundle(root: Path, bundle: Mapping[str, Any]) -> None:
    base._write_json(root / "v5-live-benchmark-results.json", bundle)
    (root / "v5-live-benchmark-summary.md").write_text(
        base._summary_markdown(bundle),
        encoding="utf-8",
    )
    write_manifest(root)


def run_benchmark(config_path: str | Path, suite_path: str | Path, output_dir: str | Path) -> int:
    """Run all V5 tasks first; do not start V3 or judges unless all V5 pass."""
    if not os.getenv("OPENROUTER_API_KEY"):
        raise base.LiveBenchmarkError("OPENROUTER_API_KEY is not set")
    config = base._load_json(config_path)
    suite = base._load_json(suite_path)
    requested = [str(value) for value in config.get("task_ids", [])]
    by_id = {
        str(row.get("task_id")): row
        for row in suite.get("tasks", [])
        if isinstance(row, Mapping) and row.get("task_id")
    }
    tasks = [by_id[value] for value in requested if value in by_id]
    if len(tasks) != len(requested):
        raise base.LiveBenchmarkError("one or more configured tasks are absent from the suite")

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    ledger = base.GlobalLedger(float(config["max_cost_usd"]), int(config["max_calls"]))
    strategy_cap = min(float(config["max_strategy_cost_usd"]), ledger.max_cost_usd)
    catalog_run = market.build_run_config(
        base._namespace(base._task_text(tasks[0]), root / "catalog", ranking_limit=50)
    )
    models, catalog_source = market.fetch_catalog(catalog_run)
    endpoint_cache: dict[str, Mapping[str, Any]] = {}
    v5_phase: list[tuple[Mapping[str, Any], Path, base.StrategyOutcome, Mapping[str, Any]]] = []
    task_bundles: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []

    # Phase 1: V5 only. This is the paid stop-loss boundary.
    for task in tasks:
        task_id = str(task["task_id"])
        task_root = root / "tasks" / task_id
        task_root.mkdir(parents=True, exist_ok=True)
        try:
            outcome, market_bundle = base._v5_strategy(
                task,
                task_root / "v5_joint_graph",
                ledger,
                models,
                endpoint_cache,
                min(strategy_cap, max(0.10, ledger.remaining_cost())),
            )
        except base.BenchmarkLimitExceeded as exc:
            outcome = base.StrategyOutcome(
                task_id=task_id,
                strategy="v5_joint_graph",
                status="failed",
                answer=None,
                actual_cost_usd=0.0,
                latency_seconds=0.0,
                call_count=0,
                models=[],
                providers=[],
                safety_failure=False,
                error=str(exc),
            )
            market_bundle = {}
        except Exception as exc:  # noqa: BLE001
            outcome = base.StrategyOutcome(
                task_id=task_id,
                strategy="v5_joint_graph",
                status="failed",
                answer=None,
                actual_cost_usd=0.0,
                latency_seconds=0.0,
                call_count=0,
                models=[],
                providers=[],
                safety_failure=False,
                error=str(exc),
            )
            market_bundle = {}
        v5_phase.append((task, task_root, outcome, market_bundle))
        if outcome.status != "success":
            records = [_unjudged_record(row[2]) for row in v5_phase]
            cutover = economy.economy_cutover_gate(records)
            cutover.setdefault("blockers", []).append(
                f"v5-fail-fast-before-v3-and-judges:{task_id}"
            )
            cutover["blockers"] = sorted(set(cutover["blockers"]))
            cutover["stage_d_paid_blind_passed"] = False
            cutover["canary_allowed"] = False
            cutover["production_cutover_allowed"] = False
            cutover["v3_deletion_allowed"] = False
            task_bundles.append(
                {
                    "task_id": task_id,
                    "status": "v5_fail_fast_stopped",
                    "outcomes": [outcome.record()],
                    "v3_executed": False,
                    "blind_judges_executed": False,
                    "ledger_after_stop": ledger.snapshot(),
                }
            )
            bundle = {
                "version": 3,
                "mode": "v5-r8-stage-d-two-phase-fail-fast",
                "benchmark_id": config["benchmark_id"],
                "status": "v5_fail_fast_stopped",
                "error": outcome.error or "V5 did not produce a valid deliverable",
                "catalog_source": catalog_source,
                "tasks_requested": len(tasks),
                "tasks_completed": 0,
                "v5_tasks_attempted": len(v5_phase),
                "strategies": ["v5_joint_graph", "v3"],
                "records": records,
                "task_bundles": task_bundles,
                "ledger": ledger.snapshot(),
                "cutover_gate": cutover,
                "v3_executed": False,
                "blind_judges_executed": False,
                "v3_and_judges_skipped_due_to_v5_failure": True,
                "fail_fast_task_id": task_id,
                "production_entrypoint_changed": False,
                "v3_deleted": False,
            }
            _write_bundle(root, bundle)
            return 2

    # Phase 2: only after all three V5 results are independently deliverable.
    for task, task_root, v5_outcome, market_bundle in v5_phase:
        task_id = str(task["task_id"])
        outcomes = [v5_outcome]
        try:
            v3_outcome = base._v3_strategy(
                task,
                task_root / "v3",
                ledger,
                min(strategy_cap, max(0.10, ledger.remaining_cost())),
            )
            outcomes.append(v3_outcome)
            direct_run = market.build_run_config(
                base._namespace(base._task_text(task), task_root / "judge", ranking_limit=50)
            )
            scores, evaluation = base._evaluate_task(
                direct_run,
                task,
                outcomes,
                market_bundle,
                ledger,
                task_root,
            )
            for outcome in outcomes:
                row = outcome.record()
                row["blind_quality_score"] = scores.get(outcome.strategy, 0.0)
                row["blind_judge_count"] = evaluation["judge_count"]
                row["blind_judge_models"] = evaluation["judge_models"]
                row["blind_judge_providers"] = evaluation["judge_providers"]
                row["blind_fatal_error"] = bool(
                    evaluation["fatal_by_strategy"].get(outcome.strategy)
                )
                row["blind_judge_disagreement_points"] = float(
                    evaluation["disagreement_points_by_strategy"].get(
                        outcome.strategy, 100.0
                    )
                )
                records.append(row)
            task_bundle = {
                "task_id": task_id,
                "domain": task.get("domain"),
                "outcomes": [outcome.record() for outcome in outcomes],
                "blind_scores": scores,
                "evaluation": evaluation,
                "ledger_after_task": ledger.snapshot(),
                "v3_executed": True,
                "blind_judges_executed": True,
            }
            task_bundles.append(task_bundle)
            base._write_json(task_root / "task-benchmark-result.json", task_bundle)
        except base.BenchmarkLimitExceeded as exc:
            status = "budget_or_call_limit_exceeded"
            error = str(exc)
            break
        except Exception as exc:  # noqa: BLE001
            status = "technical_failure"
            error = f"task {task_id}: {exc}"
            task_bundles.append(
                {
                    "task_id": task_id,
                    "status": status,
                    "error": str(exc),
                    "outcomes": [outcome.record() for outcome in outcomes],
                }
            )
            break
    else:
        status = "success"
        error = ""

    cutover = economy.economy_cutover_gate(records)
    bundle = {
        "version": 3,
        "mode": "v5-r8-stage-d-two-phase-fail-fast",
        "benchmark_id": config["benchmark_id"],
        "status": status,
        "error": error or None,
        "catalog_source": catalog_source,
        "tasks_requested": len(tasks),
        "tasks_completed": len({row.get("task_id") for row in records}),
        "v5_tasks_attempted": len(v5_phase),
        "strategies": ["v5_joint_graph", "v3"],
        "records": records,
        "task_bundles": task_bundles,
        "ledger": ledger.snapshot(),
        "cutover_gate": cutover,
        "v3_executed": any(row.get("strategy") == "v3" for row in records),
        "blind_judges_executed": any(
            int(row.get("blind_judge_count", 0) or 0) > 0 for row in records
        ),
        "v3_and_judges_skipped_due_to_v5_failure": False,
        "production_entrypoint_changed": False,
        "v3_deleted": False,
    }
    _write_bundle(root, bundle)
    return 0 if status == "success" else 2
