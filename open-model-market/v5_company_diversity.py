"""Hard model-company diversity policy for the V5 optimizer.

The model company is derived from the canonicalized author prefix of the direct
OpenRouter model ID (the part before ``/``).  This is intentionally distinct
from the inference Provider: two models may be served by one Provider while
still belonging to different model companies.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import replace
from threading import Lock
from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

from execution_graph import GraphLimits
import v5_budget_runtime_parity as budget_parity
import v5_planner as planner
import v5_value_optimizer as base_optimizer

# Explicit production variables.  The first is a hard constraint, not a score.
REQUIRE_DISTINCT_MODEL_COMPANIES = True
MINIMUM_CANDIDATES_PER_WORK = 24

# Canonicalize known OpenRouter author aliases to the underlying model company.
# Unknown authors remain isolated under their own stable author prefix.
MODEL_COMPANY_ALIASES: Mapping[str, str] = {
    "alibaba": "alibaba",
    "qwen": "alibaba",
    "anthropic": "anthropic",
    "amazon": "amazon",
    "bytedance": "bytedance",
    "bytedance-seed": "bytedance",
    "cohere": "cohere",
    "deepseek": "deepseek",
    "google": "google",
    "meta": "meta",
    "meta-llama": "meta",
    "microsoft": "microsoft",
    "minimax": "minimax",
    "mistral": "mistral",
    "mistralai": "mistral",
    "moonshot": "moonshot",
    "moonshotai": "moonshot",
    "nvidia": "nvidia",
    "openai": "openai",
    "perplexity": "perplexity",
    "stepfun": "stepfun",
    "x-ai": "xai",
    "xai": "xai",
    "z-ai": "zhipu",
    "zhipu": "zhipu",
}

_LOCK = Lock()


def canonical_model_company(model_id: str) -> str:
    """Return a deterministic company identity from one direct model ID."""
    value = str(model_id or "").strip().casefold()
    author = value.split("/", 1)[0].strip() if "/" in value else value
    if not author:
        return "unknown"
    return MODEL_COMPANY_ALIASES.get(author, author)


def candidate_company(candidate: planner.CandidateNode | Mapping[str, Any]) -> str:
    if isinstance(candidate, Mapping):
        return canonical_model_company(str(candidate.get("model") or ""))
    return canonical_model_company(candidate.model)


def _order_key(row: planner.CandidateNode) -> tuple[float, float, float, str]:
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
    """Reserve company alternatives before model and Pareto supplements.

    A candidate can be dominated in isolation yet still be necessary to make a
    multi-node graph feasible under the no-company-reuse hard constraint.
    """
    limit = max(MINIMUM_CANDIDATES_PER_WORK, int(maximum_per_group))
    groups: dict[tuple[str, tuple[str, ...]], list[planner.CandidateNode]] = {}
    for candidate in candidates:
        groups.setdefault(
            (candidate.interpretation_id, candidate.coverage_keys), []
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
        for row in ordered:
            best_by_company.setdefault(candidate_company(row), row)

        best_by_model: dict[str, planner.CandidateNode] = {}
        for row in ordered:
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
            row.coverage_keys,
            candidate_company(row),
            -row.estimated_quality,
            row.estimated_cost,
            row.candidate_id,
        )
    )
    return kept


def _company_safe_recovery_pool(
    candidates: Sequence[planner.CandidateNode],
    selected: Sequence[planner.CandidateNode],
    interpretation_id: str,
    limits: GraphLimits,
    *,
    require_distinct_model_companies: bool,
) -> dict[str, list[dict[str, Any]]]:
    selected_ids = {row.candidate_id for row in selected}
    selected_companies = {candidate_company(row) for row in selected}
    result: dict[str, list[dict[str, Any]]] = {}
    for chosen in selected:
        alternatives = [
            row
            for row in candidates
            if row.interpretation_id == interpretation_id
            and row.coverage_keys == chosen.coverage_keys
            and row.candidate_id not in selected_ids
            and row.model != chosen.model
        ]
        alternatives.sort(key=_order_key)
        safe: list[dict[str, Any]] = []
        seen_companies: set[str] = set()
        for row in alternatives:
            company = candidate_company(row)
            if require_distinct_model_companies and company in selected_companies:
                continue
            if company in seen_companies:
                continue
            payload = row.to_dict()
            payload["model_company"] = company
            safe.append(payload)
            seen_companies.add(company)
            if len(safe) >= limits.max_replacements + 1:
                break
        result[chosen.candidate_id] = safe
    return result


def optimize_execution_graph(
    candidate_bundle: Mapping[str, Any],
    *,
    limits: GraphLimits | None = None,
    quality_tolerance_pct: float = 2.0,
    solver_timeout_seconds: float = 20.0,
    require_distinct_model_companies: bool = REQUIRE_DISTINCT_MODEL_COMPANIES,
) -> dict[str, Any]:
    """Optimize one graph with a global no-company-reuse hard constraint."""
    limits = limits or GraphLimits()
    candidates = planner._candidate_objects(candidate_bundle)
    interpretations = dict(candidate_bundle.get("interpretations", {}))
    if not candidates or not interpretations:
        raise planner.V5PlanningError("Candidate bundle is empty.")

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
        base_optimizer._scaled_cost(candidate) * x[index]
        for index, candidate in enumerate(candidates)
    ]
    initial_cost = sum(initial_cost_terms)

    company_indices: dict[str, list[int]] = defaultdict(list)
    for index, candidate in enumerate(candidates):
        company_indices[candidate_company(candidate)].append(index)
    if require_distinct_model_companies:
        for company, indices in sorted(company_indices.items()):
            if len(indices) > 1:
                model.Add(sum(x[index] for index in indices) <= 1)

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
            require_distinct_model = base_optimizer._work_requires_distinct_model(
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
                    "different_company_required": bool(
                        require_distinct_model_companies
                    ),
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
        quality_terms.append(
            int(round(score * base_optimizer.QUALITY_SCALE)) * x[index]
        )
    for interpretation_id, variable in y.items():
        interpretation_score = float(
            interpretations[interpretation_id]
            .get("metrics", {})
            .get("interpretation_score", 0.5)
        )
        quality_terms.append(
            int(
                round(
                    interpretation_score
                    * base_optimizer.QUALITY_SCALE
                    * 0.25
                )
            )
            * variable
        )
    quality_expr = sum(quality_terms)

    call_count = sum(x)
    call_overhead = max(
        1,
        int(
            round(
                base_optimizer.CALL_OVERHEAD_USD
                * base_optimizer.COST_SCALE
            )
        ),
    )
    expected_recovery_cost_terms = [
        base_optimizer._scaled_expected_recovery_cost(candidate) * x[index]
        for index, candidate in enumerate(candidates)
    ]
    expected_recovery_cost = sum(expected_recovery_cost_terms)
    expected_recovery_overhead_terms = [
        int(
            round(
                planner._clamp(candidate.failure_probability)
                * call_overhead
            )
        )
        * x[index]
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
        model.Add(
            effective_cost
            <= int(round(limits.max_budget_usd * base_optimizer.COST_SCALE))
        )

    try:
        solver, status, phase_status = base_optimizer._solve_cost_performance(
            model,
            quality_expr,
            effective_cost,
            solver_timeout_seconds,
        )
    except planner.V5PlanningError as exc:
        if require_distinct_model_companies:
            raise planner.V5PlanningError(
                f"{exc}; distinct-model-company hard constraint active; "
                f"available_companies={len(company_indices)}"
            ) from exc
        raise

    selected_indices = [
        index for index, variable in enumerate(x) if solver.Value(variable)
    ]
    selected_interpretations = [
        interpretation_id
        for interpretation_id, variable in y.items()
        if solver.Value(variable)
    ]
    if len(selected_interpretations) != 1:
        raise planner.V5PlanningError(
            "Solver did not select exactly one interpretation."
        )
    selected_interpretation = selected_interpretations[0]
    selected_candidates = [candidates[index] for index in selected_indices]
    selected_company_rows = [candidate_company(row) for row in selected_candidates]
    if (
        require_distinct_model_companies
        and len(selected_company_rows) != len(set(selected_company_rows))
    ):
        raise planner.V5PlanningError(
            "Selected graph violates the distinct-model-company hard constraint."
        )

    normalized_quality = planner._clamp(
        sum(candidates[index].estimated_quality for index in selected_indices)
        / max(1, len(selected_indices))
    )
    graph = planner._selected_graph(
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
    metadata["cost_performance_ratio_unit"] = (
        "risk_adjusted_utility_per_effective_expected_usd"
    )
    metadata["marginal_utility_stop"] = {
        "scope": "optional graph expansion across feasible task interpretations and candidate bundles",
        "criterion": "accept expansion only when it improves the global risk-adjusted utility/effective-expected-cost ratio",
        "tie_break": "for equal best ratio choose the lower effective expected cost",
        "mandatory_work_exception": "hard required coverage is never dropped for price",
    }
    metadata["independence_policy"] = {
        "hard_model_diversity_scope": "explicit-independence-groups-only",
        "hard_model_company_diversity_scope": "all-substantive-nodes-in-one-task",
        "hard_provider_diversity_scope": "none",
        "provider_diversity": "preferred-and-enforced-by-r8-runtime-rebalancing",
        "constraints": [
            row
            for row in hard_independence_constraints
            if row["interpretation_id"] == selected_interpretation
        ],
    }
    node_company_by_candidate_id = {
        row.candidate_id: candidate_company(row)
        for row in selected_candidates
    }
    metadata["model_company_policy"] = {
        "require_distinct_model_companies": bool(
            require_distinct_model_companies
        ),
        "company_identity_source": "canonicalized-direct-model-author-prefix",
        "selected_company_count": len(set(selected_company_rows)),
        "selected_companies": sorted(set(selected_company_rows)),
        "node_company_by_candidate_id": node_company_by_candidate_id,
        "candidate_company_count": len(company_indices),
        "candidate_pool_minimum_per_work": MINIMUM_CANDIDATES_PER_WORK,
        "same_company_reuse_allowed": not require_distinct_model_companies,
        "failure_policy": "fail-closed-before-model-call",
    }
    metadata["recovery_pool"] = _company_safe_recovery_pool(
        candidates,
        selected_candidates,
        selected_interpretation,
        limits,
        require_distinct_model_companies=require_distinct_model_companies,
    )
    metadata["recovery_company_policy"] = {
        "replacement_company_must_be_unused_by_selected_graph": bool(
            require_distinct_model_companies
        ),
        "one_best_replacement_per_company": True,
    }

    selected_quality = max(
        0,
        base_optimizer._value(solver, quality_expr),
    )
    selected_initial_cost = max(
        0,
        base_optimizer._value(solver, initial_cost),
    )
    selected_recovery_cost = max(
        0,
        base_optimizer._value(solver, expected_recovery_cost),
    )
    selected_effective_cost = max(
        1,
        base_optimizer._value(solver, effective_cost),
    )
    selected_expected_recovery_calls = sum(
        planner._clamp(candidates[index].failure_probability)
        for index in selected_indices
    )
    scaled_objective_ratio = selected_quality / selected_effective_cost
    public_cost_performance_ratio = (
        (selected_quality / base_optimizer.QUALITY_SCALE)
        / (selected_effective_cost / base_optimizer.COST_SCALE)
    )
    return {
        "version": 5,
        "optimizer": "google-or-tools-cp-sat",
        "selection_method": "execution-graph-expected-cost-performance-v5-distinct-model-companies",
        "solver_status": solver.StatusName(status),
        "phase_status": phase_status,
        "selected_interpretation": selected_interpretation,
        "highest_principle": "maximum_cost_performance",
        "objective_order": [
            "hard_constraints",
            "maximum_expected_cost_performance",
        ],
        "cost_performance_definition": (
            "risk_adjusted_task_utility_divided_by_initial_cost_plus_expected_recovery_cost_plus_call_overhead"
        ),
        "cost_performance_ratio_unit": (
            "risk_adjusted_utility_per_effective_expected_usd"
        ),
        "selected_quality_objective_scaled": selected_quality,
        "selected_initial_cost_scaled": selected_initial_cost,
        "selected_expected_recovery_cost_scaled": selected_recovery_cost,
        "selected_effective_cost_scaled": selected_effective_cost,
        "selected_initial_cost_usd": round(
            selected_initial_cost / base_optimizer.COST_SCALE,
            8,
        ),
        "selected_expected_recovery_cost_usd": round(
            selected_recovery_cost / base_optimizer.COST_SCALE,
            8,
        ),
        "selected_effective_cost_usd": round(
            selected_effective_cost / base_optimizer.COST_SCALE,
            8,
        ),
        "selected_expected_recovery_calls": round(
            selected_expected_recovery_calls,
            6,
        ),
        "quality_scale": base_optimizer.QUALITY_SCALE,
        "cost_scale": base_optimizer.COST_SCALE,
        "call_overhead_usd": base_optimizer.CALL_OVERHEAD_USD,
        "scaled_objective_ratio": round(scaled_objective_ratio, 9),
        "cost_performance_ratio": round(public_cost_performance_ratio, 9),
        "marginal_utility_stop": metadata["marginal_utility_stop"],
        "deprecated_quality_tolerance_pct_ignored": float(
            quality_tolerance_pct
        ),
        "selected_candidate_ids": [
            candidates[index].candidate_id for index in selected_indices
        ],
        "selected_model_companies": sorted(set(selected_company_rows)),
        "require_distinct_model_companies": bool(
            require_distinct_model_companies
        ),
        "hard_independence_constraints": hard_independence_constraints,
        "execution_graph": graph_data,
        "fallback_used": False,
    }


def risk_budgeted_optimize_execution_graph(
    candidate_bundle: Mapping[str, Any],
    *,
    limits: GraphLimits | None = None,
    quality_tolerance_pct: float = 2.0,
    solver_timeout_seconds: float = 20.0,
    require_distinct_model_companies: bool = REQUIRE_DISTINCT_MODEL_COMPANIES,
) -> dict[str, Any]:
    """Run the company-aware optimizer on the exact runtime risk budget."""
    limits = limits or GraphLimits()
    raw_budget = budget_parity.planning_raw_budget_usd(limits)
    planning_limits = replace(limits, max_budget_usd=raw_budget)
    iterations = budget_parity.adaptive_ratio_iterations(
        candidate_bundle,
        solver_timeout_seconds,
    )

    with _LOCK:
        previous = int(base_optimizer.MAX_RATIO_ITERATIONS)
        base_optimizer.MAX_RATIO_ITERATIONS = iterations
        try:
            result = optimize_execution_graph(
                candidate_bundle,
                limits=planning_limits,
                quality_tolerance_pct=quality_tolerance_pct,
                solver_timeout_seconds=solver_timeout_seconds,
                require_distinct_model_companies=(
                    require_distinct_model_companies
                ),
            )
        finally:
            base_optimizer.MAX_RATIO_ITERATIONS = previous

    graph = result.get("execution_graph")
    graph = graph if isinstance(graph, dict) else {}
    raw_cost = float(graph.get("estimated_total_cost", 0.0) or 0.0)
    multiplier = max(1.0, float(limits.cost_risk_multiplier))
    risk_cost = raw_cost * multiplier
    hard_budget = limits.max_budget_usd
    if hard_budget is not None and risk_cost > float(hard_budget) + 1e-12:
        raise planner.V5PlanningError(
            "Risk-adjusted selected graph exceeds the runtime hard budget after solve"
        )

    parity = {
        "hard_runtime_budget_usd": hard_budget,
        "planning_raw_budget_usd": raw_budget,
        "cost_risk_multiplier": multiplier,
        "selected_raw_cost_usd": round(raw_cost, 8),
        "selected_risk_adjusted_cost_usd": round(risk_cost, 8),
        "adaptive_ratio_iterations": iterations,
        "policy": "optimizer-raw-budget-equals-runtime-hard-budget-divided-by-risk-multiplier",
    }
    graph.setdefault("metadata", {})["budget_preflight_parity"] = parity
    result["budget_preflight_parity"] = parity
    result["adaptive_ratio_iterations"] = iterations
    return result
