"""Deterministic, constraint-faithful planning diagnostics for V5."""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

from v5_company_diversity import candidate_company
from v5_value_optimizer import CALL_OVERHEAD_USD

COST_SCALE = 1_000_000
UTILITY_SCALE = 100_000


def _coverage(candidate: Mapping[str, Any]) -> set[str]:
    return {str(x) for x in candidate.get("coverage_keys", [])}


def _risk_adjusted_utility(row: Mapping[str, Any]) -> float:
    quality = float(row.get("estimated_quality", 0.0))
    failure = max(
        0.0,
        min(1.0, float(row.get("failure_probability", 0.0))),
    )
    uncertainty = max(
        0.0,
        min(1.0, float(row.get("quality_uncertainty", 0.0))),
    )
    return max(
        0.0,
        quality * (1.0 - 0.35 * failure) - 0.10 * uncertainty,
    )


def _effective_expected_cost(row: Mapping[str, Any]) -> float:
    initial = max(0.0, float(row.get("estimated_cost", 0.0)))
    failure = max(
        0.0,
        min(1.0, float(row.get("failure_probability", 0.0))),
    )
    return initial * (1.0 + failure) + CALL_OVERHEAD_USD * (
        1.0 + failure
    )


def _constraint_map(
    optimization: Mapping[str, Any],
    interpretation_id: str,
) -> dict[str, dict[str, bool]]:
    result: dict[str, dict[str, bool]] = {}
    rows = optimization.get("hard_independence_constraints")
    if not isinstance(rows, list):
        return result
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or str(row.get("interpretation_id")) != interpretation_id
        ):
            continue
        work_id = str(row.get("work_id") or "")
        if not work_id:
            continue
        result[work_id] = {
            "different_model_required": bool(
                row.get("different_model_required")
            ),
            "different_company_required": bool(
                row.get("different_company_required")
            ),
            "different_provider_required": bool(
                row.get("different_provider_required")
            ),
        }
    return result


def _metrics(
    rows: Sequence[Mapping[str, Any]],
    required: set[str],
    copies_by_work: Mapping[str, Any],
    independence: Mapping[str, Mapping[str, bool]],
    interpretation_score: float,
    *,
    require_distinct_model_companies: bool,
) -> dict[str, Any]:
    covered = set().union(*(_coverage(row) for row in rows)) if rows else set()
    violations: list[str] = []
    for key in required:
        count = sum(key in _coverage(row) for row in rows)
        if count != 1:
            violations.append(f"{key}:selection-count={count}")

    if require_distinct_model_companies:
        companies = [candidate_company(row) for row in rows]
        counts = {
            company: companies.count(company)
            for company in sorted(set(companies))
            if companies.count(company) > 1
        }
        for company, count in counts.items():
            violations.append(
                f"model-company-reused:{company}:selection-count={count}"
            )

    for work_id, copies_raw in copies_by_work.items():
        copies = int(copies_raw)
        if copies < 2:
            continue
        selected_by_copy = []
        for copy_index in range(copies):
            key = f"{work_id}#{copy_index}"
            matches = [row for row in rows if key in _coverage(row)]
            if len(matches) == 1:
                selected_by_copy.append(matches[0])
        policy = independence.get(str(work_id), {})
        if (
            len(selected_by_copy) == copies
            and policy.get("different_model_required")
        ):
            models = [
                str(row.get("model") or "")
                for row in selected_by_copy
            ]
            if len(set(models)) != copies:
                violations.append(
                    f"{work_id}:independent-copies-reuse-model"
                )
        if (
            len(selected_by_copy) == copies
            and policy.get("different_company_required")
        ):
            companies = [
                candidate_company(row) for row in selected_by_copy
            ]
            if len(set(companies)) != copies:
                violations.append(
                    f"{work_id}:independent-copies-reuse-company"
                )
        if (
            len(selected_by_copy) == copies
            and policy.get("different_provider_required")
        ):
            endpoints = [
                str(row.get("provider_endpoint") or "")
                for row in selected_by_copy
            ]
            if len(set(endpoints)) != copies:
                violations.append(
                    f"{work_id}:independent-copies-reuse-endpoint"
                )

    utility = sum(_risk_adjusted_utility(row) for row in rows) + max(
        0.0,
        interpretation_score,
    ) * 0.25
    expected_recovery_cost = sum(
        max(0.0, float(row.get("estimated_cost", 0.0)))
        * max(
            0.0,
            min(1.0, float(row.get("failure_probability", 0.0))),
        )
        for row in rows
    )
    initial_cost = sum(
        max(0.0, float(row.get("estimated_cost", 0.0)))
        for row in rows
    )
    effective_cost = sum(_effective_expected_cost(row) for row in rows)
    quality = sum(
        float(row.get("estimated_quality", 0.0)) for row in rows
    ) / max(1, len(rows))
    ratio = utility / max(1e-12, effective_cost)
    companies = sorted({candidate_company(row) for row in rows})
    return {
        "feasible": required <= covered and not violations,
        "coverage_ratio": round(
            len(required & covered) / max(1, len(required)),
            6,
        ),
        "estimated_quality": round(quality, 6),
        "risk_adjusted_utility": round(utility, 8),
        "estimated_initial_cost_usd": round(initial_cost, 8),
        "estimated_recovery_cost_usd": round(
            expected_recovery_cost,
            8,
        ),
        "effective_expected_cost_usd": round(effective_cost, 8),
        "cost_performance_ratio": round(ratio, 9),
        "node_count": len(rows),
        "hard_constraint_violations": violations,
        "models": sorted(
            {str(row.get("model") or "") for row in rows}
        ),
        "model_companies": companies,
        "model_company_count": len(companies),
        "provider_endpoints": sorted(
            {str(row.get("provider_endpoint") or "") for row in rows}
        ),
    }


