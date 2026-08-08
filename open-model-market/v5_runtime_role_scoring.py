"""Model scoring for arbitrary current roles without fixed business coefficients."""
from __future__ import annotations

import math
from statistics import mean
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "current-role-signal-normalized-scoring-1"


class RuntimeRoleScoringError(ValueError):
    """Raised when current role/candidate inputs cannot be scored."""


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


def _pressure(profile: Mapping[str, Any], key: str) -> float:
    values = _mapping(profile.get("pressure"))
    try:
        return max(0.0, min(1.0, float(values.get(key) or 0) / 100.0))
    except (TypeError, ValueError):
        return 0.0


def _rank_map(rows: Sequence[tuple[str, Any]]) -> dict[str, int]:
    ordered = sorted(rows, key=lambda item: (item[1], item[0]))
    return {model: rank for rank, (model, _) in enumerate(ordered, 1)}


def _normalized_integer_weights(
    strengths: Mapping[str, float],
    candidate_count: int,
) -> dict[str, int]:
    cleaned = {key: max(0.0, float(value)) for key, value in strengths.items()}
    total = sum(cleaned.values())
    if total <= 0:
        cleaned = {key: 1.0 for key in cleaned}
        total = float(len(cleaned))
    scale = max(1, candidate_count * max(1, len(cleaned)))
    return {
        key: max(1, round(value / total * scale))
        for key, value in cleaned.items()
    }


def role_structural_profile(
    profile: Mapping[str, Any],
    role: Mapping[str, Any],
) -> dict[str, Any]:
    assigned = len(_sequence(role.get("assigned_work_units")))
    dependencies = len(_sequence(role.get("depends_on_role_ids")))
    functions = len(_sequence(role.get("functions")))
    final_role = role.get("final_role") is True
    work_units = max(1, assigned)
    structural_units = max(1, work_units + dependencies + max(1, functions) + int(final_role))
    task_units = max(1, _positive_int(profile.get("work_unit_count")))
    structural_ratio = structural_units / max(1, structural_units + task_units)
    dependency_ratio = dependencies / max(1, work_units + dependencies)

    base_prompt = max(1, _positive_int(profile.get("expected_prompt_tokens")))
    base_completion = max(1, _positive_int(profile.get("expected_completion_tokens")))
    reserve = max(1, _positive_int(profile.get("protocol_reserve_tokens")))
    governance_floor = max(0, _positive_int(profile.get("governance_context_floor")))
    local_reserve = max(1, math.ceil(reserve * math.sqrt(structural_units)))
    prompt_tokens = (
        base_prompt
        + dependencies * base_completion
        + max(0, work_units - 1) * local_reserve
    )

    relevant_pressures = [
        _pressure(profile, "overall"),
        _pressure(profile, "constraints"),
        _pressure(profile, "delivery"),
        structural_ratio,
    ]
    completion_pressure = mean(relevant_pressures)
    completion_tokens = max(1, math.ceil(base_completion * (1.0 + completion_pressure)))
    required_context_tokens = governance_floor + local_reserve + prompt_tokens + completion_tokens

    input_signal = _pressure(profile, "input")
    evidence_signal = _pressure(profile, "evidence")
    constraint_signal = _pressure(profile, "constraints")
    delivery_signal = _pressure(profile, "delivery")
    overall_signal = _pressure(profile, "overall")
    quality_signal = mean((overall_signal, structural_ratio, constraint_signal))
    cost_signal = max(0.0, 1.0 - quality_signal)
    capacity_signal = mean((input_signal, delivery_signal, structural_ratio, dependency_ratio))
    popularity_signal = mean((max(0.0, 1.0 - evidence_signal), delivery_signal))
    marginal_signal = math.sqrt(max(0.0, cost_signal * quality_signal))
    strengths = {
        "task_cost": cost_signal,
        "intelligence": quality_signal,
        "weekly_popularity": popularity_signal,
        "capacity_headroom": capacity_signal,
        "marginal_return": marginal_signal,
    }
    candidate_count = max(1, _positive_int(profile.get("candidate_count")))
    weights = _normalized_integer_weights(strengths, candidate_count)

    return {
        "schema_version": SCHEMA_VERSION,
        "role_id": str(role.get("role_id") or "dynamic-role"),
        "source": "current-role-and-current-task-signal-normalization",
        "assigned_work_unit_count": assigned,
        "dependency_count": dependencies,
        "function_count": functions,
        "final_role": final_role,
        "structural_units": structural_units,
        "structural_pressure": round(100 * structural_ratio),
        "structural_ratio": round(structural_ratio, 8),
        "dependency_ratio": round(dependency_ratio, 8),
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "required_context_tokens": int(required_context_tokens),
        "protocol_reserve_tokens": int(local_reserve),
        "weights": weights,
        "weight_strengths": {key: round(value, 8) for key, value in strengths.items()},
        "weight_derivation": "normalize-current-role-and-task-signals",
        "fixed_business_weight_coefficients_used": False,
        "fixed_metric_role_class_used": False,
        "semantic_role_routing_used": False,
    }


