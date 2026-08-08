"""Cost-effectiveness-first OR-Tools assignment for current generated roles.

Priority is lexicographic and current-task only:
1. task-contract capability, capacity and current reliability risk;
2. task cost and marginal return (cost effectiveness);
3. company heterogeneity only when the higher-priority layers tie;
4. stable deterministic tie-break.

Cost, tokens and company diversity remain soft objectives, never eligibility gates.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

import v5_runtime_role_assignment as legacy
from v5_runtime_role_scoring import (
    RuntimeRoleScoringError,
    SCHEMA_VERSION as ROLE_SCORING_SCHEMA_VERSION,
    build_runtime_recovery_metrics,
    build_runtime_role_metrics,
)

SCHEMA_VERSION = "current-role-ortools-assignment-3-cost-effectiveness-first"
RuntimeRoleAssignmentError = legacy.RuntimeRoleAssignmentError
_CAPABILITY_KEYS = legacy._CAPABILITY_KEYS  # noqa: SLF001
_ECONOMY_KEYS = legacy._ECONOMY_KEYS  # noqa: SLF001
_company = legacy._company  # noqa: SLF001
_annotate = legacy._annotate  # noqa: SLF001
_objective_components = legacy._objective_components  # noqa: SLF001
_solver_profile = legacy._solver_profile  # noqa: SLF001
_heterogeneity_audit = legacy._heterogeneity_audit  # noqa: SLF001


def _fallback_rank_key(
    row: Mapping[str, Any],
    metric: Mapping[str, Any],
    used_companies: set[str],
) -> tuple[Any, ...]:
    capability, economy = _objective_components(metric)
    return (
        capability,
        economy,
        int(_company(row) in used_companies),
        float(metric.get("estimated_task_cost_usd") or 0.0),
        str(row.get("model") or ""),
    )


def _recovery_rows(
    candidates: Sequence[Mapping[str, Any]],
    recovery_metrics: Mapping[str, Mapping[str, Any]],
    selected_ids: set[str],
    selected_companies: set[str],
    recovery_count: int,
) -> list[dict[str, Any]]:
    used_ids = set(selected_ids)
    used_companies = set(selected_companies)
    result: list[dict[str, Any]] = []
    for slot in range(1, max(0, int(recovery_count)) + 1):
        ranked = sorted(
            (
                row
                for row in candidates
                if str(row.get("model") or "") not in used_ids
            ),
            key=lambda row: _fallback_rank_key(
                row,
                recovery_metrics[str(row["model"])],
                used_companies,
            ),
        )
        if not ranked:
            break
        source = ranked[0]
        model_id = str(source["model"])
        row = _annotate(source, recovery_metrics[model_id])
        row["slot"] = slot
        row["warm_recovery_priority"] = slot
        row["recovery_resilience"] = {
            "selection_source": (
                "capability-risk-then-cost-marginal-return-then-company-heterogeneity"
            ),
            "cost_effectiveness_priority": True,
            "hard_cost_gate": False,
            "hard_company_diversity_constraint": False,
            "company_heterogeneity_soft_objective": True,
            "provider_constraint": False,
            "capacity_hard_gate": False,
            "cross_task_history_used": False,
        }
        result.append(row)
        used_ids.add(model_id)
        used_companies.add(_company(source))
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
        raise RuntimeRoleAssignmentError(
            "current candidate inventory is smaller than current role plus recovery demand"
        )
    try:
        metrics_by_role = [
            build_runtime_role_metrics(candidates, profile, role) for role in roles
        ]
        recovery_metrics, recovery_shape = build_runtime_recovery_metrics(
            candidates,
            profile,
            roles,
        )
    except RuntimeRoleScoringError as exc:
        raise RuntimeRoleAssignmentError(str(exc)) from exc

    model = cp_model.CpModel()
    active = {
        (candidate_index, role_index): model.new_bool_var(
            f"active_{candidate_index}_{role_index}"
        )
        for candidate_index in range(len(candidates))
        for role_index in range(len(roles))
    }
    recovery = {
        index: model.new_bool_var(f"recovery_{index}")
        for index in range(len(candidates))
    }
    for role_index in range(len(roles)):
        model.add(
            sum(active[index, role_index] for index in range(len(candidates))) == 1
        )
    for index in range(len(candidates)):
        model.add(
            sum(active[index, role] for role in range(len(roles)))
            + recovery[index]
            <= 1
        )
    model.add(sum(recovery.values()) == int(recovery_count))

    company_indices: dict[str, list[int]] = {}
    for index, row in enumerate(candidates):
        company_indices.setdefault(_company(row), []).append(index)
    company_used = {
        company: model.new_bool_var(f"company_used_{position}")
        for position, company in enumerate(sorted(company_indices))
    }
    for company, indices in company_indices.items():
        position_vars = [
            active[index, role_index]
            for index in indices
            for role_index in range(len(roles))
        ] + [recovery[index] for index in indices]
        model.add(sum(position_vars) >= company_used[company])
        for variable in position_vars:
            model.add(company_used[company] >= variable)

    total_positions = len(roles) + int(recovery_count)
    max_tie_per_position = max(
        1,
        len(candidates) * (len(roles) + 1) + len(roles) + 1,
    )
    max_tie_sum = max(1, total_positions * max_tie_per_position)

    capability_terms: list[Any] = []
    economy_terms: list[Any] = []
    tie_terms: list[Any] = []
    economy_values: list[int] = []
    for index, row in enumerate(candidates):
        model_id = str(row["model"])
        for role_index in range(len(roles)):
            metric = metrics_by_role[role_index][model_id]
            capability, economy = _objective_components(metric)
            tie = index * max(1, len(roles)) + role_index
            capability_terms.append(capability * active[index, role_index])
            economy_terms.append(economy * active[index, role_index])
            economy_values.append(economy)
            tie_terms.append(tie * active[index, role_index])
        recovery_metric = recovery_metrics[model_id]
        capability, economy = _objective_components(recovery_metric)
        capability_terms.append(capability * recovery[index])
        economy_terms.append(economy * recovery[index])
        economy_values.append(economy)
        recovery_tie = len(candidates) * max(1, len(roles)) + index
        tie_terms.append(recovery_tie * recovery[index])

    # Economy must dominate heterogeneity, while both remain below capability/risk.
    max_economy_per_position = max([0, *economy_values])
    max_economy_sum = max(1, total_positions * max_economy_per_position)
    diversity_scale = max_tie_sum + 1
    economy_scale = total_positions * diversity_scale + max_tie_sum + 1
    capability_scale = max_economy_sum * economy_scale + total_positions * diversity_scale + max_tie_sum + 1
    repeated_company_positions = total_positions - sum(company_used.values())
    model.minimize(
        sum(capability_terms) * capability_scale
        + sum(economy_terms) * economy_scale
        + repeated_company_positions * diversity_scale
        + sum(tie_terms)
    )

    solver_profile = _solver_profile(profile, candidates, roles, recovery_count)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = int(solver_profile["num_search_workers"])
    solver.parameters.random_seed = int(solver_profile["random_seed"])
    solver.parameters.max_time_in_seconds = float(
        solver_profile["max_time_in_seconds"]
    )
    status = solver.solve(model)
    accepted = {cp_model.OPTIMAL, cp_model.FEASIBLE}

    def audit(*, fallback: bool) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "optimizer": (
                "ortools-cp-sat-with-current-signal-heuristic-fallback"
                if fallback
                else "ortools-cp-sat"
            ),
            "solver_status": solver.status_name(status),
            "optimality_proven": status == cp_model.OPTIMAL,
            "fallback_used": fallback,
            "dynamic_solver_profile": solver_profile,
            "role_scoring_schema_version": ROLE_SCORING_SCHEMA_VERSION,
            "role_metric_mode": "current-role-current-task-normalized-signals",
            "recovery_metric_mode": (
                "heaviest-current-generated-role-current-signal-normalization"
            ),
            "recovery_reference_role": dict(recovery_shape),
            "metric_role_adapter_used": False,
            "fixed_metric_role_grammar_used": False,
            "semantic_role_routing_used": False,
            "fixed_business_weight_coefficients_used": False,
            "fixed_business_solver_time_used": False,
            "cost_effectiveness_priority": True,
            "cost_is_business_gate": False,
            "company_heterogeneity_soft_objective": True,
            "company_diversity_is_execution_gate": False,
            "hard_company_diversity_constraint": False,
            "candidate_company_count": len(company_indices),
            "objective_priority": [
                "current-task-capability-and-capacity-risk",
                "current-task-cost-and-marginal-return",
                "maximize-distinct-company-coverage-on-higher-priority-tie",
                "stable-deterministic-tie-break",
            ],
            "objective_component_policy": {
                "capability_and_risk": [
                    *_CAPABILITY_KEYS,
                    "capacity_shortfall_penalty",
                ],
                "economy_and_marginal_return": list(_ECONOMY_KEYS),
                "company_diversity": "distinct-company-coverage-on-higher-priority-tie",
                "cross_task_history_used": False,
            },
            "objective_lexicographic_scales": {
                "capability_and_risk_scale": capability_scale,
                "economy_and_marginal_return_scale": economy_scale,
                "company_diversity_scale": diversity_scale,
                "stable_tie_scale": 1,
            },
            "cross_task_company_history_used": False,
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
        used_ids: set[str] = set()
        used_companies: set[str] = set()
        selected: list[dict[str, Any]] = []
        for role_index, role in enumerate(roles):
            ranked = sorted(
                candidates,
                key=lambda row: _fallback_rank_key(
                    row,
                    metrics_by_role[role_index][str(row["model"])],
                    used_companies,
                ),
            )
            source = next(
                row for row in ranked if str(row["model"]) not in used_ids
            )
            model_id = str(source["model"])
            used_ids.add(model_id)
            used_companies.add(_company(source))
            selected.append(
                {
                    **_annotate(source, metrics_by_role[role_index][model_id]),
                    **dict(role),
                    "slot": role_index + 1,
                }
            )
        recoveries = _recovery_rows(
            candidates,
            recovery_metrics,
            used_ids,
            used_companies,
            recovery_count,
        )
        result_audit = audit(fallback=True)
        result_audit.update(_heterogeneity_audit(selected, recoveries))
        result_audit["objective_priority"] = value_priority = audit(fallback=True)["objective_priority"]
        del value_priority
        return selected, recoveries, result_audit

    selected: list[dict[str, Any]] = []
    for role_index, role in enumerate(roles):
        matches = [
            index
            for index in range(len(candidates))
            if solver.value(active[index, role_index]) == 1
        ]
        source = candidates[matches[0]]
        model_id = str(source["model"])
        selected.append(
            {
                **_annotate(source, metrics_by_role[role_index][model_id]),
                **dict(role),
                "slot": role_index + 1,
            }
        )

    recoveries: list[dict[str, Any]] = []
    for index, row in enumerate(candidates):
        if solver.value(recovery[index]) == 1:
            recoveries.append(
                _annotate(row, recovery_metrics[str(row["model"])])
            )
    recoveries.sort(
        key=lambda row: (
            int(row.get("task_adaptive_capability_risk_score") or 0),
            int(row.get("task_adaptive_economy_score") or 0),
            float(row.get("estimated_task_cost_usd") or 0.0),
            str(row.get("model") or ""),
        )
    )
    for index, row in enumerate(recoveries, 1):
        row["slot"] = index
        row["warm_recovery_priority"] = index
        row["recovery_resilience"] = {
            "selection_source": (
                "capability-risk-then-cost-marginal-return-then-company-heterogeneity"
            ),
            "cost_effectiveness_priority": True,
            "hard_cost_gate": False,
            "hard_company_diversity_constraint": False,
            "company_heterogeneity_soft_objective": True,
            "provider_constraint": False,
            "capacity_hard_gate": False,
            "cross_task_history_used": False,
        }
    result_audit = audit(fallback=False)
    result_audit.update(_heterogeneity_audit(selected, recoveries))
    # Legacy heterogeneity audit carries the old priority string.  The actual
    # solver objective above is authoritative; overwrite audit semantics here.
    result_audit["objective_priority"] = audit(fallback=False)["objective_priority"]
    return selected, recoveries, result_audit


__all__ = ["RuntimeRoleAssignmentError", "SCHEMA_VERSION", "solve_runtime_roles"]