def _add_independence_constraints(
    model: cp_model.CpModel,
    variables: Sequence[Any],
    candidates: Sequence[Mapping[str, Any]],
    copies_by_work: Mapping[str, Any],
    independence: Mapping[str, Mapping[str, bool]],
    *,
    require_distinct_model_companies: bool,
) -> None:
    if require_distinct_model_companies:
        by_company: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(candidates):
            by_company[candidate_company(row)].append(index)
        for indices in by_company.values():
            if len(indices) > 1:
                model.Add(
                    sum(variables[index] for index in indices) <= 1
                )

    for work_id, copies_raw in copies_by_work.items():
        copies = int(copies_raw)
        policy = independence.get(str(work_id), {})
        if copies < 2 or not any(policy.values()):
            continue
        by_copy: dict[int, list[int]] = {}
        for copy_index in range(copies):
            key = f"{work_id}#{copy_index}"
            by_copy[copy_index] = [
                index
                for index, row in enumerate(candidates)
                if key in _coverage(row)
            ]
        for left_copy in range(copies):
            for right_copy in range(left_copy + 1, copies):
                for left in by_copy[left_copy]:
                    for right in by_copy[right_copy]:
                        same_model = str(
                            candidates[left].get("model")
                        ) == str(candidates[right].get("model"))
                        same_company = candidate_company(
                            candidates[left]
                        ) == candidate_company(candidates[right])
                        same_provider = str(
                            candidates[left].get("provider_endpoint")
                        ) == str(
                            candidates[right].get("provider_endpoint")
                        )
                        if (
                            policy.get("different_model_required")
                            and same_model
                        ) or (
                            policy.get("different_company_required")
                            and same_company
                        ) or (
                            policy.get("different_provider_required")
                            and same_provider
                        ):
                            model.Add(
                                variables[left] + variables[right] <= 1
                            )