def _estimate_task_cost_usd(
    candidate: Mapping[str, Any],
    role_shape: Mapping[str, Any],
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
    candidate: Mapping[str, Any],
    role_shape: Mapping[str, Any],
) -> tuple[bool, float, float]:
    required_context = _positive_int(role_shape.get("required_context_tokens"))
    required_completion = _positive_int(role_shape.get("completion_tokens"))
    context_length = _positive_int(candidate.get("context_length"))
    maximum_completion = _positive_int(candidate.get("max_completion_tokens"))
    context_ratio = required_context / context_length if context_length else 0.0
    completion_ratio = required_completion / maximum_completion if maximum_completion else 0.0
    compatible = not (
        (context_length and required_context > context_length)
        or (maximum_completion and required_completion > maximum_completion)
    )
    shortfall = max(0.0, context_ratio - 1.0) + max(0.0, completion_ratio - 1.0)
    return compatible, float(context_ratio + completion_ratio), float(shortfall)


def _metrics_for_shape(
    candidates: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    role_shape: Mapping[str, Any],
    *,
    metric_source: str,
) -> dict[str, dict[str, Any]]:
    if not candidates:
        raise RuntimeRoleScoringError("candidate list is empty")
    raw: dict[str, dict[str, Any]] = {}
    for row in candidates:
        model = str(row.get("model") or "").strip()
        if not model or model in raw:
            raise RuntimeRoleScoringError("candidate model identities must be unique")
        compatible, capacity_risk, capacity_shortfall = _capacity(row, role_shape)
        popularity = _positive_int(row.get("popularity_rank")) or 1_000_000
        raw[model] = {
            "compatible": compatible,
            "estimated_task_cost_usd": _estimate_task_cost_usd(row, role_shape),
            "capacity_risk": capacity_risk,
            "capacity_shortfall": capacity_shortfall,
            "official_intelligence_rank": _positive_int(row.get("official_intelligence_rank")) or 1_000_000,
            "weekly_popularity_rank": popularity,
            "popularity_rank": popularity,
        }

    cost_rank = _rank_map([(model, row["estimated_task_cost_usd"]) for model, row in raw.items()])
    intelligence_rank = _rank_map([(model, row["official_intelligence_rank"]) for model, row in raw.items()])
    popularity_rank = _rank_map([(model, row["weekly_popularity_rank"]) for model, row in raw.items()])
    capacity_rank = _rank_map([(model, row["capacity_risk"]) for model, row in raw.items()])
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
        shortfall_scale = max(1, sum(int(value) for value in weights.values()) * max(1, candidate_count))
        shortfall_penalty = int(round(float(values["capacity_shortfall"]) * shortfall_scale * (1.0 + pressure)))
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
            "weight_strengths": dict(role_shape.get("weight_strengths") or {}),
            "weight_derivation": "normalize-current-role-and-task-signals",
            "ranks": ranks,
            "base_objective_score": int(base_objective),
            "capacity_shortfall_penalty": shortfall_penalty,
            "objective_score": int(base_objective + shortfall_penalty),
            "provider_metric_used": False,
            "capacity_is_hard_gate": False,
            "metric_source": metric_source,
            "fixed_business_weight_coefficients_used": False,
            "fixed_metric_role_class_used": False,
            "role_structural_profile": dict(role_shape),
        }
    return result


def build_runtime_role_metrics(
    candidates: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    role: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    enriched_profile = {**dict(profile), "candidate_count": len(candidates)}
    shape = role_structural_profile(enriched_profile, role)
    return _metrics_for_shape(
        candidates,
        enriched_profile,
        shape,
        metric_source="current-role-current-task-normalized-signals",
    )


def build_runtime_recovery_metrics(
    candidates: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    roles: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    enriched_profile = {**dict(profile), "candidate_count": len(candidates)}
    shapes = [role_structural_profile(enriched_profile, role) for role in roles]
    if not shapes:
        raise RuntimeRoleScoringError("recovery scoring needs at least one active role")
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
    recovery_shape["role_id"] = "runtime-recovery-current-heaviest-role"
    recovery_shape["source"] = "heaviest-current-generated-role"
    return (
        _metrics_for_shape(
            candidates,
            enriched_profile,
            recovery_shape,
            metric_source="heaviest-current-generated-role-current-signal-normalization",
        ),
        recovery_shape,
    )


__all__ = [
    "RuntimeRoleScoringError",
    "SCHEMA_VERSION",
    "build_runtime_recovery_metrics",
    "build_runtime_role_metrics",
    "role_structural_profile",
]
