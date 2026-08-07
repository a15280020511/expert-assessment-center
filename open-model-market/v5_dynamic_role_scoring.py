"""Role scoring derived from the current generated role graph.

The active hierarchical planner must not collapse arbitrary task-derived roles into a
fixed evidence/options/review/synthesis metric grammar.  This module derives token
shape, scoring weights and recovery demand directly from the current role's structural
signals plus the current task profile.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "v5-current-role-structural-scoring-1"


class DynamicRoleScoringError(ValueError):
    """Raised when the current role/candidate data cannot be scored."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _finite_nonnegative(value: Any) -> float:
    if isinstance(value, bool) or value in (None, ""):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _pressure(profile: Mapping[str, Any], key: str) -> int:
    values = _mapping(profile.get("pressure"))
    try:
        return max(0, min(100, int(values.get(key) or 0)))
    except (TypeError, ValueError):
        return 0


def _rank_map(rows: Sequence[tuple[str, Any]]) -> dict[str, int]:
    ordered = sorted(rows, key=lambda item: (item[1], item[0]))
    return {model: rank for rank, (model, _) in enumerate(ordered, 1)}


def role_structural_profile(
    profile: Mapping[str, Any],
    role: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve one role's planning demand without a semantic role class."""
    assigned = len(_sequence(role.get("assigned_work_units")))
    dependencies = len(_sequence(role.get("depends_on_role_ids")))
    functions = len(_sequence(role.get("functions")))
    final_role = role.get("final_role") is True

    work_units = max(1, assigned)
    structural_units = max(
        1,
        work_units + dependencies + max(1, functions) + int(final_role),
    )
    overall = _pressure(profile, "overall")
    input_pressure = _pressure(profile, "input")
    constraint_pressure = _pressure(profile, "constraints")
    evidence_pressure = _pressure(profile, "evidence")
    delivery_pressure = _pressure(profile, "delivery")

    structural_pressure = min(
        100,
        7 * work_units
        + 11 * dependencies
        + 4 * max(1, functions)
        + (13 if final_role else 0),
    )
    dependency_ratio = dependencies / max(1, work_units + dependencies)
    base_prompt = max(1, _positive_int(profile.get("expected_prompt_tokens")))
    base_completion = max(
        1, _positive_int(profile.get("expected_completion_tokens"))
    )
    reserve = max(0, _positive_int(profile.get("protocol_reserve_tokens")))
    governance_floor = max(
        0, _positive_int(profile.get("governance_context_floor"))
    )

    local_reserve = math.ceil(
        reserve * min(2.0, 0.5 + math.log2(structural_units + 1) / 2.0)
    )
    prompt_tokens = (
        base_prompt
        + dependencies * base_completion
        + max(0, work_units - 1) * max(1, local_reserve // structural_units)
    )
    completion_pressure = min(
        1.5,
        (
            overall
            + structural_pressure
            + constraint_pressure / 2.0
            + delivery_pressure / 2.0
        )
        / 260.0,
    )
    completion_tokens = math.ceil(base_completion * (1.0 + completion_pressure))
    required_context_tokens = (
        governance_floor + local_reserve + prompt_tokens + completion_tokens
    )

    # These are objective coefficients, not business gates.  Every weight is resolved
    # from the current task/role shape; no role identity or semantic keyword is read.
    task_cost = max(
        5,
        72 - round(0.38 * overall) - round(0.22 * structural_pressure),
    )
    intelligence = max(
        5,
        10
        + round(0.32 * overall)
        + round(0.18 * structural_pressure)
        + round(0.12 * constraint_pressure),
    )
    popularity = max(
        3,
        7
        + round(0.08 * evidence_pressure)
        + round(0.05 * structural_pressure),
    )
    capacity = max(
        5,
        8
        + round(0.15 * input_pressure)
        + round(0.12 * delivery_pressure)
        + round(0.14 * structural_pressure)
        + round(10 * dependency_ratio),
    )
    marginal = max(
        5,
        40
        - round(0.18 * overall)
        - round(0.08 * structural_pressure)
        + round(6 * (1.0 - dependency_ratio)),
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "role_id": str(role.get("role_id") or "dynamic-role"),
        "source": "current-generated-role-structural-signals",
        "assigned_work_unit_count": assigned,
        "dependency_count": dependencies,
        "function_count": functions,
        "final_role": final_role,
        "structural_units": structural_units,
        "structural_pressure": int(structural_pressure),
        "dependency_ratio": round(float(dependency_ratio), 8),
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "required_context_tokens": int(required_context_tokens),
        "protocol_reserve_tokens": int(local_reserve),
        "weights": {
            "task_cost": int(task_cost),
            "intelligence": int(intelligence),
            "weekly_popularity": int(popularity),
            "capacity_headroom": int(capacity),
            "marginal_return": int(marginal),
        },
        "fixed_metric_role_class_used": False,
        "semantic_role_routing_used": False,
    }


def _estimate_task_cost_usd(
    candidate: Mapping[str, Any], role_shape: Mapping[str, Any]
) -> float:
    return (
        _finite_nonnegative(candidate.get("prompt_usd_per_million"))
        * _positive_int(role_shape.get("prompt_tokens"))
        / 1_000_000
        + _finite_nonnegative(candidate.get("completion_usd_per_million"))
        * _positive_int(role_shape.get("completion_tokens"))
        / 1_000_000
        + _finite_nonnegative(candidate.get("request_usd"))
    )


def _capacity(
    candidate: Mapping[str, Any], role_shape: Mapping[str, Any]
) -> tuple[bool, float, float]:
    required_context = _positive_int(role_shape.get("required_context_tokens"))
    required_completion = _positive_int(role_shape.get("completion_tokens"))
    context_length = _positive_int(candidate.get("context_length"))
    maximum_completion = _positive_int(candidate.get("max_completion_tokens"))
    context_ratio = required_context / context_length if context_length else 1.25
    completion_ratio = (
        required_completion / maximum_completion if maximum_completion else 1.0
    )
    compatible = not (
        (context_length and required_context > context_length)
        or (maximum_completion and required_completion > maximum_completion)
    )
    shortfall = max(0.0, context_ratio - 1.0) + max(
        0.0, completion_ratio - 1.0
    )
    return compatible, float(context_ratio + completion_ratio), float(shortfall)


def _metrics_for_shape(
    candidates: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    role_shape: Mapping[str, Any],
    *,
    metric_source: str,
) -> dict[str, dict[str, Any]]:
    if not candidates:
        raise DynamicRoleScoringError("candidate list is empty")
    raw: dict[str, dict[str, Any]] = {}
    for row in candidates:
        model = str(row.get("model") or "").strip()
        if not model or model in raw:
            raise DynamicRoleScoringError(
                "candidate model identities must be unique"
            )
        compatible, capacity_risk, capacity_shortfall = _capacity(row, role_shape)
        popularity = _positive_int(row.get("popularity_rank")) or 1_000_000
        raw[model] = {
            "compatible": compatible,
            "estimated_task_cost_usd": _estimate_task_cost_usd(row, role_shape),
            "capacity_risk": capacity_risk,
            "capacity_shortfall": capacity_shortfall,
            "official_intelligence_rank": _positive_int(
                row.get("official_intelligence_rank")
            )
            or 1_000_000,
            "weekly_popularity_rank": popularity,
            "popularity_rank": popularity,
        }

    cost_rank = _rank_map(
        [(model, row["estimated_task_cost_usd"]) for model, row in raw.items()]
    )
    intelligence_rank = _rank_map(
        [(model, row["official_intelligence_rank"]) for model, row in raw.items()]
    )
    popularity_rank = _rank_map(
        [(model, row["weekly_popularity_rank"]) for model, row in raw.items()]
    )
    capacity_rank = _rank_map(
        [(model, row["capacity_risk"]) for model, row in raw.items()]
    )
    candidate_count = len(raw)
    marginal_rows: list[tuple[str, float]] = []
    for model, row in raw.items():
        quality_utility = max(1, candidate_count + 1 - intelligence_rank[model])
        marginal = row["estimated_task_cost_usd"] / quality_utility
        row["quality_utility"] = quality_utility
        row["marginal_cost_per_quality"] = marginal
        marginal_rows.append((model, marginal))
    marginal_rank = _rank_map(marginal_rows)

    weights = dict(_mapping(role_shape.get("weights")))
    pressure = _pressure(profile, "overall")
    result: dict[str, dict[str, Any]] = {}
    for model, values in raw.items():
        ranks = {
            "task_cost": cost_rank[model],
            "intelligence": intelligence_rank[model],
            "weekly_popularity": popularity_rank[model],
            "capacity_headroom": capacity_rank[model],
            "marginal_return": marginal_rank[model],
        }
        base_objective = sum(int(weights[key]) * ranks[key] for key in ranks)
        shortfall_scale = max(
            1,
            sum(int(value) for value in weights.values())
            * max(1, candidate_count)
            * (1 + pressure)
            // 50,
        )
        shortfall_penalty = int(
            round(float(values["capacity_shortfall"]) * shortfall_scale)
        )
        result[model] = {
            **values,
            "role_id": str(role_shape.get("role_id") or "dynamic-role"),
            "role_tokens": {
                "prompt_tokens": int(role_shape["prompt_tokens"]),
                "completion_tokens": int(role_shape["completion_tokens"]),
                "required_context_tokens": int(role_shape["required_context_tokens"]),
                "protocol_reserve_tokens": int(role_shape["protocol_reserve_tokens"]),
                "dependency_fan_in": int(role_shape.get("dependency_count") or 0),
            },
            "weights": weights,
            "ranks": ranks,
            "base_objective_score": int(base_objective),
            "capacity_shortfall_penalty": shortfall_penalty,
            "objective_score": int(base_objective + shortfall_penalty),
            "provider_metric_used": False,
            "capacity_is_hard_gate": False,
            "metric_source": metric_source,
            "fixed_metric_role_class_used": False,
            "role_structural_profile": dict(role_shape),
        }
    return result


def build_dynamic_role_metrics(
    candidates: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    role: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    shape = role_structural_profile(profile, role)
    return _metrics_for_shape(
        candidates,
        profile,
        shape,
        metric_source="current-generated-role-structural-signals",
    )


def build_dynamic_recovery_metrics(
    candidates: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    roles: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Score recovery against the heaviest role generated for this task."""
    shapes = [role_structural_profile(profile, role) for role in roles]
    if not shapes:
        raise DynamicRoleScoringError("recovery scoring needs at least one active role")
    shape = max(
        shapes,
        key=lambda row: (
            int(row.get("required_context_tokens") or 0),
            int(row.get("completion_tokens") or 0),
            int(row.get("structural_pressure") or 0),
            str(row.get("role_id") or ""),
        ),
    )
    recovery_shape = dict(shape)
    recovery_shape["role_id"] = "runtime-recovery-for-heaviest-current-role"
    recovery_shape["source"] = "heaviest-current-generated-role"
    return (
        _metrics_for_shape(
            candidates,
            profile,
            recovery_shape,
            metric_source="heaviest-current-generated-role",
        ),
        recovery_shape,
    )


__all__ = [
    "DynamicRoleScoringError",
    "SCHEMA_VERSION",
    "build_dynamic_recovery_metrics",
    "build_dynamic_role_metrics",
    "role_structural_profile",
]