def _solve_plan(
    required: set[str],
    candidates: Sequence[Mapping[str, Any]],
    copies_by_work: Mapping[str, Any],
    independence: Mapping[str, Mapping[str, bool]],
    *,
    maximum_nodes: int,
    objective: str,
    require_distinct_model_companies: bool,
    fixed_model: str | None = None,
    random_seed: int = 0,
) -> list[Mapping[str, Any]]:
    if not candidates or maximum_nodes <= 0:
        return []
    model = cp_model.CpModel()
    variables = [
        model.NewBoolVar(f"candidate_{index}")
        for index in range(len(candidates))
    ]
    for key in sorted(required):
        terms = [
            variables[index]
            for index, row in enumerate(candidates)
            if key in _coverage(row)
        ]
        if not terms:
            return []
        model.Add(sum(terms) == 1)
    model.Add(sum(variables) <= maximum_nodes)
    if fixed_model is not None:
        for index, row in enumerate(candidates):
            if str(row.get("model") or "") != fixed_model:
                model.Add(variables[index] == 0)
    _add_independence_constraints(
        model,
        variables,
        candidates,
        copies_by_work,
        independence,
        require_distinct_model_companies=(
            require_distinct_model_companies
        ),
    )

    if objective == "cost":
        expression = sum(
            max(
                1,
                int(round(_effective_expected_cost(row) * COST_SCALE)),
            )
            * variables[index]
            for index, row in enumerate(candidates)
        )
        model.Minimize(expression)
    elif objective == "quality":
        expression = sum(
            max(
                0,
                int(round(_risk_adjusted_utility(row) * UTILITY_SCALE)),
            )
            * variables[index]
            for index, row in enumerate(candidates)
        )
        model.Maximize(expression)
    elif objective == "random":
        rng = random.Random(random_seed)
        expression = sum(
            rng.randint(1, 100_000) * variables[index]
            for index in range(len(candidates))
        )
        model.Minimize(expression)
    else:
        raise ValueError(f"Unknown benchmark objective: {objective}")

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = random_seed
    status = solver.Solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        return []
    return [
        row
        for index, row in enumerate(candidates)
        if solver.Value(variables[index])
    ]


def _single_model_plan(
    required: set[str],
    candidates: Sequence[Mapping[str, Any]],
    copies_by_work: Mapping[str, Any],
    independence: Mapping[str, Mapping[str, bool]],
    *,
    maximum_nodes: int,
    strongest: bool,
    require_distinct_model_companies: bool,
) -> list[Mapping[str, Any]]:
    plans: list[list[Mapping[str, Any]]] = []
    for model_id in sorted(
        {str(row.get("model") or "") for row in candidates}
    ):
        plan = _solve_plan(
            required,
            candidates,
            copies_by_work,
            independence,
            maximum_nodes=maximum_nodes,
            objective="quality" if strongest else "cost",
            require_distinct_model_companies=(
                require_distinct_model_companies
            ),
            fixed_model=model_id,
        )
        if plan:
            plans.append(plan)
    if not plans:
        return []
    if strongest:
        return max(
            plans,
            key=lambda rows: (
                sum(_risk_adjusted_utility(row) for row in rows),
                -sum(_effective_expected_cost(row) for row in rows),
            ),
        )
    return min(
        plans,
        key=lambda rows: (
            sum(_effective_expected_cost(row) for row in rows),
            -sum(_risk_adjusted_utility(row) for row in rows),
        ),
    )


