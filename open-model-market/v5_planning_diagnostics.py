"""Constraint-faithful diagnostics for V5 planning failures.

The production optimizer must fail closed, but a bare CP-SAT ``INFEASIBLE`` is
not actionable. These helpers solve each interpretation without ticket ceilings
to identify its minimum node count and minimum effective expected cost, then
compare those minima with the approved node and risk-adjusted cost budgets.
No model call is performed.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

from execution_graph import GraphLimits
from v5_planner import CandidateNode
from v5_value_optimizer import CALL_OVERHEAD_USD

COST_SCALE = 1_000_000


def _candidate_objects(bundle: Mapping[str, Any]) -> list[CandidateNode]:
    return [CandidateNode(**row) for row in bundle.get("candidates", [])]


def _coverage_keys(meta: Mapping[str, Any]) -> list[str]:
    return [
        f"{work_id}#{copy_index}"
        for work_id, copies in dict(meta.get("copies_by_work", {})).items()
        for copy_index in range(int(copies))
    ]


def _effective_cost_scaled(candidate: CandidateNode) -> int:
    expected_recovery = candidate.estimated_cost * max(
        0.0, min(1.0, candidate.failure_probability)
    )
    expected_recovery_overhead = CALL_OVERHEAD_USD * max(
        0.0, min(1.0, candidate.failure_probability)
    )
    value = (
        candidate.estimated_cost
        + expected_recovery
        + CALL_OVERHEAD_USD
        + expected_recovery_overhead
    )
    return max(1, int(round(value * COST_SCALE)))


def _model_independence_required(
    meta: Mapping[str, Any],
    candidates: Sequence[CandidateNode],
    work_id: str,
) -> bool:
    policies = meta.get("independence_policy_by_work", {})
    if isinstance(policies, Mapping):
        policy = policies.get(work_id, {})
        if isinstance(policy, Mapping) and policy.get("different_model_required"):
            return True
    return any(work_id in candidate.independence_groups for candidate in candidates)


def _build_model(
    interpretation_id: str,
    meta: Mapping[str, Any],
    candidates: Sequence[CandidateNode],
) -> tuple[cp_model.CpModel, list[cp_model.IntVar], list[int], list[str]]:
    scoped = [
        candidate
        for candidate in candidates
        if candidate.interpretation_id == interpretation_id
    ]
    model = cp_model.CpModel()
    variables = [model.NewBoolVar(f"candidate_{index}") for index in range(len(scoped))]
    keys = _coverage_keys(meta)
    missing: list[str] = []
    for key in keys:
        terms = [
            variables[index]
            for index, candidate in enumerate(scoped)
            if key in candidate.coverage_keys
        ]
        if not terms:
            missing.append(key)
        else:
            model.Add(sum(terms) == 1)

    for work_id, copies_raw in dict(meta.get("copies_by_work", {})).items():
        copies = int(copies_raw)
        if copies < 2 or not _model_independence_required(meta, scoped, str(work_id)):
            continue
        by_copy: dict[int, list[int]] = {}
        for copy_index in range(copies):
            key = f"{work_id}#{copy_index}"
            by_copy[copy_index] = [
                index
                for index, candidate in enumerate(scoped)
                if key in candidate.coverage_keys
            ]
        for left_copy in range(copies):
            for right_copy in range(left_copy + 1, copies):
                for left in by_copy[left_copy]:
                    for right in by_copy[right_copy]:
                        if scoped[left].model == scoped[right].model:
                            model.Add(variables[left] + variables[right] <= 1)

    costs = [_effective_cost_scaled(candidate) for candidate in scoped]
    return model, variables, costs, missing


def _solve_minimum(
    interpretation_id: str,
    meta: Mapping[str, Any],
    candidates: Sequence[CandidateNode],
) -> dict[str, Any]:
    model, variables, costs, missing = _build_model(
        interpretation_id, meta, candidates
    )
    coverage_counts = {
        key: sum(
            1
            for candidate in candidates
            if candidate.interpretation_id == interpretation_id
            and key in candidate.coverage_keys
        )
        for key in _coverage_keys(meta)
    }
    if missing:
        return {
            "interpretation_id": interpretation_id,
            "feasible_without_ticket_limits": False,
            "failure_reason": "missing_coverage_candidates",
            "missing_coverage_keys": missing,
            "coverage_candidate_counts": coverage_counts,
            "minimum_required_nodes": None,
            "minimum_effective_expected_cost_usd": None,
        }

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    model.Minimize(sum(variables))
    status = solver.Solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        return {
            "interpretation_id": interpretation_id,
            "feasible_without_ticket_limits": False,
            "failure_reason": "independence_or_exact_coverage_conflict",
            "missing_coverage_keys": [],
            "coverage_candidate_counts": coverage_counts,
            "minimum_required_nodes": None,
            "minimum_effective_expected_cost_usd": None,
        }

    minimum_nodes = int(sum(solver.Value(variable) for variable in variables))
    model.Add(sum(variables) == minimum_nodes)
    model.Minimize(sum(cost * variable for cost, variable in zip(costs, variables)))
    cost_solver = cp_model.CpSolver()
    cost_solver.parameters.max_time_in_seconds = 10.0
    cost_solver.parameters.num_search_workers = 1
    cost_solver.parameters.random_seed = 0
    cost_status = cost_solver.Solve(model)
    minimum_cost = None
    if cost_status in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        minimum_cost = sum(
            cost * cost_solver.Value(variable)
            for cost, variable in zip(costs, variables)
        ) / COST_SCALE

    return {
        "interpretation_id": interpretation_id,
        "feasible_without_ticket_limits": True,
        "failure_reason": None,
        "missing_coverage_keys": [],
        "coverage_candidate_counts": coverage_counts,
        "minimum_required_nodes": minimum_nodes,
        "minimum_effective_expected_cost_usd": (
            round(float(minimum_cost), 8) if minimum_cost is not None else None
        ),
    }


def build_infeasibility_report(
    candidate_bundle: Mapping[str, Any],
    limits: GraphLimits,
    *,
    message: str,
) -> dict[str, Any]:
    """Return a structured reason for an optimizer failure."""
    candidates = _candidate_objects(candidate_bundle)
    interpretations = dict(candidate_bundle.get("interpretations", {}))
    rows = [
        _solve_minimum(interpretation_id, meta, candidates)
        for interpretation_id, meta in sorted(interpretations.items())
    ]
    feasible_rows = [row for row in rows if row["feasible_without_ticket_limits"]]
    raw_budget = None
    if limits.max_budget_usd is not None:
        raw_budget = float(limits.max_budget_usd) / max(
            1.0, float(limits.cost_risk_multiplier)
        )

    node_fit = [
        row
        for row in feasible_rows
        if int(row["minimum_required_nodes"]) <= int(limits.max_nodes)
    ]
    cost_fit = [
        row
        for row in node_fit
        if raw_budget is None
        or (
            row["minimum_effective_expected_cost_usd"] is not None
            and float(row["minimum_effective_expected_cost_usd"]) <= raw_budget + 1e-12
        )
    ]
    if not feasible_rows:
        code = "CAPABILITY_OR_INDEPENDENCE_GAP"
    elif not node_fit:
        code = "BUDGET_INSUFFICIENT_NODES"
    elif not cost_fit:
        code = "BUDGET_INSUFFICIENT_COST"
    else:
        code = "UNEXPLAINED_CONSTRAINT_CONFLICT"

    calibration = candidate_bundle.get("hard_capability_calibration", {})
    return {
        "version": 5,
        "status": "INFEASIBLE",
        "code": code,
        "message": message,
        "ticket_limits": {
            "maximum_initial_nodes": int(limits.max_nodes),
            "maximum_total_calls": int(limits.max_model_calls),
            "maximum_replacements": int(limits.max_replacements),
            "hard_runtime_budget_usd": limits.max_budget_usd,
            "planning_raw_budget_usd": (
                round(raw_budget, 8) if raw_budget is not None else None
            ),
            "cost_risk_multiplier": float(limits.cost_risk_multiplier),
        },
        "candidate_counts": {
            "before_pareto": int(candidate_bundle.get("candidate_count_before_pareto", 0)),
            "after_pareto": int(candidate_bundle.get("candidate_count_after_pareto", 0)),
            "pruned": int(candidate_bundle.get("pareto_pruned_count", 0)),
        },
        "interpretations": rows,
        "minimum_required_nodes": min(
            (
                int(row["minimum_required_nodes"])
                for row in feasible_rows
                if row["minimum_required_nodes"] is not None
            ),
            default=None,
        ),
        "minimum_effective_expected_cost_usd": min(
            (
                float(row["minimum_effective_expected_cost_usd"])
                for row in feasible_rows
                if row["minimum_effective_expected_cost_usd"] is not None
            ),
            default=None,
        ),
        "hard_capability_calibration": calibration,
        "model_calls_performed": 0,
        "fallback_used": False,
    }


def build_candidate_generation_failure_report(
    resources: Mapping[str, Any],
    market: Mapping[str, Any],
    *,
    message: str,
) -> dict[str, Any]:
    return {
        "version": 5,
        "status": "INFEASIBLE",
        "code": "CANDIDATE_GENERATION_EMPTY",
        "message": message,
        "task_signals": dict(
            resources.get("task_semantics", {}).get("task_signals", {})
        ),
        "market_counts": {
            "endpoint_count": int(market.get("endpoint_count", 0)),
            "real_endpoint_count": int(market.get("real_endpoint_count", 0)),
            "synthetic_fixture_count": int(market.get("synthetic_fixture_count", 0)),
        },
        "model_calls_performed": 0,
        "fallback_used": False,
    }
