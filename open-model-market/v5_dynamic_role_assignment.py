"""OR-Tools assignment for arbitrary task-derived roles.

Unlike the compatibility optimizer, this active solver never maps generated roles into
a fixed semantic role family before scoring. Each role is scored from its own current
structural profile, while recovery is scored against the heaviest role in this run.

Company heterogeneity is a lexicographic soft objective. The active ordering is:
1. current-task capability and capacity/reliability risk;
2. maximize distinct-company coverage;
3. current-task cost and marginal-return efficiency;
4. stable deterministic tie-break.

No company count or uniqueness rule is a hard gate. A materially stronger/safer model
therefore beats diversity, while a merely cheaper same-company model does not crowd out
an equally capable model from a new company.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

import v5_top50_pool_optimizer as base
from v5_dynamic_role_scoring import (
    DynamicRoleScoringError,
    SCHEMA_VERSION as ROLE_SCORING_SCHEMA_VERSION,
    build_dynamic_recovery_metrics,
    build_dynamic_role_metrics,
)
from v5_model_company import canonical_model_company


class DynamicRoleAssignmentError(RuntimeError):
    """Raised when a finite current-role assignment cannot be constructed."""


_CAPABILITY_KEYS = ("intelligence", "weekly_popularity", "capacity_headroom")
_ECONOMY_KEYS = ("task_cost", "marginal_return")


def _company(row: Mapping[str, Any]) -> str:
    model = str(row.get("model") or "").strip()
    company = canonical_model_company(model)
    if company and company != "unknown":
        return company
    raw = str(row.get("company") or "").strip().casefold()
    return raw or "unknown"


def _objective_components(metric: Mapping[str, Any]) -> tuple[int, int]:
    """Split the existing dynamic metric into capability/risk and economy layers."""
    ranks = metric.get("ranks")
    weights = metric.get("weights")
    if not isinstance(ranks, Mapping) or not isinstance(weights, Mapping):
        # Compatibility for synthetic tests/legacy metrics. Production structural
        # scoring always supplies ranks+weights; old aggregate score remains primary.
        return int(metric.get("objective_score") or 0), 0

    def weighted(keys: Sequence[str]) -> int:
        return sum(
            int(weights.get(key) or 0) * int(ranks.get(key) or 0)
            for key in keys
        )

    capability = weighted(_CAPABILITY_KEYS) + int(
        metric.get("capacity_shortfall_penalty") or 0
    )
    economy = weighted(_ECONOMY_KEYS)
    return capability, economy


def _heterogeneity_audit(
    selected: Sequence[Mapping[str, Any]],
    backups: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    primary_companies = [_company(row) for row in selected]
    recovery_companies = [_company(row) for row in backups]
    sequence = [*primary_companies, *recovery_companies]
    distinct = len(set(sequence))
    return {
        "company_heterogeneity_soft_objective": True,
        "company_diversity_is_execution_gate": False,
        "hard_company_diversity_constraint": False,
        "objective_priority": [
            "current-task-capability-and-capacity-risk",
            "maximize-distinct-company-coverage",
            "current-task-cost-and-marginal-return",
            "stable-deterministic-tie-break",
        ],
        "primary_company_sequence": primary_companies,
        "recovery_company_sequence": recovery_companies,
        "assigned_company_sequence": sequence,
        "primary_distinct_company_count": len(set(primary_companies)),
        "assigned_distinct_company_count": distinct,
        "assigned_position_count": len(sequence),
        "company_heterogeneity_ratio": round(distinct / max(1, len(sequence)), 6),
        "same_company_position_reuse_count": max(0, len(sequence) - distinct),
        "cross_task_company_history_used": False,
    }


def _fallback_rank_key(
    row: Mapping[str, Any],
    metric: Mapping[str, Any],
    used_companies: set[str],
) -> tuple[Any, ...]:
    capability, economy = _objective_components(metric)
    return (
        capability,
        int(_company(row) in used_companies),
        economy,
        float(metric.get("estimated_task_cost_usd") or 0.0),
        str(row.get("model") or ""),
    )


def _fallback_recoveries(
    candidates: Sequence[Mapping[str, Any]],
    recovery_metrics: Mapping[str, Mapping[str, Any]],
    used_models: set[str],
    used_companies: set[str],
    recovery_count: int,
) -> list[dict[str, Any]]:
    backups: list[dict[str, Any]] = []
    for _ in range(max(0, int(recovery_count))):
        ranked = sorted(
            (
                row
                for row in candidates
                if str(row.get("model") or "") not in used_models
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
        backups.append(base._annotate(source, recovery_metrics[model_id]))  # noqa: SLF001
        used_models.add(model_id)
        used_companies.add(_company(source))
    return base._annotate_recovery_rows(backups)  # noqa: SLF001


def solve_dynamic_roles(
    candidates: list[dict[str, Any]],
    profile: Mapping[str, Any],
    roles: Sequence[Mapping[str, Any]],
    recovery_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not roles:
        raise DynamicRoleAssignmentError("dynamic role plan is empty")
    try:
        metrics_by_role = [
            build_dynamic_role_metrics(candidates, profile, role)
            for role in roles
        ]
        recovery_metrics, recovery_shape = build_dynamic_recovery_metrics(
            candidates,
            profile,
            roles,
        )
    except DynamicRoleScoringError as exc:
        raise DynamicRoleAssignmentError(str(exc)) from exc

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

    # Exact model identity cannot occupy two graph positions at once. This is a
    # structural integrity invariant, not a model/company/provider eligibility gate.
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

    # Exact integer scales encode a true lexicographic soft objective:
    # capability/risk > company diversity > economy/marginal return > stable tie.
    max_economy_per_position = max([0, *economy_values])
    max_economy_sum = max(1, total_positions * max_economy_per_position)
    economy_scale = max_tie_sum + 1
    diversity_scale = max_economy_sum * economy_scale + max_tie_sum + 1
    capability_scale = diversity_scale * (total_positions + 1)

    repeated_company_positions = total_positions - sum(company_used.values())
    model.minimize(
        sum(capability_terms) * capability_scale
        + repeated_company_positions * diversity_scale
        + sum(economy_terms) * economy_scale
        + sum(tie_terms)
    )

    solver_profile = base._dynamic_solver_profile(  # noqa: SLF001
        profile,
        len(candidates),
        len(roles),
        recovery_count,
    )
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = int(solver_profile["num_search_workers"])
    solver.parameters.random_seed = int(solver_profile["random_seed"])
    solver.parameters.max_time_in_seconds = float(
        solver_profile["max_time_in_seconds"]
    )
    status = solver.solve(model)
    accepted_statuses = {cp_model.OPTIMAL, cp_model.FEASIBLE}

    def audit(*, fallback: bool) -> dict[str, Any]:
        value: dict[str, Any] = {
            "optimizer": (
                "ortools-cp-sat-with-heuristic-fallback"
                if fallback
                else "ortools-cp-sat"
            ),
            "solver_status": solver.status_name(status),
            "optimality_proven": status == cp_model.OPTIMAL,
            "fallback_used": fallback,
            "dynamic_solver_profile": solver_profile,
            "role_scoring_schema_version": ROLE_SCORING_SCHEMA_VERSION,
            "role_metric_mode": "current-generated-role-structural-signals",
            "recovery_metric_mode": "heaviest-current-generated-role",
            "recovery_reference_role": dict(recovery_shape),
            "metric_role_adapter_used": False,
            "fixed_metric_role_grammar_used": False,
            "semantic_role_routing_used": False,
            "company_heterogeneity_soft_objective": True,
            "company_diversity_is_execution_gate": False,
            "hard_company_diversity_constraint": False,
            "candidate_company_count": len(company_indices),
            "objective_component_policy": {
                "capability_and_risk": [
                    *_CAPABILITY_KEYS,
                    "capacity_shortfall_penalty",
                ],
                "company_diversity": "distinct-company-coverage",
                "economy_and_marginal_return": list(_ECONOMY_KEYS),
                "cross_task_history_used": False,
            },
            "objective_lexicographic_scales": {
                "capability_and_risk_scale": capability_scale,
                "company_diversity_scale": diversity_scale,
                "economy_and_marginal_return_scale": economy_scale,
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

    if status not in accepted_statuses:
        used_models: set[str] = set()
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
                (row for row in ranked if str(row["model"]) not in used_models),
                ranked[0],
            )
            used_models.add(str(source["model"]))
            used_companies.add(_company(source))
            selected.append(
                {
                    **base._annotate(  # noqa: SLF001
                        source,
                        metrics_by_role[role_index][str(source["model"])],
                    ),
                    **dict(role),
                    "slot": role_index + 1,
                }
            )
        backups = _fallback_recoveries(
            candidates,
            recovery_metrics,
            used_models,
            used_companies,
            recovery_count,
        )
        result_audit = audit(fallback=True)
        result_audit.update(_heterogeneity_audit(selected, backups))
        return selected, backups, result_audit

    selected: list[dict[str, Any]] = []
    for role_index, role in enumerate(roles):
        matches = [
            index
            for index in range(len(candidates))
            if solver.value(active[index, role_index]) == 1
        ]
        index = matches[0]
        source = candidates[index]
        selected.append(
            {
                **base._annotate(  # noqa: SLF001
                    source,
                    metrics_by_role[role_index][str(source["model"])],
                ),
                **dict(role),
                "slot": role_index + 1,
            }
        )

    backups: list[dict[str, Any]] = []
    for index, row in enumerate(candidates):
        if solver.value(recovery[index]) == 1:
            backups.append(
                base._annotate(  # noqa: SLF001
                    row,
                    recovery_metrics[str(row["model"])],
                )
            )
    backups = base._annotate_recovery_rows(backups)  # noqa: SLF001
    result_audit = audit(fallback=False)
    result_audit.update(_heterogeneity_audit(selected, backups))
    return selected, backups, result_audit


__all__ = ["DynamicRoleAssignmentError", "solve_dynamic_roles"]