def planning_benchmark(
    planner_bundle: Mapping[str, Any],
    *,
    random_seed: int = 20260730,
) -> dict[str, Any]:
    optimization = planner_bundle["optimization"]
    graph = optimization["execution_graph"]
    interpretation_id = str(optimization["selected_interpretation"])
    meta = planner_bundle["candidate_graph"]["interpretations"][
        interpretation_id
    ]
    required = {
        f"{work_id}#{copy_index}"
        for work_id, copies in meta["copies_by_work"].items()
        for copy_index in range(int(copies))
    }
    candidates = [
        row
        for row in planner_bundle["candidate_graph"]["candidates"]
        if str(row["interpretation_id"]) == interpretation_id
    ]
    selected_ids = set(optimization["selected_candidate_ids"])
    selected = [
        row for row in candidates if row["candidate_id"] in selected_ids
    ]
    independence = _constraint_map(optimization, interpretation_id)
    require_distinct_companies = bool(
        optimization.get("require_distinct_model_companies")
        or graph.get("metadata", {})
        .get("model_company_policy", {})
        .get("require_distinct_model_companies")
    )
    maximum_nodes = max(1, len(graph.get("nodes") or selected))
    interpretation_score = float(
        meta.get("metrics", {}).get("interpretation_score", 0.5)
    )

    common = {
        "required": required,
        "candidates": candidates,
        "copies_by_work": meta["copies_by_work"],
        "independence": independence,
        "maximum_nodes": maximum_nodes,
        "require_distinct_model_companies": require_distinct_companies,
    }
    strongest_single = _single_model_plan(
        **common,
        strongest=True,
    )
    cheapest_single = _single_model_plan(
        **common,
        strongest=False,
    )
    random_plan = _solve_plan(
        **common,
        objective="random",
        random_seed=random_seed,
    )
    cheapest_feasible = _solve_plan(
        **common,
        objective="cost",
    )
    strongest_feasible = _solve_plan(
        **common,
        objective="quality",
    )

    def metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return _metrics(
            rows,
            required,
            meta["copies_by_work"],
            independence,
            interpretation_score,
            require_distinct_model_companies=(
                require_distinct_companies
            ),
        )

    strategies = {
        "v5_joint_graph": metrics(selected),
        "strongest_single_model": metrics(strongest_single),
        "lowest_price_single_model": metrics(cheapest_single),
        "random_feasible": metrics(random_plan),
        "lowest_cost_feasible": metrics(cheapest_feasible),
        "highest_utility_feasible": metrics(strongest_feasible),
    }
    v5 = strategies["v5_joint_graph"]
    feasible_comparators = {
        name: row
        for name, row in strategies.items()
        if name != "v5_joint_graph" and row["feasible"]
    }
    dominated = []
    for name, row in feasible_comparators.items():
        if (
            v5["risk_adjusted_utility"]
            >= row["risk_adjusted_utility"]
            and v5["effective_expected_cost_usd"]
            <= row["effective_expected_cost_usd"]
        ):
            dominated.append(name)
    best_comparator_ratio = max(
        (
            float(row["cost_performance_ratio"])
            for row in feasible_comparators.values()
        ),
        default=0.0,
    )
    ratio_proven = bool(
        feasible_comparators
        and v5["feasible"]
        and float(v5["cost_performance_ratio"]) + 1e-9
        >= best_comparator_ratio
    )
    gate = bool(
        v5["feasible"]
        and v5["coverage_ratio"] == 1.0
        and feasible_comparators
    )
    return {
        "version": 5,
        "benchmark_type": (
            "deterministic-constraint-faithful-planning-proof"
        ),
        "runtime_policy": "v5-only-no-alternate-runtime",
        "selected_interpretation": interpretation_id,
        "required_coverage_keys": sorted(required),
        "independence_policy": independence,
        "model_company_policy": {
            "require_distinct_model_companies": (
                require_distinct_companies
            ),
            "identity_source": (
                "canonicalized-direct-model-author-prefix"
            ),
        },
        "maximum_comparable_nodes": maximum_nodes,
        "strategies": strategies,
        "feasible_comparator_count": len(feasible_comparators),
        "best_comparator_cost_performance_ratio": round(
            best_comparator_ratio,
            9,
        ),
        "v5_pareto_dominates": sorted(dominated),
        "cost_performance_claim_allowed": ratio_proven,
        "value_proof_status": (
            "PROVEN_AGAINST_GENERATED_FEASIBLE_COMPARATORS"
            if ratio_proven
            else "NOT_PROVEN"
        ),
        "planning_gate_passed": gate,
        "planning_gate_definition": (
            "selected graph is feasible with complete coverage and at "
            "least one independently generated feasible comparator under "
            "the same model-company hard constraint"
        ),
        "execution_graph_summary": {
            "estimated_quality": graph["estimated_quality"],
            "estimated_total_cost": graph["estimated_total_cost"],
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
            "stage_count": len(graph["execution_stages"]),
        },
    }


def write_benchmark(path: str | Path, value: Mapping[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
