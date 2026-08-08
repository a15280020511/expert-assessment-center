"""OR-Tools assignment for current generated roles and current-signal scoring."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

from v5_runtime_role_scoring import (
    RuntimeRoleScoringError,
    SCHEMA_VERSION as ROLE_SCORING_SCHEMA_VERSION,
    build_runtime_recovery_metrics,
    build_runtime_role_metrics,
)

SCHEMA_VERSION = "current-role-ortools-assignment-1"


class RuntimeRoleAssignmentError(RuntimeError):
    """Raised when the current finite assignment cannot be constructed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _annotate(row: Mapping[str, Any], metric: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(row)
    value.update(
        {
            "estimated_task_cost_usd": float(metric.get("estimated_task_cost_usd") or 0.0),
            "task_adaptive_objective_score": int(metric.get("objective_score") or 0),
            "task_adaptive_base_objective_score": int(metric.get("base_objective_score") or 0),
            "task_adaptive_ranks": dict(metric.get("ranks") or {}),
            "task_adaptive_weights": dict(metric.get("weights") or {}),
            "task_adaptive_weight_strengths": dict(metric.get("weight_strengths") or {}),
            "task_adaptive_role_tokens": dict(metric.get("role_tokens") or {}),
            "task_adaptive_capacity_compatible": bool(metric.get("compatible", True)),
            "capacity_shortfall": float(metric.get("capacity_shortfall") or 0.0),
            "capacity_shortfall_penalty": int(metric.get("capacity_shortfall_penalty") or 0),
            "capacity_is_hard_gate": False,
            "marginal_cost_per_quality": float(metric.get("marginal_cost_per_quality") or 0.0),
            "fixed_business_weight_coefficients_used": False,
            "tool_use_forbidden": True,
            "tools_allowed": False,
        }
    )
    return value


def _solver_profile(
    profile: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    roles: Sequence[Mapping[str, Any]],
    recovery_count: int,
) -> dict[str, Any]:
    problem_cells = max(1, len(candidates) * (len(roles) + 1))
    structural_scale = max(1, int(profile.get("work_unit_count") or len(roles) or 1))
    recovery_scale = max(1, recovery_count + 1)
    max_time = max(1.0, math.log2(problem_cells + 1) + math.log2(structural_scale + recovery_scale))
    seed_material = {
        "profile": profile,
        "candidate_ids": [str(row.get("model") or "") for row in candidates],
        "roles": [
            {
                "role_id": row.get("role_id"),
                "assigned_work_units": row.get("assigned_work_units"),
                "depends_on_role_ids": row.get("depends_on_role_ids"),
            }
            for row in roles
        ],
        "recovery_count": recovery_count,
    }
    seed = int(hashlib.sha256(_canonical(seed_material)).hexdigest()[:8], 16) % 2_147_483_647
    return {
        "problem_cells": problem_cells,
        "max_time_in_seconds": round(max_time, 6),
        "random_seed": seed,
        "num_search_workers": 1,
        "single_worker_reason": "deterministic-audit-reproducibility",
        "solver_effort_source": "current-problem-size-and-current-graph",
        "fixed_business_solver_time_used": False,
    }


def _recovery_rows(
    candidates: Sequence[Mapping[str, Any]],
    recovery_metrics: Mapping[str, Mapping[str, Any]],
    selected_ids: set[str],
    recovery_count: int,
) -> list[dict[str, Any]]:
    ranked = sorted(
        (row for row in candidates if str(row.get("model") or "") not in selected_ids),
        key=lambda row: (
            int(recovery_metrics[str(row["model"])].get("objective_score") or 0),
            float(recovery_metrics[str(row["model"])].get("estimated_task_cost_usd") or 0.0),
            str(row["model"]),
        ),
    )
    result: list[dict[str, Any]] = []
    for index, source in enumerate(ranked[:recovery_count], 1):
        model_id = str(source["model"])
        row = _annotate(source, recovery_metrics[model_id])
        row["slot"] = index
        row["warm_recovery_priority"] = index
        row["recovery_resilience"] = {
            "selection_source": "current-heaviest-role-normalized-objective",
            "hard_company_diversity_constraint": False,
            "provider_constraint": False,
            "capacity_hard_gate": False,
            "cross_task_history_used": False,
        }
        result.append(row)
    return result


def solve_runtime_roles(
    candidates: list[dict[str, Any]],
    profile: Mapping[str, Any],
    roles: Sequence[Mapping[str, Any]],
    recovery_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not roles:
        raise RuntimeRoleAssignmentError("current role plan is empty")
    if len(candidates) < len(roles) + int(recovery_count):
        raise RuntimeRoleAssignmentError("current candidate inventory is smaller than current role plus recovery demand")
    try:
        metrics_by_role = [build_runtime_role_metrics(candidates, profile, role) for role in roles]
        recovery_metrics, recovery_shape = build_runtime_recovery_metrics(candidates, profile, roles)
    except RuntimeRoleScoringError as exc:
        raise RuntimeRoleAssignmentError(str(exc)) from exc

    model = cp_model.CpModel()
    active = {
        (candidate_index, role_index): model.new_bool_var(f"active_{candidate_index}_{role_index}")
        for candidate_index in range(len(candidates))
        for role_index in range(len(roles))
    }
    recovery = {index: model.new_bool_var(f"recovery_{index}") for index in range(len(candidates))}
    for role_index in range(len(roles)):
        model.add(sum(active[index, role_index] for index in range(len(candidates))) == 1)
    for index in range(len(candidates)):
        model.add(sum(active[index, role] for role in range(len(roles))) + recovery[index] <= 1)
    model.add(sum(recovery.values()) == int(recovery_count))

    tie_base = max(2, len(candidates) * max(1, len(roles)) + 1)
    terms: list[Any] = []
    for index, row in enumerate(candidates):
        model_id = str(row["model"])
        for role_index in range(len(roles)):
            metric = metrics_by_role[role_index][model_id]
            tie = index * max(1, len(roles)) + role_index
            terms.append((int(metric.get("objective_score") or 0) * tie_base + tie) * active[index, role_index])
        recovery_metric = recovery_metrics[model_id]
        terms.append((int(recovery_metric.get("objective_score") or 0) * tie_base + index) * recovery[index])
    model.minimize(sum(terms))

    solver_profile = _solver_profile(profile, candidates, roles, recovery_count)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = int(solver_profile["num_search_workers"])
    solver.parameters.random_seed = int(solver_profile["random_seed"])
    solver.parameters.max_time_in_seconds = float(solver_profile["max_time_in_seconds"])
    status = solver.solve(model)
    accepted = {cp_model.OPTIMAL, cp_model.FEASIBLE}

    def audit(*, fallback: bool) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "optimizer": "ortools-cp-sat-with-current-signal-heuristic-fallback" if fallback else "ortools-cp-sat",
            "solver_status": solver.status_name(status),
            "optimality_proven": status == cp_model.OPTIMAL,
            "fallback_used": fallback,
            "dynamic_solver_profile": solver_profile,
            "role_scoring_schema_version": ROLE_SCORING_SCHEMA_VERSION,
            "role_metric_mode": "current-role-current-task-normalized-signals",
            "recovery_metric_mode": "heaviest-current-generated-role-current-signal-normalization",
            "recovery_reference_role": dict(recovery_shape),
            "metric_role_adapter_used": False,
            "fixed_metric_role_grammar_used": False,
            "semantic_role_routing_used": False,
            "fixed_business_weight_coefficients_used": False,
            "fixed_business_solver_time_used": False,
        }
        if not fallback:
            value.update(
                {
                    "objective_value": float(solver.objective_value),
                    "best_objective_bound": float(solver.best_objective_bound),
                    "wall_time_seconds": round(float(solver.wall_time), 6),
                }
            )
        return value

    if status not in accepted:
        used: set[str] = set()
        selected: list[dict[str, Any]] = []
        for role_index, role in enumerate(roles):
            ranked = sorted(
                candidates,
                key=lambda row: (
                    int(metrics_by_role[role_index][str(row["model"])].get("objective_score") or 0),
                    float(metrics_by_role[role_index][str(row["model"])].get("estimated_task_cost_usd") or 0.0),
                    str(row["model"]),
                ),
            )
            source = next(row for row in ranked if str(row["model"]) not in used)
            used.add(str(source["model"]))
            selected.append({**_annotate(source, metrics_by_role[role_index][str(source["model"])]), **dict(role), "slot": role_index + 1})
        recoveries = _recovery_rows(candidates, recovery_metrics, used, recovery_count)
        return selected, recoveries, audit(fallback=True)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for role_index, role in enumerate(roles):
        matches = [index for index in range(len(candidates)) if solver.value(active[index, role_index]) == 1]
        source = candidates[matches[0]]
        selected_ids.add(str(source["model"]))
        selected.append({**_annotate(source, metrics_by_role[role_index][str(source["model"])]), **dict(role), "slot": role_index + 1})

    recoveries: list[dict[str, Any]] = []
    for index, row in enumerate(candidates):
        if solver.value(recovery[index]) == 1:
            value = _annotate(row, recovery_metrics[str(row["model"])])
            recoveries.append(value)
    recoveries.sort(
        key=lambda row: (
            int(row.get("task_adaptive_objective_score") or 0),
            float(row.get("estimated_task_cost_usd") or 0.0),
            str(row.get("model") or ""),
        )
    )
    for index, row in enumerate(recoveries, 1):
        row["slot"] = index
        row["warm_recovery_priority"] = index
        row["recovery_resilience"] = {
            "selection_source": "current-heaviest-role-normalized-objective",
            "hard_company_diversity_constraint": False,
            "provider_constraint": False,
            "capacity_hard_gate": False,
            "cross_task_history_used": False,
        }
    return selected, recoveries, audit(fallback=False)


__all__ = ["RuntimeRoleAssignmentError", "SCHEMA_VERSION", "solve_runtime_roles"]
