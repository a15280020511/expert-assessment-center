"""V5 planner facade with cost-performance as the highest objective.

Market compilation, candidate generation, hard constraints and graph validation
remain in ``v5_planner``. Only the former quality-first/quality-band objective is
replaced by direct fractional cost-performance optimization.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

import v5_planner as base
from cost_performance_solver import RATIO_SCALE, solve_cost_performance
from execution_graph import GraphLimits

COST_SCALE = 1_000_000
ZERO_PRICE_GUARD_UNITS_PER_CALL = 1  # one micro-dollar per selected call


def _candidate_value(row: Mapping[str, Any]) -> float:
    quality = float(row.get("estimated_quality", 0.0))
    failure = float(row.get("failure_probability", 0.0))
    uncertainty = float(row.get("quality_uncertainty", 0.0))
    risk_adjusted = quality * (1.0 - 0.35 * failure) - 0.10 * uncertainty
    cost = max(0.000001, float(row.get("estimated_cost", 0.0)))
    return risk_adjusted / cost


def optimize_execution_graph(
    candidate_bundle: Mapping[str, Any],
    *,
    limits: GraphLimits | None = None,
    quality_tolerance_pct: float | None = None,
    solver_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Select the graph with maximum risk-adjusted utility per total cost.

    ``quality_tolerance_pct`` is accepted only to avoid breaking older callers;
    it has no effect and is not written to the optimization artifact.
    """
    del quality_tolerance_pct
    limits = limits or GraphLimits()
    candidates = base._candidate_objects(candidate_bundle)
    interpretations = dict(candidate_bundle.get("interpretations", {}))
    if not candidates or not interpretations:
        raise base.V5PlanningError("Candidate bundle is empty.")

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
                if candidate.interpretation_id == interpretation_id and key in candidate.coverage_keys
            ]
            if not terms:
                model.Add(y[interpretation_id] == 0)
            else:
                model.Add(sum(terms) == y[interpretation_id])

    model.Add(sum(x) <= limits.max_nodes)
    cost_terms = [
        int(round(candidate.estimated_cost * COST_SCALE)) * x[index]
        for index, candidate in enumerate(candidates)
    ]
    cost_expr = sum(cost_terms)
    call_count_expr = sum(x)
    effective_cost_expr = cost_expr + call_count_expr * ZERO_PRICE_GUARD_UNITS_PER_CALL
    if limits.max_budget_usd is not None:
        model.Add(cost_expr <= int(round(limits.max_budget_usd * COST_SCALE)))

    for interpretation_id, meta in interpretations.items():
        for work_id, copies in meta["copies_by_work"].items():
            if int(copies) < 2:
                continue
            copy_candidates: dict[int, list[int]] = {}
            for copy_index in range(int(copies)):
                key = f"{work_id}#{copy_index}"
                copy_candidates[copy_index] = [
                    index
                    for index, candidate in enumerate(candidates)
                    if candidate.interpretation_id == interpretation_id and key in candidate.coverage_keys
                ]
            for left_copy in range(int(copies)):
                for right_copy in range(left_copy + 1, int(copies)):
                    for left in copy_candidates[left_copy]:
                        for right in copy_candidates[right_copy]:
                            if (
                                candidates[left].model == candidates[right].model
                                or candidates[left].provider_endpoint == candidates[right].provider_endpoint
                            ):
                                model.Add(x[left] + x[right] <= 1)

    quality_terms = []
    for index, candidate in enumerate(candidates):
        score = (
            candidate.estimated_quality * (1.0 - 0.35 * candidate.failure_probability)
            - 0.10 * candidate.quality_uncertainty
        )
        quality_terms.append(int(round(score * 100_000)) * x[index])
    for interpretation_id, variable in y.items():
        interpretation_score = float(
            interpretations[interpretation_id].get("metrics", {}).get("interpretation_score", 0.5)
        )
        quality_terms.append(int(round(interpretation_score * 100_000 * 0.25)) * variable)
    utility_expr = sum(quality_terms)
    failure_expr = sum(
        int(round(candidate.failure_probability * 100_000)) * x[index]
        for index, candidate in enumerate(candidates)
    )

    try:
        solved = solve_cost_performance(
            model,
            numerator_expr=utility_expr,
            denominator_expr=effective_cost_expr,
            actual_cost_expr=cost_expr,
            call_count_expr=call_count_expr,
            tie_break_penalty_expr=cost_expr * 100 + call_count_expr * 10_000 + failure_expr,
            timeout_seconds=solver_timeout_seconds,
            workers=8,
        )
    except RuntimeError as exc:
        raise base.V5PlanningError(str(exc)) from exc

    solver = solved.solver
    selected_indices = [index for index, variable in enumerate(x) if solver.Value(variable)]
    selected_interpretations = [
        interpretation_id for interpretation_id, variable in y.items() if solver.Value(variable)
    ]
    if len(selected_interpretations) != 1:
        raise base.V5PlanningError("Solver did not select exactly one interpretation.")
    selected_interpretation = selected_interpretations[0]

    normalized_quality = base._clamp(
        sum(candidates[index].estimated_quality for index in selected_indices)
        / max(1, len(selected_indices))
    )
    graph = base._selected_graph(
        candidates,
        selected_indices,
        candidate_bundle,
        selected_interpretation,
        0.0,
        normalized_quality,
        limits,
    )
    graph_payload = graph.to_dict()
    metadata = dict(graph_payload.get("metadata", {}))
    metadata["objective_order"] = [
        "hard_constraints",
        "maximum_cost_performance",
        "minimum_cost_calls_failure_as_tiebreakers",
    ]
    metadata["cost_performance_definition"] = (
        "risk_adjusted_task_utility / effective_total_estimated_cost"
    )
    metadata["quality_first_phase_used"] = False
    metadata["quality_tolerance_band_used"] = False
    metadata["zero_price_guard_usd_per_call"] = (
        ZERO_PRICE_GUARD_UNITS_PER_CALL / COST_SCALE
    )

    recovery_pool = dict(metadata.get("recovery_pool", {}))
    for node_id, alternatives in recovery_pool.items():
        recovery_pool[node_id] = sorted(
            alternatives,
            key=lambda row: (
                -_candidate_value(row),
                float(row.get("estimated_cost", 0.0)),
                float(row.get("failure_probability", 0.0)),
                str(row.get("candidate_id", "")),
            ),
        )
    metadata["recovery_pool"] = recovery_pool
    graph_payload["metadata"] = metadata
    graph_payload["quality_floor"] = 0.0

    return {
        "version": 5,
        "optimizer": "google-or-tools-cp-sat",
        "selection_method": "direct-fractional-cost-performance",
        "solver_status": solved.status_name,
        "selected_interpretation": selected_interpretation,
        "objective_order": metadata["objective_order"],
        "cost_performance": {
            "definition": metadata["cost_performance_definition"],
            "ratio": round(solved.ratio_scaled / RATIO_SCALE, 9),
            "ratio_scaled": solved.ratio_scaled,
            "utility_numerator": solved.numerator_value,
            "effective_cost_denominator_units": solved.denominator_value,
            "actual_estimated_cost_usd": round(solved.actual_cost_value / COST_SCALE, 9),
            "zero_price_guard_usd_per_call": ZERO_PRICE_GUARD_UNITS_PER_CALL / COST_SCALE,
            "call_count": solved.call_count,
            "iterations": list(solved.iterations),
        },
        "quality_first_phase_used": False,
        "quality_tolerance_band_used": False,
        "selected_candidate_ids": [candidates[index].candidate_id for index in selected_indices],
        "execution_graph": graph_payload,
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
    quality_tolerance_pct: float | None = None,
    solver_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    market = base.compile_model_endpoint_market(
        ranked,
        resource_bundle,
        endpoint_payloads=endpoint_payloads,
        ranking_limit=ranking_limit,
        allow_synthetic_fixture=allow_synthetic_fixture,
    )
    candidates = base.generate_candidate_graph(
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
