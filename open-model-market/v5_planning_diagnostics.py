"""Constraint-faithful diagnostics for V5 planning failures.

The production optimizer must fail closed, but a bare CP-SAT ``INFEASIBLE`` is
not actionable. These helpers reproduce exact coverage, model independence and
task-global model-company uniqueness before comparing the resulting joint node
and cost pair with approved limits. No model call is performed.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

from execution_graph import GraphLimits
from v5_model_company import (
    REQUIRE_DISTINCT_MODEL_COMPANIES,
    candidate_company,
)
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
    *,
    require_distinct_model_companies: bool = (
        REQUIRE_DISTINCT_MODEL_COMPANIES
    ),
) -> tuple[
    cp_model.CpModel,
    list[cp_model.IntVar],
    list[int],
    list[str],
    list[CandidateNode],
]:
    scoped = [
        candidate
        for candidate in candidates
        if candidate.interpretation_id == interpretation_id
    ]
    model = cp_model.CpModel()
    variables = [
        model.NewBoolVar(f"candidate_{index}")
        for index in range(len(scoped))
    ]
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

    if require_distinct_model_companies:
        indices_by_company: dict[str, list[int]] = defaultdict(list)
        for index, candidate in enumerate(scoped):
            indices_by_company[candidate_company(candidate)].append(index)
        for indices in indices_by_company.values():
            if len(indices) > 1:
                model.Add(sum(variables[index] for index in indices) <= 1)

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
    return model, variables, costs, missing, scoped


def _solver() -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    return solver


def _selected_ids(
    solver: cp_model.CpSolver,
    variables: Sequence[cp_model.IntVar],
    candidates: Sequence[CandidateNode],
) -> list[str]:
    return [
        candidate.candidate_id
        for variable, candidate in zip(variables, candidates)
        if solver.Value(variable)
    ]


def _relaxed_company_diagnostic(
    interpretation_id: str,
    meta: Mapping[str, Any],
    candidates: Sequence[CandidateNode],
) -> dict[str, Any]:
    model, variables, _, missing, scoped = _build_model(
        interpretation_id,
        meta,
        candidates,
        require_distinct_model_companies=False,
    )
    available_companies = sorted(
        {candidate_company(candidate) for candidate in scoped}
    )
    if missing:
        return {
            "relaxed_feasible": False,
            "minimum_required_nodes_without_company_uniqueness": None,
            "available_company_count": len(available_companies),
            "available_companies": available_companies,
        }
    model.Minimize(sum(variables))
    solver = _solver()
    status = solver.Solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        return {
            "relaxed_feasible": False,
            "minimum_required_nodes_without_company_uniqueness": None,
            "available_company_count": len(available_companies),
            "available_companies": available_companies,
        }
    minimum_nodes = int(
        sum(solver.Value(variable) for variable in variables)
    )
    return {
        "relaxed_feasible": True,
        "minimum_required_nodes_without_company_uniqueness": minimum_nodes,
        "minimum_distinct_companies_required": minimum_nodes,
        "available_company_count": len(available_companies),
        "available_companies": available_companies,
    }


def _solve_minimum(
    interpretation_id: str,
    meta: Mapping[str, Any],
    candidates: Sequence[CandidateNode],
) -> dict[str, Any]:
    model, variables, costs, missing, scoped = _build_model(
        interpretation_id,
        meta,
        candidates,
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
    company_diagnostic = _relaxed_company_diagnostic(
        interpretation_id,
        meta,
        candidates,
    )
    if missing:
        return {
            "interpretation_id": interpretation_id,
            "feasible_without_ticket_limits": False,
            "failure_reason": "missing_coverage_candidates",
            "missing_coverage_keys": missing,
            "coverage_candidate_counts": coverage_counts,
            "minimum_required_nodes": None,
            "minimum_effective_expected_cost_usd": None,
            "minimum_node_solution_candidate_ids": [],
            "model_company_diagnostic": company_diagnostic,
        }

    node_solver = _solver()
    model.Minimize(sum(variables))
    status = node_solver.Solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        company_conflict = bool(company_diagnostic["relaxed_feasible"])
        return {
            "interpretation_id": interpretation_id,
            "feasible_without_ticket_limits": False,
            "failure_reason": (
                "model_company_diversity_conflict"
                if company_conflict
                else "independence_or_exact_coverage_conflict"
            ),
            "missing_coverage_keys": [],
            "coverage_candidate_counts": coverage_counts,
            "minimum_required_nodes": None,
            "minimum_effective_expected_cost_usd": None,
            "minimum_node_solution_candidate_ids": [],
            "model_company_diagnostic": company_diagnostic,
        }

    minimum_nodes = int(
        sum(node_solver.Value(variable) for variable in variables)
    )
    cost_model, cost_variables, cost_values, cost_missing, cost_scoped = _build_model(
        interpretation_id,
        meta,
        candidates,
    )
    if cost_missing:
        raise RuntimeError(
            "coverage changed between deterministic diagnostic solves"
        )
    cost_model.Add(sum(cost_variables) == minimum_nodes)
    cost_model.Minimize(
        sum(
            cost * variable
            for cost, variable in zip(cost_values, cost_variables)
        )
    )
    cost_solver = _solver()
    cost_status = cost_solver.Solve(cost_model)
    minimum_cost = None
    selected_ids: list[str] = []
    if cost_status in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        minimum_cost = sum(
            cost * cost_solver.Value(variable)
            for cost, variable in zip(cost_values, cost_variables)
        ) / COST_SCALE
        selected_ids = _selected_ids(
            cost_solver,
            cost_variables,
            cost_scoped,
        )

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
        "minimum_node_solution_candidate_ids": selected_ids,
        "model_company_diagnostic": {
            **company_diagnostic,
            "strict_company_unique_solution_exists": True,
        },
    }


def _minimum_cost(rows: Sequence[Mapping[str, Any]]) -> float | None:
    values = [
        float(row["minimum_effective_expected_cost_usd"])
        for row in rows
        if row.get("minimum_effective_expected_cost_usd") is not None
    ]
    return min(values) if values else None


def _minimum_nodes(rows: Sequence[Mapping[str, Any]]) -> int | None:
    values = [
        int(row["minimum_required_nodes"])
        for row in rows
        if row.get("minimum_required_nodes") is not None
    ]
    return min(values) if values else None


def _remediation_options(
    rows: Sequence[Mapping[str, Any]],
    limits: GraphLimits,
    raw_budget: float | None,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    multiplier = max(1.0, float(limits.cost_risk_multiplier))
    recovery_reserve = max(0, int(limits.max_replacements))
    for row in rows:
        nodes = int(row["minimum_required_nodes"])
        raw_cost = float(row["minimum_effective_expected_cost_usd"])
        hard_budget = raw_cost * multiplier
        options.append(
            {
                "interpretation_id": row["interpretation_id"],
                "required_initial_nodes": nodes,
                "required_total_calls_with_current_recovery_reserve": (
                    nodes + recovery_reserve
                ),
                "minimum_planning_raw_budget_usd": round(raw_cost, 8),
                "minimum_hard_runtime_budget_usd": round(hard_budget, 8),
                "fits_current_node_limit": nodes <= int(limits.max_nodes),
                "fits_current_raw_budget": (
                    raw_budget is None or raw_cost <= raw_budget + 1e-12
                ),
                "fits_current_joint_limits": (
                    nodes <= int(limits.max_nodes)
                    and (
                        raw_budget is None
                        or raw_cost <= raw_budget + 1e-12
                    )
                ),
                "minimum_node_solution_candidate_ids": list(
                    row.get("minimum_node_solution_candidate_ids") or []
                ),
            }
        )
    return sorted(
        options,
        key=lambda row: (
            not row["fits_current_joint_limits"],
            row["minimum_hard_runtime_budget_usd"],
            row["required_initial_nodes"],
            row["interpretation_id"],
        ),
    )


def build_infeasibility_report(
    candidate_bundle: Mapping[str, Any],
    limits: GraphLimits,
    *,
    message: str,
) -> dict[str, Any]:
    """Return a structured, joint node/cost reason for optimizer failure."""
    candidates = _candidate_objects(candidate_bundle)
    interpretations = dict(candidate_bundle.get("interpretations", {}))
    rows = [
        _solve_minimum(interpretation_id, meta, candidates)
        for interpretation_id, meta in sorted(interpretations.items())
    ]
    feasible_rows = [
        row for row in rows if row["feasible_without_ticket_limits"]
    ]
    company_conflict_rows = [
        row
        for row in rows
        if row.get("failure_reason") == "model_company_diversity_conflict"
    ]
    raw_budget = None
    if limits.max_budget_usd is not None:
        raw_budget = float(limits.max_budget_usd) / max(
            1.0,
            float(limits.cost_risk_multiplier),
        )

    node_fit = [
        row
        for row in feasible_rows
        if int(row["minimum_required_nodes"]) <= int(limits.max_nodes)
    ]
    cost_fit_any_nodes = [
        row
        for row in feasible_rows
        if raw_budget is None
        or (
            row["minimum_effective_expected_cost_usd"] is not None
            and float(row["minimum_effective_expected_cost_usd"])
            <= raw_budget + 1e-12
        )
    ]
    joint_fit = [
        row
        for row in node_fit
        if raw_budget is None
        or (
            row["minimum_effective_expected_cost_usd"] is not None
            and float(row["minimum_effective_expected_cost_usd"])
            <= raw_budget + 1e-12
        )
    ]
    if not feasible_rows and company_conflict_rows:
        code = "MODEL_COMPANY_DIVERSITY_INSUFFICIENT"
    elif not feasible_rows:
        code = "CAPABILITY_OR_INDEPENDENCE_GAP"
    elif not node_fit:
        code = "BUDGET_INSUFFICIENT_NODES"
    elif not joint_fit:
        code = "BUDGET_INSUFFICIENT_COST"
    else:
        code = "UNEXPLAINED_CONSTRAINT_CONFLICT"

    global_min_nodes = _minimum_nodes(feasible_rows)
    global_min_cost = _minimum_cost(feasible_rows)
    node_limited_min_cost = _minimum_cost(node_fit)
    budget_limited_min_nodes = _minimum_nodes(cost_fit_any_nodes)
    reported_min_cost = (
        node_limited_min_cost
        if node_limited_min_cost is not None
        else global_min_cost
    )
    multiplier = max(1.0, float(limits.cost_risk_multiplier))
    calibration = candidate_bundle.get("hard_capability_calibration", {})
    remediations = _remediation_options(
        feasible_rows,
        limits,
        raw_budget,
    )
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
            "before_pareto": int(
                candidate_bundle.get("candidate_count_before_pareto", 0)
            ),
            "after_pareto": int(
                candidate_bundle.get("candidate_count_after_pareto", 0)
            ),
            "pruned": int(
                candidate_bundle.get("pareto_pruned_count", 0)
            ),
        },
        "model_company_policy": {
            "require_distinct_model_companies": True,
            "scope": "all-substantive-nodes-in-one-task",
            "failure_policy": "fail-closed-before-model-call",
            "conflicting_interpretation_ids": [
                row["interpretation_id"]
                for row in company_conflict_rows
            ],
        },
        "interpretations": rows,
        "minimum_required_nodes": global_min_nodes,
        "minimum_effective_expected_cost_usd": (
            round(reported_min_cost, 8)
            if reported_min_cost is not None
            else None
        ),
        "joint_limit_diagnostics": {
            "minimum_required_nodes_any_interpretation": global_min_nodes,
            "minimum_effective_expected_cost_usd_any_interpretation": (
                round(global_min_cost, 8)
                if global_min_cost is not None
                else None
            ),
            "minimum_effective_expected_cost_usd_within_node_limit": (
                round(node_limited_min_cost, 8)
                if node_limited_min_cost is not None
                else None
            ),
            "minimum_hard_runtime_budget_usd_within_node_limit": (
                round(node_limited_min_cost * multiplier, 8)
                if node_limited_min_cost is not None
                else None
            ),
            "minimum_required_nodes_within_planning_budget": (
                budget_limited_min_nodes
            ),
            "jointly_feasible_interpretation_ids": [
                row["interpretation_id"] for row in joint_fit
            ],
            "independent_minima_must_not_be_combined": True,
        },
        "feasible_remediation_options": remediations,
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
            "real_endpoint_count": int(
                market.get("real_endpoint_count", 0)
            ),
            "synthetic_fixture_count": int(
                market.get("synthetic_fixture_count", 0)
            ),
        },
        "model_calls_performed": 0,
        "fallback_used": False,
    }
