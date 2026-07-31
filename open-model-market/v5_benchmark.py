"""Deterministic planning diagnostics for the V5 dynamic execution graph."""
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


def _greedy(
    required: set[str],
    candidates: Sequence[Mapping[str, Any]],
    key: Any,
    maximum_nodes: int = 16,
) -> list[Mapping[str, Any]]:
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


def _single_model(
    required: set[str],
    candidates: Sequence[Mapping[str, Any]],
    strongest: bool,
) -> list[Mapping[str, Any]]:
    by_model: dict[str, list[Mapping[str, Any]]] = {}
    for row in candidates:
        by_model.setdefault(str(row.get("model") or ""), []).append(row)
    plans: list[list[Mapping[str, Any]]] = []
    for rows in by_model.values():
        plan = _greedy(
            required,
            rows,
            (lambda row, gain: (-gain, -float(row.get("estimated_quality", 0.0)), float(row.get("estimated_cost", 0.0))))
            if strongest
            else (lambda row, gain: (-gain, float(row.get("estimated_cost", 0.0)), -float(row.get("estimated_quality", 0.0)))),
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
    rng = random.Random(random_seed)
    randomized = list(candidates)
    rng.shuffle(randomized)
    random_plan = _greedy(required, randomized, lambda row, gain: (-gain, rng.random()))
    cheapest_feasible = _greedy(
        required,
        candidates,
        lambda row, gain: (-gain, float(row["estimated_cost"]), -float(row["estimated_quality"])),
    )
    strategies = {
        "v5_joint_graph": _metrics(selected, required, meta["copies_by_work"]),
        "strongest_single_model": _metrics(strongest_single, required, meta["copies_by_work"]),
        "lowest_price_single_model": _metrics(cheapest_single, required, meta["copies_by_work"]),
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
        "benchmark_type": "deterministic-planning-diagnostic",
        "runtime_policy": "v5-only-no-alternate-runtime",
        "selected_interpretation": interpretation_id,
        "required_coverage_keys": sorted(required),
        "strategies": strategies,
        "v5_pareto_dominates": dominated,
        "planning_gate_passed": bool(v5["feasible"] and v5["coverage_ratio"] == 1.0),
        "execution_graph_summary": {
            "estimated_quality": graph["estimated_quality"],
            "estimated_total_cost": graph["estimated_total_cost"],
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
            "stage_count": len(graph["execution_stages"]),
        },
    }


def write_benchmark(path: str | Path, value: Mapping[str, Any]) -> None:
    Path(path).write_text(json.dumps(dict(value), ensure_ascii=False, indent=2), encoding="utf-8")
