"""Shared CP-SAT solver for direct cost-performance maximization.

The objective is a fractional program:

    risk-adjusted task utility / effective total cost

Hard constraints are installed by the caller. The solver uses a bounded
Dinkelbach iteration and never runs a separate maximum-quality phase.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ortools.sat.python import cp_model

RATIO_SCALE = 1_000_000
MAX_ITERATIONS = 12


@dataclass(frozen=True)
class CostPerformanceSolve:
    solver: cp_model.CpSolver
    status: int
    status_name: str
    numerator_value: int
    denominator_value: int
    actual_cost_value: int
    call_count: int
    ratio_scaled: int
    iterations: tuple[dict[str, int | str], ...]


def _solver(timeout_seconds: float, workers: int) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(1.0, float(timeout_seconds))
    solver.parameters.num_search_workers = max(1, int(workers))
    solver.parameters.random_seed = 0
    return solver


def solve_cost_performance(
    model: cp_model.CpModel,
    *,
    numerator_expr: Any,
    denominator_expr: Any,
    actual_cost_expr: Any,
    call_count_expr: Any,
    tie_break_penalty_expr: Any,
    timeout_seconds: float,
    workers: int = 1,
) -> CostPerformanceSolve:
    """Maximize numerator/denominator, then use cost/calls/risk only as ties.

    ``denominator_expr`` must be strictly positive for every feasible solution.
    Callers should add a very small zero-price guard to real estimated cost.
    """
    ratio_scaled = 0
    trace: list[dict[str, int | str]] = []

    for iteration in range(1, MAX_ITERATIONS + 1):
        residual_expr = numerator_expr * RATIO_SCALE - ratio_scaled * denominator_expr
        model.Maximize(residual_expr)
        solver = _solver(timeout_seconds, workers)
        status = solver.Solve(model)
        if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            raise RuntimeError(
                f"Cost-performance solve found no feasible solution: {solver.StatusName(status)}"
            )

        numerator = int(solver.Value(numerator_expr))
        denominator = max(1, int(solver.Value(denominator_expr)))
        residual = numerator * RATIO_SCALE - ratio_scaled * denominator
        next_ratio = int(round(numerator * RATIO_SCALE / denominator))
        trace.append({
            "iteration": iteration,
            "status": solver.StatusName(status),
            "numerator": numerator,
            "denominator": denominator,
            "ratio_scaled": next_ratio,
            "residual": residual,
        })
        if next_ratio == ratio_scaled or abs(residual) <= 1:
            ratio_scaled = next_ratio
            break
        ratio_scaled = next_ratio

    # Lock the converged fractional optimum first. Only after the best residual
    # is fixed do cost, call count and risk decide among equal-value solutions.
    # This avoids multiplying already-large objective coefficients and therefore
    # avoids CP-SAT MODEL_INVALID integer-overflow rejection.
    residual_expr = numerator_expr * RATIO_SCALE - ratio_scaled * denominator_expr
    model.Maximize(residual_expr)
    ratio_solver = _solver(timeout_seconds, workers)
    ratio_status = ratio_solver.Solve(model)
    if ratio_status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        status_name = ratio_solver.StatusName(ratio_status)
        raise RuntimeError(f"Cost-performance lock solve failed: {status_name}")
    best_residual = int(ratio_solver.Value(residual_expr))
    model.Add(residual_expr == best_residual)

    model.Minimize(tie_break_penalty_expr)
    final_solver = _solver(timeout_seconds, workers)
    final_status = final_solver.Solve(model)
    if final_status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        status_name = final_solver.StatusName(final_status)
        raise RuntimeError(f"Cost-performance tie-break solve failed: {status_name}")

    numerator = int(final_solver.Value(numerator_expr))
    denominator = max(1, int(final_solver.Value(denominator_expr)))
    actual_cost = max(0, int(final_solver.Value(actual_cost_expr)))
    calls = max(0, int(final_solver.Value(call_count_expr)))
    ratio_scaled = int(round(numerator * RATIO_SCALE / denominator))
    trace.append({
        "iteration": len(trace) + 1,
        "status": final_solver.StatusName(final_status),
        "numerator": numerator,
        "denominator": denominator,
        "ratio_scaled": ratio_scaled,
        "residual": numerator * RATIO_SCALE - ratio_scaled * denominator,
    })

    return CostPerformanceSolve(
        solver=final_solver,
        status=final_status,
        status_name=final_solver.StatusName(final_status),
        numerator_value=numerator,
        denominator_value=denominator,
        actual_cost_value=actual_cost,
        call_count=calls,
        ratio_scaled=ratio_scaled,
        iterations=tuple(trace),
    )
