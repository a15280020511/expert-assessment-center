"""Deterministic planning baselines and production-cutover evidence gate for V5."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Mapping, Sequence


def _coverage(candidate: Mapping[str, Any]) -> set[str]:
    return {str(x) for x in candidate.get("coverage_keys", [])}


def _metrics(
    rows: Sequence[Mapping[str, Any]],
    required: set[str],
    copies_by_work: Mapping[str, Any],
) -> dict[str, Any]:
    covered = set().union(*(_coverage(row) for row in rows)) if rows else set()
    violations: list[str] = []
    for work_id, copies_raw in copies_by_work.items():
        copies = int(copies_raw)
        if copies < 2:
            continue
        selected_by_copy = []
        for copy_index in range(copies):
            key = f"{work_id}#{copy_index}"
            matches = [row for row in rows if key in _coverage(row)]
            if len(matches) != 1:
                violations.append(f"{work_id}:copy-{copy_index}-selection-count={len(matches)}")
                continue
            selected_by_copy.append(matches[0])
        if len(selected_by_copy) == copies:
            models = [str(row.get("model") or "") for row in selected_by_copy]
            endpoints = [str(row.get("provider_endpoint") or "") for row in selected_by_copy]
            if len(set(models)) != copies:
                violations.append(f"{work_id}:independent-copies-reuse-model")
            if len(set(endpoints)) != copies:
                violations.append(f"{work_id}:independent-copies-reuse-endpoint")
    quality = sum(float(row.get("estimated_quality", 0.0)) for row in rows) / max(1, len(rows))
    return {
        "feasible": required <= covered and not violations,
        "coverage_ratio": round(len(required & covered) / max(1, len(required)), 6),
        "estimated_quality": round(quality, 6),
        "estimated_cost_usd": round(sum(float(row.get("estimated_cost", 0.0)) for row in rows), 8),
        "node_count": len(rows),
        "hard_constraint_violations": violations,
        "models": sorted({str(row.get("model") or "") for row in rows}),
        "provider_endpoints": sorted({str(row.get("provider_endpoint") or "") for row in rows}),
    }


def _greedy(required: set[str], candidates: Sequence[Mapping[str, Any]], key, maximum_nodes: int = 16) -> list[Mapping[str, Any]]:
    remaining = set(required)
    selected: list[Mapping[str, Any]] = []
    pool = list(candidates)
    while remaining and len(selected) < maximum_nodes:
        useful = [row for row in pool if _coverage(row) & remaining]
        if not useful:
            break
        useful.sort(key=lambda row: key(row, len(_coverage(row) & remaining)))
        chosen = useful[0]
        selected.append(chosen)
        remaining -= _coverage(chosen)
        pool = [row for row in pool if row.get("candidate_id") != chosen.get("candidate_id")]
    return selected


def _single_model(required: set[str], candidates: Sequence[Mapping[str, Any]], strongest: bool) -> list[Mapping[str, Any]]:
    by_model: dict[str, list[Mapping[str, Any]]] = {}
    for row in candidates:
        by_model.setdefault(str(row.get("model") or ""), []).append(row)
    plans: list[list[Mapping[str, Any]]] = []
    for rows in by_model.values():
        plan = _greedy(
            required,
            rows,
            (lambda row, gain: (-gain, -float(row.get("estimated_quality", 0.0)), float(row.get("estimated_cost", 0.0))))
            if strongest else
            (lambda row, gain: (-gain, float(row.get("estimated_cost", 0.0)), -float(row.get("estimated_quality", 0.0)))),
        )
        if plan and required <= set().union(*(_coverage(row) for row in plan)):
            plans.append(plan)
    if not plans:
        return []
    if strongest:
        return max(
            plans,
            key=lambda rows: (
                sum(float(row.get("estimated_quality", 0.0)) for row in rows) / len(rows),
                -sum(float(row.get("estimated_cost", 0.0)) for row in rows),
            ),
        )
    return min(
        plans,
        key=lambda rows: (
            sum(float(row.get("estimated_cost", 0.0)) for row in rows),
            -sum(float(row.get("estimated_quality", 0.0)) for row in rows) / len(rows),
        ),
    )


def planning_benchmark(planner_bundle: Mapping[str, Any], *, random_seed: int = 20260730) -> dict[str, Any]:
    optimization = planner_bundle["optimization"]
    graph = optimization["execution_graph"]
    interpretation_id = optimization["selected_interpretation"]
    meta = planner_bundle["candidate_graph"]["interpretations"][interpretation_id]
    required = {
        f"{work_id}#{copy_index}"
        for work_id, copies in meta["copies_by_work"].items()
        for copy_index in range(int(copies))
    }
    candidates = [
        row
        for row in planner_bundle["candidate_graph"]["candidates"]
        if row["interpretation_id"] == interpretation_id
    ]
    selected_ids = set(optimization["selected_candidate_ids"])
    selected = [row for row in candidates if row["candidate_id"] in selected_ids]
    strongest_single = _single_model(required, candidates, strongest=True)
    cheapest_single = _single_model(required, candidates, strongest=False)
    fixed = _greedy(
        required,
        candidates,
        lambda row, gain: (-gain, -float(row["estimated_quality"]), float(row["estimated_cost"])),
        maximum_nodes=4,
    )
    rng = random.Random(random_seed)
    randomized = list(candidates)
    rng.shuffle(randomized)
    random_plan = _greedy(required, randomized, lambda row, gain: (-gain, rng.random()), maximum_nodes=16)
    cheapest_feasible = _greedy(
        required,
        candidates,
        lambda row, gain: (-gain, float(row["estimated_cost"]), -float(row["estimated_quality"])),
        maximum_nodes=16,
    )
    strategies = {
        "v5_joint_graph": _metrics(selected, required, meta["copies_by_work"]),
        "strongest_single_model": _metrics(strongest_single, required, meta["copies_by_work"]),
        "lowest_price_single_model": _metrics(cheapest_single, required, meta["copies_by_work"]),
        "fixed_3_plus_1": _metrics(fixed, required, meta["copies_by_work"]),
        "v3_compatibility_baseline": _metrics(fixed, required, meta["copies_by_work"]),
        "random_feasible": _metrics(random_plan, required, meta["copies_by_work"]),
        "lowest_cost_feasible": _metrics(cheapest_feasible, required, meta["copies_by_work"]),
    }
    v5 = strategies["v5_joint_graph"]
    dominated = []
    for name, row in strategies.items():
        if name == "v5_joint_graph" or not row["feasible"]:
            continue
        if v5["estimated_quality"] >= row["estimated_quality"] and v5["estimated_cost_usd"] <= row["estimated_cost_usd"]:
            dominated.append(name)
    return {
        "version": 5,
        "benchmark_type": "deterministic-planning-surrogate",
        "selected_interpretation": interpretation_id,
        "required_coverage_keys": sorted(required),
        "strategies": strategies,
        "v5_pareto_dominates": dominated,
        "planning_gate_passed": bool(v5["feasible"] and v5["coverage_ratio"] == 1.0),
        "production_cutover_allowed": False,
        "production_cutover_blocker": "A multi-task live outcome benchmark is required; planning estimates alone cannot replace V3.",
        "reported_v3_baseline_is_structural_proxy": True,
        "execution_graph_summary": {
            "estimated_quality": graph["estimated_quality"],
            "estimated_total_cost": graph["estimated_total_cost"],
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
            "stage_count": len(graph["execution_stages"]),
        },
    }


def _judge_evidence_valid(row: Mapping[str, Any]) -> bool:
    models = {str(value) for value in row.get("blind_judge_models", []) if str(value)}
    providers = {str(value) for value in row.get("blind_judge_providers", []) if str(value)}
    return bool(
        int(row.get("blind_judge_count", 0) or 0) >= 2
        and len(models) >= 2
        and len(providers) >= 2
        and float(row.get("blind_judge_disagreement_points", 100.0) or 100.0) <= 35.0
    )


def live_cutover_gate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate complete live blind evidence; never infer outcome quality from planner scores."""
    required_strategies = {
        "v5_joint_graph",
        "v3",
        "strongest_single_model",
        "lowest_price_single_model",
        "fixed_3_plus_1",
        "random_feasible",
    }
    tasks = sorted({str(row.get("task_id") or "") for row in records if row.get("task_id")})
    expected_tasks = set(tasks)
    by_strategy: dict[str, list[Mapping[str, Any]]] = {}
    for row in records:
        by_strategy.setdefault(str(row.get("strategy") or ""), []).append(row)
    missing = sorted(required_strategies - set(by_strategy))
    blockers: list[str] = []
    if len(tasks) < 5:
        blockers.append("fewer-than-5-independent-tasks")
    if missing:
        blockers.append("missing-strategies:" + ",".join(missing))

    summaries: dict[str, Any] = {}
    for strategy in sorted(required_strategies | set(by_strategy)):
        rows = by_strategy.get(strategy, [])
        row_tasks = [str(row.get("task_id") or "") for row in rows if row.get("task_id")]
        unique_tasks = set(row_tasks)
        missing_tasks = sorted(expected_tasks - unique_tasks)
        duplicate_tasks = sorted({task_id for task_id in row_tasks if row_tasks.count(task_id) > 1})
        invalid_judging = [
            str(row.get("task_id") or "")
            for row in rows
            if not _judge_evidence_valid(row)
        ]
        safety_failures = sum(bool(row.get("safety_failure")) for row in rows)
        fatal_errors = sum(bool(row.get("blind_fatal_error")) for row in rows)
        successes = [
            row
            for row in rows
            if row.get("status") == "success"
            and not row.get("safety_failure")
            and not row.get("blind_fatal_error")
            and _judge_evidence_valid(row)
        ]
        summaries[strategy] = {
            "task_count": len(rows),
            "unique_task_count": len(unique_tasks),
            "missing_tasks": missing_tasks,
            "duplicate_tasks": duplicate_tasks,
            "success_rate": round(len(successes) / max(1, len(expected_tasks)), 6),
            "mean_blind_quality": round(
                sum(float(row.get("blind_quality_score", 0.0)) for row in successes) / max(1, len(successes)),
                6,
            ),
            "mean_cost_usd": round(
                sum(float(row.get("actual_cost_usd", 0.0)) for row in successes) / max(1, len(successes)),
                8,
            ),
            "mean_latency_seconds": round(
                sum(float(row.get("latency_seconds", 0.0)) for row in successes) / max(1, len(successes)),
                6,
            ),
            "mean_judge_disagreement_points": round(
                sum(float(row.get("blind_judge_disagreement_points", 100.0)) for row in rows) / max(1, len(rows)),
                6,
            ),
            "invalid_judge_evidence_tasks": sorted(set(invalid_judging)),
            "safety_failures": safety_failures,
            "blind_fatal_errors": fatal_errors,
        }
        if strategy in required_strategies:
            if missing_tasks:
                blockers.append(f"{strategy}:missing-tasks:" + ",".join(missing_tasks))
            if duplicate_tasks:
                blockers.append(f"{strategy}:duplicate-task-records:" + ",".join(duplicate_tasks))
            if invalid_judging:
                blockers.append(f"{strategy}:invalid-independent-blind-judging")

    v5 = summaries.get("v5_joint_graph", {})
    v3 = summaries.get("v3", {})
    if v5.get("safety_failures", 1):
        blockers.append("v5-safety-failure")
    if v5.get("blind_fatal_errors", 1):
        blockers.append("v5-blind-fatal-error")
    if float(v5.get("success_rate", 0.0)) < 0.80:
        blockers.append("v5-success-rate-below-80-percent")
    if float(v5.get("success_rate", 0.0)) < float(v3.get("success_rate", 1.0)):
        blockers.append("v5-success-rate-below-v3")
    if float(v5.get("mean_blind_quality", 0.0)) < float(v3.get("mean_blind_quality", 1.0)) * 1.02:
        blockers.append("v5-quality-improvement-below-2-percent")
    v3_cost = float(v3.get("mean_cost_usd", 0.0))
    if float(v5.get("mean_cost_usd", 1e9)) > max(v3_cost * 1.25, v3_cost + 0.02):
        blockers.append("v5-cost-regression-above-policy")
    if float(v5.get("mean_judge_disagreement_points", 100.0)) > 20.0:
        blockers.append("v5-judge-disagreement-above-20-points")

    blockers = sorted(set(blockers))
    return {
        "version": 5,
        "benchmark_type": "live-blind-outcome-comparison",
        "task_ids": tasks,
        "summaries": summaries,
        "missing_strategies": missing,
        "production_cutover_allowed": not blockers,
        "blockers": blockers,
        "cutover_policy": {
            "minimum_tasks": 5,
            "required_task_coverage_per_strategy": "all benchmark tasks",
            "minimum_independent_judges_per_result": 2,
            "minimum_distinct_judge_models": 2,
            "minimum_distinct_judge_providers": 2,
            "maximum_per_result_judge_disagreement_points": 35,
            "maximum_mean_v5_judge_disagreement_points": 20,
            "minimum_v5_success_rate": 0.80,
            "minimum_quality_improvement_over_v3": 0.02,
            "maximum_relative_cost_regression": 0.25,
            "safety_failures_allowed": 0,
            "blind_fatal_errors_allowed": 0,
        },
    }


def write_benchmark(path: str | Path, value: Mapping[str, Any]) -> None:
    Path(path).write_text(json.dumps(dict(value), ensure_ascii=False, indent=2), encoding="utf-8")
