"""Cost-performance-first V5 execution-graph optimizer.

Hard constraints are mandatory. The primary objective is risk-adjusted task
utility per effective expected dollar. Effective cost includes the initial
model request, the candidate's expected one-step recovery cost, and operating
overhead for both the initial and expected recovery calls.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

from execution_graph import GraphLimits
from v5_planner import (
    CandidateNode,
    V5PlanningError,
    _candidate_objects,
    _clamp,
    _selected_graph,
    compile_model_endpoint_market,
    generate_candidate_graph,
)

COST_SCALE = 1_000_000
QUALITY_SCALE = 100_000
PROBABILITY_SCALE = 100_000
CALL_OVERHEAD_USD = 0.0001
MAX_RATIO_ITERATIONS = 10


def _solver(seconds: float) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(1.0, float(seconds))
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = 0
    return solver


def _value(solver: cp_model.CpSolver, expression: Any) -> int:
    return int(solver.Value(expression))


def _solve_cost_performance(
    model: cp_model.CpModel,
    quality: Any,
    effective_cost: Any,
    timeout_seconds: float,
) -> tuple[cp_model.CpSolver, int, list[str]]:
    """Solve the discrete quality/effective-cost ratio with linear iterations."""
    phase_status: list[str] = []

    model.Minimize(effective_cost)
    solver = _solver(timeout_seconds)
    status = solver.Solve(model)
    phase_status.append(f"minimum-feasible-cost:{solver.StatusName(status)}")
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise V5PlanningError(
            f"No feasible V5 execution graph; solver status={solver.StatusName(status)}"
        )

    best_quality = max(0, _value(solver, quality))
    best_cost = max(1, _value(solver, effective_cost))

    for iteration in range(MAX_RATIO_ITERATIONS):
        divisor = math.gcd(best_quality, best_cost) or 1
        numerator = best_quality // divisor
        denominator = best_cost // divisor
        model.Maximize(quality * denominator - effective_cost * numerator)
        candidate_solver = _solver(timeout_seconds)
        candidate_status = candidate_solver.Solve(model)
        phase_status.append(
            f"ratio-iteration-{iteration + 1}:{candidate_solver.StatusName(candidate_status)}"
        )
        if candidate_status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            break
        candidate_quality = max(0, _value(candidate_solver, quality))
        candidate_cost = max(1, _value(candidate_solver, effective_cost))
        residual = candidate_quality * denominator - candidate_cost * numerator
        solver = candidate_solver
        status = candidate_status
        best_quality = candidate_quality
        best_cost = candidate_cost
        if residual <= 0:
            break

    model.Add(quality * best_cost >= effective_cost * best_quality)
    model.Minimize(effective_cost)
    tie_solver = _solver(timeout_seconds)
    tie_status = tie_solver.Solve(model)
    phase_status.append(f"best-ratio-lowest-cost:{tie_solver.StatusName(tie_status)}")
    if tie_status in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        solver = tie_solver
        status = tie_status
    return solver, status, phase_status


def _work_requires_distinct_model(
    candidates: Sequence[CandidateNode],
    candidate_indices: Sequence[int],
    work_id: str,
) -> bool:
    """Return the explicit hard model-independence policy for one work unit."""
    return any(
        work_id in candidates[index].independence_groups
        for index in candidate_indices
    )


def _scaled_cost(candidate: CandidateNode) -> int:
    return max(0, int(round(candidate.estimated_cost * COST_SCALE)))


def _scaled_expected_recovery_cost(candidate: CandidateNode) -> int:
    """Price one bounded replacement attempt by its estimated failure probability."""
    return max(
        0,
        int(
            round(
                candidate.estimated_cost
                * _clamp(candidate.failure_probability)
                * COST_SCALE
            )
        ),
    )


def optimize_execution_graph(
    candidate_bundle: Mapping[str, Any],
    *,
    limits: GraphLimits | None = None,
    quality_tolerance_pct: float = 2.0,
    solver_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Select the feasible V5 graph with maximum expected cost-performance.

    ``quality_tolerance_pct`` remains in the signature only for compatibility
    with older callers. It is recorded as ignored and does not affect solving.
    """
    limits = limits or GraphLimits()
    candidates = _candidate_objects(candidate_bundle)
    interpretations = dict(candidate_bundle.get("interpretations", {}))
    if not candidates or not interpretations:
        raise V5PlanningError("Candidate bundle is empty.")

    model = cp_model.CpModel()
    y = {
        interpretation_id: model.NewBoolVar(f"interpretation_{index}")
        for index, interpretation_id in enumerate(sorted(interpretations))
    }
    x = [model.NewBoolVar(f"candidate_{index}") for index in range(len(candidates))]
    model.Add(sum(y.values()) == 1)
    for index, candidate in enumerate(candidates):
        model.Add(x[index] <= y[candidate.interpretation_id])

    for interpretation_id, meta in interpretations.items():
        coverage_keys = [
            f"{work_id}#{copy_index}"
            for work_id, copies in meta["copies_by_work"].items()
            for copy_index in range(int(copies))
        ]
        for key in coverage_keys:
            terms = [
                x[index]
                for index, candidate in enumerate(candidates)
                if candidate.interpretation_id == interpretation_id
                and key in candidate.coverage_keys
            ]
            if not terms:
                model.Add(y[interpretation_id] == 0)
            else:
                model.Add(sum(terms) == y[interpretation_id])

    model.Add(sum(x) <= limits.max_nodes)
    initial_cost_terms = [
        _scaled_cost(candidate) * x[index]
        for index, candidate in enumerate(candidates)
    ]
    initial_cost = sum(initial_cost_terms)

    hard_independence_constraints: list[dict[str, Any]] = []
    for interpretation_id, meta in interpretations.items():
        for work_id, copies in meta["copies_by_work"].items():
            copies = int(copies)
            if copies < 2:
                continue
            copy_candidates: dict[int, list[int]] = {}
            for copy_index in range(copies):
                key = f"{work_id}#{copy_index}"
                copy_candidates[copy_index] = [
                    index
                    for index, candidate in enumerate(candidates)
                    if candidate.interpretation_id == interpretation_id
                    and key in candidate.coverage_keys
                ]
            scoped_indices = sorted(
                {index for indices in copy_candidates.values() for index in indices}
            )
            require_distinct_model = _work_requires_distinct_model(
                candidates,
                scoped_indices,
                str(work_id),
            )
            hard_independence_constraints.append(
                {
                    "interpretation_id": str(interpretation_id),
                    "work_id": str(work_id),
                    "copies": copies,
                    "different_model_required": require_distinct_model,
                    "different_provider_required": False,
                    "provider_diversity_mode": "preferred-runtime-rebalancing",
                }
            )
            if not require_distinct_model:
                continue
            for left_copy in range(copies):
                for right_copy in range(left_copy + 1, copies):
                    for left in copy_candidates[left_copy]:
                        for right in copy_candidates[right_copy]:
                            if candidates[left].model == candidates[right].model:
                                model.Add(x[left] + x[right] <= 1)

    quality_terms = []
    for index, candidate in enumerate(candidates):
        score = (
            candidate.estimated_quality
            * (1.0 - 0.35 * candidate.failure_probability)
            - 0.10 * candidate.quality_uncertainty
        )
        quality_terms.append(int(round(score * QUALITY_SCALE)) * x[index])
    for interpretation_id, variable in y.items():
        interpretation_score = float(
            interpretations[interpretation_id]
            .get("metrics", {})
            .get("interpretation_score", 0.5)
        )
        quality_terms.append(
            int(round(interpretation_score * QUALITY_SCALE * 0.25)) * variable
        )
    quality_expr = sum(quality_terms)

    call_count = sum(x)
    call_overhead = max(1, int(round(CALL_OVERHEAD_USD * COST_SCALE)))
    expected_recovery_cost_terms = [
        _scaled_expected_recovery_cost(candidate) * x[index]
        for index, candidate in enumerate(candidates)
    ]
    expected_recovery_cost = sum(expected_recovery_cost_terms)
    expected_recovery_overhead_terms = [
        int(round(_clamp(candidate.failure_probability) * call_overhead)) * x[index]
        for index, candidate in enumerate(candidates)
    ]
    expected_recovery_overhead = sum(expected_recovery_overhead_terms)
    effective_cost = (
        initial_cost
        + expected_recovery_cost
        + call_count * call_overhead
        + expected_recovery_overhead
    )
    if limits.max_budget_usd is not None:
        model.Add(effective_cost <= int(round(limits.max_budget_usd * COST_SCALE)))

    solver, status, phase_status = _solve_cost_performance(
        model,
        quality_expr,
        effective_cost,
        solver_timeout_seconds,
    )

    selected_indices = [
        index for index, variable in enumerate(x) if solver.Value(variable)
    ]
    selected_interpretations = [
        interpretation_id
        for interpretation_id, variable in y.items()
        if solver.Value(variable)
    ]
    if len(selected_interpretations) != 1:
        raise V5PlanningError("Solver did not select exactly one interpretation.")
    selected_interpretation = selected_interpretations[0]

    normalized_quality = _clamp(
        sum(candidates[index].estimated_quality for index in selected_indices)
        / max(1, len(selected_indices))
    )
    graph = _selected_graph(
        candidates,
        selected_indices,
        candidate_bundle,
        selected_interpretation,
        normalized_quality,
        normalized_quality,
        limits,
    )
    graph_data = graph.to_dict()
    metadata = graph_data.setdefault("metadata", {})
    metadata["highest_principle"] = "maximum_cost_performance"
    metadata["objective_order"] = [
        "hard_constraints",
        "maximum_expected_cost_performance",
    ]
    metadata["cost_performance_definition"] = (
        "risk_adjusted_task_utility_divided_by_initial_cost_plus_expected_recovery_cost_plus_call_overhead"
    )
    metadata["marginal_utility_stop"] = {
        "scope": "optional graph expansion across feasible task interpretations and candidate bundles",
        "criterion": "accept expansion only when it improves the global risk-adjusted utility/effective-expected-cost ratio",
        "tie_break": "for equal best ratio choose the lower effective expected cost",
        "mandatory_work_exception": "hard required coverage is never dropped for price",
    }
    metadata["independence_policy"] = {
        "hard_model_diversity_scope": "explicit-independence-groups-only",
        "hard_provider_diversity_scope": "none",
        "provider_diversity": "preferred-and-enforced-by-r8-runtime-rebalancing",
        "constraints": [
            row
            for row in hard_independence_constraints
            if row["interpretation_id"] == selected_interpretation
        ],
    }

    selected_quality = max(0, _value(solver, quality_expr))
    selected_initial_cost = max(0, _value(solver, initial_cost))
    selected_recovery_cost = max(0, _value(solver, expected_recovery_cost))
    selected_effective_cost = max(1, _value(solver, effective_cost))
    selected_expected_recovery_calls = sum(
        _clamp(candidates[index].failure_probability)
        for index in selected_indices
    )
    return {
        "version": 5,
        "optimizer": "google-or-tools-cp-sat",
        "selection_method": "execution-graph-expected-cost-performance-v5",
        "solver_status": solver.StatusName(status),
        "phase_status": phase_status,
        "selected_interpretation": selected_interpretation,
        "highest_principle": "maximum_cost_performance",
        "objective_order": ["hard_constraints", "maximum_expected_cost_performance"],
        "cost_performance_definition": (
            "risk_adjusted_task_utility_divided_by_initial_cost_plus_expected_recovery_cost_plus_call_overhead"
        ),
        "selected_quality_objective_scaled": selected_quality,
        "selected_initial_cost_scaled": selected_initial_cost,
        "selected_expected_recovery_cost_scaled": selected_recovery_cost,
        "selected_effective_cost_scaled": selected_effective_cost,
        "selected_initial_cost_usd": round(selected_initial_cost / COST_SCALE, 8),
        "selected_expected_recovery_cost_usd": round(selected_recovery_cost / COST_SCALE, 8),
        "selected_effective_cost_usd": round(selected_effective_cost / COST_SCALE, 8),
        "selected_expected_recovery_calls": round(selected_expected_recovery_calls, 6),
        "cost_scale": COST_SCALE,
        "call_overhead_usd": CALL_OVERHEAD_USD,
        "cost_performance_ratio": round(
            selected_quality / selected_effective_cost,
            9,
        ),
        "marginal_utility_stop": metadata["marginal_utility_stop"],
        "deprecated_quality_tolerance_pct_ignored": float(quality_tolerance_pct),
        "selected_candidate_ids": [
            candidates[index].candidate_id for index in selected_indices
        ],
        "hard_independence_constraints": hard_independence_constraints,
        "execution_graph": graph_data,
        "fallback_used": False,
    }


def compile_and_optimize_v5(
    ranked: Sequence[Any],
    resource_bundle: Mapping[str, Any],
    *,
    endpoint_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    allow_synthetic_fixture: bool = False,
    ranking_limit: int = 50,
    limits: GraphLimits | None = None,
    maximum_per_group: int = 12,
    quality_tolerance_pct: float = 2.0,
    solver_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    market = compile_model_endpoint_market(
        ranked,
        resource_bundle,
        endpoint_payloads=endpoint_payloads,
        ranking_limit=ranking_limit,
        allow_synthetic_fixture=allow_synthetic_fixture,
    )
    candidates = generate_candidate_graph(
        resource_bundle,
        market,
        maximum_per_group=maximum_per_group,
    )
    optimization = optimize_execution_graph(
        candidates,
        limits=limits,
        quality_tolerance_pct=quality_tolerance_pct,
        solver_timeout_seconds=solver_timeout_seconds,
    )
    return {
        "version": 5,
        "market": market,
        "candidate_graph": candidates,
        "optimization": optimization,
    }
