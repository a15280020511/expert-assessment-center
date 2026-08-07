"""OR-Tools assignment for arbitrary task-derived roles.

Unlike the compatibility optimizer, this active solver never maps generated roles into
a fixed semantic role family before scoring.  Each role is scored from its own current
structural profile, while recovery is scored against the heaviest role in this run.
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


class DynamicRoleAssignmentError(RuntimeError):
    """Raised when a finite current-role assignment cannot be constructed."""


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

    # Exact model identity cannot occupy two graph positions at once.  This is a
    # structural integrity invariant, not a model/company/provider eligibility gate.
    for index in range(len(candidates)):
        model.add(
            sum(active[index, role] for role in range(len(roles)))
            + recovery[index]
            <= 1
        )
    model.add(sum(recovery.values()) == int(recovery_count))

    tie_base = max(2, len(candidates) * max(1, len(roles)) + 1)
    terms: list[Any] = []
    for index, row in enumerate(candidates):
        model_id = str(row["model"])
        for role_index in range(len(roles)):
            metric = metrics_by_role[role_index][model_id]
            tie = index * max(1, len(roles)) + role_index
            terms.append(
                (
                    int(metric.get("objective_score") or 0) * tie_base + tie
                )
                * active[index, role_index]
            )
        recovery_metric = recovery_metrics[model_id]
        terms.append(
            (
                int(recovery_metric.get("objective_score") or 0) * tie_base + index
            )
            * recovery[index]
        )
    model.minimize(sum(terms))

    solver_profile = base._dynamic_solver_profile(  # noqa: SLF001
        profile,
        len(candidates),
        len(roles),
        recovery_count,
    )
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = int(
        solver_profile["num_search_workers"]
    )
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
        used: set[str] = set()
        selected: list[dict[str, Any]] = []
        for role_index, role in enumerate(roles):
            ranked = sorted(
                candidates,
                key=lambda row: (
                    int(
                        metrics_by_role[role_index][str(row["model"])].get(
                            "objective_score"
                        )
                        or 0
                    ),
                    float(
                        metrics_by_role[role_index][str(row["model"])].get(
                            "estimated_task_cost_usd"
                        )
                        or 0.0
                    ),
                    str(row["model"]),
                ),
            )
            source = next(
                (row for row in ranked if str(row["model"]) not in used),
                ranked[0],
            )
            used.add(str(source["model"]))
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
        backups = base._heuristic_recoveries(  # noqa: SLF001
            candidates,
            recovery_metrics,
            used,
            recovery_count,
        )
        return selected, backups, audit(fallback=True)

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
                base._annotate(row, recovery_metrics[str(row["model"])])  # noqa: SLF001
            )
    backups = base._annotate_recovery_rows(backups)  # noqa: SLF001
    return selected, backups, audit(fallback=False)


__all__ = ["DynamicRoleAssignmentError", "solve_dynamic_roles"]
