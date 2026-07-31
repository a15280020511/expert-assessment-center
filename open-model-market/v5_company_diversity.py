"""Company-diversity policy composition for V5.

The canonical CP-SAT implementation lives in ``v5_value_optimizer``. This
module only owns company-preserving candidate pruning and compatibility exports.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_budget_runtime_parity as budget_parity
import v5_planner as planner
import v5_value_optimizer as value_optimizer
from execution_graph import GraphLimits
from v5_model_company import (
    MINIMUM_CANDIDATES_PER_WORK as MINIMUM_CANDIDATES_PER_WORK,
)
from v5_model_company import MODEL_COMPANY_ALIASES as MODEL_COMPANY_ALIASES
from v5_model_company import (
    REQUIRE_DISTINCT_MODEL_COMPANIES as REQUIRE_DISTINCT_MODEL_COMPANIES,
)
from v5_model_company import candidate_company as candidate_company
from v5_model_company import (
    canonical_model_company as canonical_model_company,
)


def _order_key(
    row: planner.CandidateNode,
) -> tuple[float, float, float, str]:
    return (
        -row.estimated_quality,
        row.estimated_cost,
        row.failure_probability,
        row.candidate_id,
    )


def company_preserving_pareto_prune(
    candidates: Sequence[planner.CandidateNode],
    maximum_per_group: int = MINIMUM_CANDIDATES_PER_WORK,
) -> list[planner.CandidateNode]:
    """Reserve distinct-company alternatives before ordinary Pareto rows."""
    limit = max(1, int(maximum_per_group))
    groups: dict[
        tuple[str, tuple[str, ...]],
        list[planner.CandidateNode],
    ] = {}
    for candidate in candidates:
        groups.setdefault(
            (
                candidate.interpretation_id,
                tuple(candidate.coverage_keys),
            ),
            [],
        ).append(candidate)

    kept: list[planner.CandidateNode] = []
    for rows in groups.values():
        ordered = sorted(rows, key=_order_key)
        frontier = [
            row
            for row in ordered
            if not any(
                planner._dominates(other, row)
                for other in rows
                if other is not row
            )
        ]
        best_by_company: dict[str, planner.CandidateNode] = {}
        best_by_model: dict[str, planner.CandidateNode] = {}
        for row in ordered:
            best_by_company.setdefault(candidate_company(row), row)
            best_by_model.setdefault(row.model, row)

        selected: list[planner.CandidateNode] = []
        selected_ids: set[str] = set()

        def extend(rows_to_add: Sequence[planner.CandidateNode]) -> None:
            for row in rows_to_add:
                if row.candidate_id in selected_ids:
                    continue
                selected.append(row)
                selected_ids.add(row.candidate_id)
                if len(selected) >= limit:
                    return

        extend(sorted(best_by_company.values(), key=_order_key))
        if len(selected) < limit:
            extend(sorted(best_by_model.values(), key=_order_key))
        if len(selected) < limit:
            extend(sorted(frontier, key=_order_key))
        if len(selected) < limit:
            extend(ordered)
        kept.extend(selected)

    kept.sort(
        key=lambda row: (
            row.interpretation_id,
            tuple(row.coverage_keys),
            candidate_company(row),
            -row.estimated_quality,
            row.estimated_cost,
            row.candidate_id,
        )
    )
    return kept


def optimize_execution_graph(
    candidate_bundle: Mapping[str, Any],
    *,
    limits: GraphLimits | None = None,
    quality_tolerance_pct: float = 2.0,
    solver_timeout_seconds: float = 20.0,
    require_distinct_model_companies: bool = (
        REQUIRE_DISTINCT_MODEL_COMPANIES
    ),
) -> dict[str, Any]:
    return value_optimizer.optimize_execution_graph(
        candidate_bundle,
        limits=limits,
        quality_tolerance_pct=quality_tolerance_pct,
        solver_timeout_seconds=solver_timeout_seconds,
        require_distinct_model_companies=(
            require_distinct_model_companies
        ),
    )


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
    return budget_parity.risk_budgeted_optimize_execution_graph(
        candidate_bundle,
        limits=limits,
        quality_tolerance_pct=quality_tolerance_pct,
        solver_timeout_seconds=solver_timeout_seconds,
        require_distinct_model_companies=(
            require_distinct_model_companies
        ),
    )
