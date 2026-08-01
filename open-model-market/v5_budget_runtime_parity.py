"""Keep V5 planning, zero-call gates, and runtime on one cost basis."""
from __future__ import annotations

import math
from dataclasses import replace
from threading import Lock
from typing import Any, Mapping

import v5_planner as planner
import v5_value_optimizer as value_optimizer
from execution_graph import GraphLimits
from v5_model_company import REQUIRE_DISTINCT_MODEL_COMPANIES

_INSTALLED = False
_LOCK = Lock()
_ORIGINAL_OPTIMIZE = value_optimizer.optimize_execution_graph
_ORIGINAL_RATIO_ITERATIONS = int(value_optimizer.MAX_RATIO_ITERATIONS)


def planning_raw_budget_usd(limits: GraphLimits) -> float | None:
    if limits.max_budget_usd is None:
        return None
    multiplier = max(1.0, float(limits.cost_risk_multiplier))
    return max(0.0, float(limits.max_budget_usd) / multiplier)


def adaptive_ratio_iterations(
    candidate_bundle: Mapping[str, Any],
    timeout_seconds: float,
) -> int:
    count = max(1, len(candidate_bundle.get("candidates", [])))
    size_term = 2 * int(math.ceil(math.log2(count + 1)))
    time_term = int(max(1.0, float(timeout_seconds)) // 4)
    return max(4, min(18, size_term + time_term))


def risk_budgeted_optimize_execution_graph(
    candidate_bundle: Mapping[str, Any],
    *,
    limits: GraphLimits | None = None,
    quality_tolerance_pct: float = 2.0,
    solver_timeout_seconds: float = 20.0,
    require_distinct_model_companies: bool = (
        REQUIRE_DISTINCT_MODEL_COMPANIES
    ),
) -> dict[str, Any]:
    """Optimize under the raw budget and the same company hard constraint."""
    limits = limits or GraphLimits()
    raw_budget = planning_raw_budget_usd(limits)
    planning_limits = replace(limits, max_budget_usd=raw_budget)
    iterations = adaptive_ratio_iterations(
        candidate_bundle,
        solver_timeout_seconds,
    )

    with _LOCK:
        previous = int(value_optimizer.MAX_RATIO_ITERATIONS)
        value_optimizer.MAX_RATIO_ITERATIONS = iterations
        try:
            result = _ORIGINAL_OPTIMIZE(
                candidate_bundle,
                limits=planning_limits,
                quality_tolerance_pct=quality_tolerance_pct,
                solver_timeout_seconds=solver_timeout_seconds,
                require_distinct_model_companies=(
                    require_distinct_model_companies
                ),
            )
        finally:
            value_optimizer.MAX_RATIO_ITERATIONS = previous

    graph = result.get("execution_graph")
    graph = graph if isinstance(graph, dict) else {}
    raw_cost = float(graph.get("estimated_total_cost", 0.0) or 0.0)
    multiplier = max(1.0, float(limits.cost_risk_multiplier))
    risk_cost = raw_cost * multiplier
    hard_budget = limits.max_budget_usd
    if (
        hard_budget is not None
        and risk_cost > float(hard_budget) + 1e-12
    ):
        raise planner.V5PlanningError(
            "Risk-adjusted selected graph exceeds the runtime hard budget "
            "after solve"
        )

    parity = {
        "hard_runtime_budget_usd": hard_budget,
        "planning_raw_budget_usd": raw_budget,
        "cost_risk_multiplier": multiplier,
        "selected_raw_cost_usd": round(raw_cost, 8),
        "selected_risk_adjusted_cost_usd": round(risk_cost, 8),
        "adaptive_ratio_iterations": iterations,
        "require_distinct_model_companies": bool(
            require_distinct_model_companies
        ),
        "policy": (
            "optimizer-raw-budget-equals-runtime-hard-budget-divided-by-"
            "risk-multiplier"
        ),
    }
    graph.setdefault("metadata", {})[
        "budget_preflight_parity"
    ] = parity
    result["budget_preflight_parity"] = parity
    result["adaptive_ratio_iterations"] = iterations
    return result
