"""Final current-task planner assembly including absolute reasoning-effort demand.

The older role planner used relative role-demand extrema.  For a one-role task (or
roles with equal demand), that collapses to an unconditional ``medium``.  This
layer keeps relative adaptation when it is informative, but also uses the current
task's absolute pressure so a simple one-role task can be low and a difficult
one-role task can be high.

The conversion from a continuous pressure signal to the finite OpenRouter effort
enum is an explicit infrastructure quantizer, not a hidden business default.
"""
from __future__ import annotations

from statistics import mean
from typing import Any, Mapping, Sequence

from v5_cost_effectiveness_parameter_closure import (
    PRINCIPLES,
    SCHEMA_VERSION as RESOURCE_SCHEMA_VERSION,
    build_runtime_planning_context as _resource_planning,
)

SCHEMA_VERSION = "runtime-planning-4-cost-effectiveness-absolute-reasoning"
_EFFORTS = ("low", "medium", "high")


def _pressure(profile: Mapping[str, Any]) -> float:
    raw = profile.get("pressure")
    raw = raw if isinstance(raw, Mapping) else {}
    values: list[float] = []
    for key in ("input", "constraint", "evidence", "delivery", "overall"):
        try:
            value = float(raw.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
        # Support either 0..1 or 0..100 planner representations.
        if value > 1.0:
            value /= 100.0
        values.append(max(0.0, min(1.0, value)))
    return max(0.0, min(1.0, mean(values) if values else 0.5))


def _absolute_effort(pressure: float) -> str:
    # Three equal regions correspond to the three production effort levels.
    # The boundaries are part of the API enum adapter, not business-domain data.
    index = min(len(_EFFORTS) - 1, int(pressure * len(_EFFORTS)))
    return _EFFORTS[index]


def _adjust_roles(planning: dict[str, Any]) -> dict[str, Any]:
    roles = [
        dict(row)
        for row in planning.get("role_plan") or []
        if isinstance(row, Mapping)
    ]
    if not roles:
        return planning
    profile = dict(planning.get("resolved_profile") or {})
    task_pressure = _pressure(profile)
    global_effort = _absolute_effort(task_pressure)
    demands = [float(row.get("role_structural_demand") or 0.0) for row in roles]
    minimum = min(demands)
    maximum = max(demands)
    global_index = _EFFORTS.index(global_effort)

    for index, row in enumerate(roles):
        if len(roles) == 1 or minimum == maximum:
            effort_index = global_index
            source = "current-task-absolute-pressure"
        else:
            relative = (
                1
                if demands[index] == maximum
                else (-1 if demands[index] == minimum else 0)
            )
            effort_index = max(
                0,
                min(len(_EFFORTS) - 1, global_index + relative),
            )
            source = "current-task-absolute-pressure-plus-current-role-relative-demand"
        row["reasoning_effort"] = _EFFORTS[effort_index]
        row["reasoning_effort_source"] = source
        row["reasoning_effort_task_pressure"] = round(task_pressure, 8)
        row["reasoning_effort_quantizer"] = (
            "three-equal-regions-over-current-task-pressure"
        )
        row["reasoning_effort_quantizer_classification"] = (
            "infrastructure_invariant"
        )
    planning["role_plan"] = roles

    resolved = dict(planning.get("resolved_parameters") or {})
    control = dict(resolved.get("control_surface_values") or {})
    role_policy = dict(control.get("role-reasoning-effort") or {})
    role_policy.update(
        {
            "mode": "current-task-absolute-pressure-plus-role-relative-demand",
            "current_task_pressure": round(task_pressure, 8),
            "current_role_efforts": {
                str(row.get("role_id")): str(row.get("reasoning_effort"))
                for row in roles
            },
            "single_role_unconditional_medium_used": False,
            "quantizer": "three-equal-regions-over-current-task-pressure",
            "quantizer_classification": "infrastructure_invariant",
        }
    )
    control["role-reasoning-effort"] = role_policy
    resolved["control_surface_values"] = control

    values = dict(resolved.get("parameter_values") or {})
    requirements = planning.get("parameter_requirements")
    requirements = requirements if isinstance(requirements, Mapping) else {}
    by_surface = requirements.get("control_surface_to_parameter_id")
    by_surface = by_surface if isinstance(by_surface, Mapping) else {}
    parameter_id = str(by_surface.get("role-reasoning-effort") or "")
    if parameter_id and parameter_id in values:
        row = dict(values[parameter_id])
        row["value"] = role_policy
        values[parameter_id] = row
    resolved["parameter_values"] = values
    planning["resolved_parameters"] = resolved

    profile["reasoning_effort_policy"] = role_policy
    profile["single_role_reasoning_effort_task_derived"] = True
    planning["resolved_profile"] = profile
    planning["single_role_reasoning_effort_task_derived"] = True
    planning["schema_version"] = SCHEMA_VERSION
    return planning


def build_runtime_planning_context(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return _adjust_roles(dict(_resource_planning(packet, candidates)))


__all__ = [
    "PRINCIPLES",
    "RESOURCE_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "build_runtime_planning_context",
]
