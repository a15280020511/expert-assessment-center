"""Deterministic current-task scoring for Top-50 OR-Tools assignment.

The scorer implements three constitutional principles without model calls,
network access, keyword routing, or cross-task history:

1. concrete problem, concrete analysis: derive a workload profile from the
   current ticket's structure and evidence volume;
2. dynamic adaptation: vary role weights and native-capacity requirements with
   that current workload;
3. small effort, large return: rank estimated task USD per unit of relative
   reasoning quality so expensive marginal gains must earn their cost.

Provider data is intentionally absent from every score.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "v5-task-adaptive-value-scoring-1"
BASE_PROTOCOL_RESERVE = 8_192
MIN_PROMPT_TOKENS = 1_024
MIN_COMPLETION_TOKENS = 768
MAX_COMPLETION_ESTIMATE = 8_192
ROLE_IDS = ("evidence", "options", "review", "synthesis")
RECOVERY_ROLE_ID = "recovery"
PRINCIPLES = (
    "concrete-problem-concrete-analysis",
    "dynamic-adaptation",
    "small-effort-large-return",
)


class TaskAdaptiveScoringError(ValueError):
    """Raised when current-task scoring inputs are malformed."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _canonical_length(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        rendered = str(value)
    return len(rendered)


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _finite_nonnegative(value: Any) -> float:
    if isinstance(value, bool) or value is None or value == "":
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _count_named_outputs(task: Mapping[str, Any]) -> int:
    total = 0
    for key in ("required_outputs", "outputs", "deliverables", "required_fields"):
        total += len(_sequence(task.get(key)))
    return total


def build_task_demand_profile(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one auditable workload profile from the current ticket only.

    This is deliberately structural rather than semantic. No domain keyword is
    mapped to a preferred model. The profile only describes workload pressure,
    token volume, constraints, evidence, and delivery shape.
    """
    task = _mapping(packet.get("task"))
    evidence = packet.get("evidence")
    acceptance = packet.get("execution_acceptance")
    requirements = _sequence(task.get("requirements"))
    evidence_rows = _sequence(evidence)
    acceptance_rows = _sequence(acceptance)

    task_characters = _canonical_length(task)
    evidence_characters = _canonical_length(evidence)
    requirement_count = len(requirements)
    evidence_count = len(evidence_rows)
    acceptance_count = len(acceptance_rows)
    delivery_item_count = _count_named_outputs(task)
    extra_task_fields = max(0, len(task) - 3)

    # Deliberately conservative character-to-token upper estimate. The score is
    # for relative planning only; actual Token usage remains provider-audited.
    expected_prompt_tokens = max(
        MIN_PROMPT_TOKENS,
        math.ceil((task_characters + evidence_characters) / 2),
    )
    expected_completion_tokens = _clamp(
        MIN_COMPLETION_TOKENS
        + 160 * requirement_count
        + 96 * acceptance_count
        + 96 * delivery_item_count
        + min(1_024, task_characters // 16),
        MIN_COMPLETION_TOKENS,
        MAX_COMPLETION_ESTIMATE,
    )

    plan = _mapping(packet.get("governance_model_plan"))
    context_floors = [_positive_int(plan.get("required_context_tokens"))]
    context_floors.extend(
        _positive_int(row.get("required_context_tokens"))
        for row in candidates
        if isinstance(row, Mapping)
    )
    governance_context_floor = max(context_floors, default=0)

    input_pressure = _clamp(
        round((task_characters + evidence_characters) / 160), 0, 100
    )
    constraint_pressure = _clamp(
        (requirement_count + acceptance_count) * 10, 0, 100
    )
    evidence_pressure = _clamp(
        evidence_count * 10 + evidence_characters // 500, 0, 100
    )
    delivery_pressure = _clamp(
        delivery_item_count * 12 + extra_task_fields * 4, 0, 100
    )
    overall_pressure = _clamp(
        round(
            0.30 * input_pressure
            + 0.30 * constraint_pressure
            + 0.20 * evidence_pressure
            + 0.20 * delivery_pressure
        ),
        0,
        100,
    )

    profile: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "principles": list(PRINCIPLES),
        "source": "current-ticket-structural-signals-only",
        "semantic_keyword_routing_used": False,
        "domain_hardcoding_used": False,
        "cross_task_history_used": False,
        "provider_metric_used": False,
        "task_characters": task_characters,
        "evidence_characters": evidence_characters,
        "requirement_count": requirement_count,
        "evidence_count": evidence_count,
        "acceptance_count": acceptance_count,
        "delivery_item_count": delivery_item_count,
        "expected_prompt_tokens": expected_prompt_tokens,
        "expected_completion_tokens": expected_completion_tokens,
        "governance_context_floor": governance_context_floor,
        "pressure": {
            "input": input_pressure,
            "constraints": constraint_pressure,
            "evidence": evidence_pressure,
            "delivery": delivery_pressure,
            "overall": overall_pressure,
        },
    }
    profile["role_token_profiles"] = {
        role_id: role_token_profile(profile, role_id)
        for role_id in (*ROLE_IDS, RECOVERY_ROLE_ID)
    }
    return profile


def role_token_profile(profile: Mapping[str, Any], role_id: str) -> dict[str, int]:
    prompt = _positive_int(profile.get("expected_prompt_tokens")) or MIN_PROMPT_TOKENS
    completion = (
        _positive_int(profile.get("expected_completion_tokens"))
        or MIN_COMPLETION_TOKENS
    )
    governance_floor = _positive_int(profile.get("governance_context_floor"))

    if role_id in {"evidence", "options"}:
        prompt_tokens = prompt
        completion_tokens = completion
    elif role_id == "review":
        prompt_tokens = prompt + 2 * completion
        completion_tokens = math.ceil(completion * 1.05)
    elif role_id in {"synthesis", RECOVERY_ROLE_ID}:
        # A warm recovery can replace any primary role, including synthesis.
        # Therefore reserve the heaviest primary role's native capacity.
        prompt_tokens = prompt + 3 * completion
        completion_tokens = math.ceil(completion * 1.20)
    else:
        raise TaskAdaptiveScoringError(f"unknown role_id: {role_id}")

    required_context_tokens = max(
        governance_floor,
        BASE_PROTOCOL_RESERVE + prompt_tokens + completion_tokens,
    )
    return {
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "required_context_tokens": int(required_context_tokens),
    }


def dynamic_role_weights(profile: Mapping[str, Any], role_id: str) -> dict[str, int]:
    pressure = _positive_int(_mapping(profile.get("pressure")).get("overall"))
    pressure = _clamp(pressure, 0, 100)

    if role_id in {"evidence", "options"}:
        weights = {
            "task_cost": 55 - round(0.30 * pressure),
            "intelligence": 20 + round(0.35 * pressure),
            "weekly_popularity": 15,
            "capacity_headroom": 10 + round(0.10 * pressure),
            "marginal_return": 25,
        }
    elif role_id == "review":
        weights = {
            "task_cost": 42 - round(0.20 * pressure),
            "intelligence": 38 + round(0.35 * pressure),
            "weekly_popularity": 12,
            "capacity_headroom": 14 + round(0.10 * pressure),
            "marginal_return": 22,
        }
    elif role_id == "synthesis":
        weights = {
            "task_cost": 32 - round(0.15 * pressure),
            "intelligence": 52 + round(0.38 * pressure),
            "weekly_popularity": 10,
            "capacity_headroom": 16 + round(0.12 * pressure),
            "marginal_return": 18,
        }
    elif role_id == RECOVERY_ROLE_ID:
        weights = {
            "task_cost": 58 - round(0.25 * pressure),
            "intelligence": 22 + round(0.25 * pressure),
            "weekly_popularity": 15,
            "capacity_headroom": 12 + round(0.08 * pressure),
            "marginal_return": 30,
        }
    else:
        raise TaskAdaptiveScoringError(f"unknown role_id: {role_id}")
    return {key: max(1, int(value)) for key, value in weights.items()}


def estimate_task_cost_usd(
    candidate: Mapping[str, Any], role_tokens: Mapping[str, Any]
) -> float:
    prompt_rate = _finite_nonnegative(candidate.get("prompt_usd_per_million"))
    completion_rate = _finite_nonnegative(candidate.get("completion_usd_per_million"))
    request_fee = _finite_nonnegative(candidate.get("request_usd"))
    prompt_tokens = _positive_int(role_tokens.get("prompt_tokens"))
    completion_tokens = _positive_int(role_tokens.get("completion_tokens"))
    return (
        prompt_rate * prompt_tokens / 1_000_000
        + completion_rate * completion_tokens / 1_000_000
        + request_fee
    )


def _capacity(candidate: Mapping[str, Any], role_tokens: Mapping[str, Any]) -> tuple[bool, float]:
    required_context = _positive_int(role_tokens.get("required_context_tokens"))
    required_completion = _positive_int(role_tokens.get("completion_tokens"))
    context_length = _positive_int(candidate.get("context_length"))
    maximum_completion = _positive_int(candidate.get("max_completion_tokens"))

    compatible = True
    if context_length and required_context and context_length < required_context:
        compatible = False
    if maximum_completion and required_completion and maximum_completion < required_completion:
        compatible = False

    # Unknown native limits remain eligible for rollback-fixture compatibility,
    # but receive conservative headroom risk so known-capable models win ties.
    context_risk = required_context / context_length if context_length else 1.25
    completion_risk = (
        required_completion / maximum_completion if maximum_completion else 1.00
    )
    return compatible, float(context_risk + completion_risk)


def _rank_map(rows: Sequence[tuple[str, Any]]) -> dict[str, int]:
    ordered = sorted(rows, key=lambda item: (item[1], item[0]))
    return {model: rank for rank, (model, _) in enumerate(ordered, 1)}


def build_role_metrics(
    candidates: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    role_id: str,
) -> dict[str, dict[str, Any]]:
    """Return per-model integer objective metrics for one role."""
    if not candidates:
        raise TaskAdaptiveScoringError("candidate list is empty")
    tokens = role_token_profile(profile, role_id)
    weights = dynamic_role_weights(profile, role_id)

    raw: dict[str, dict[str, Any]] = {}
    for row in candidates:
        model = str(row.get("model") or "").strip()
        if not model or model in raw:
            raise TaskAdaptiveScoringError("candidate model identities must be unique")
        compatible, capacity_risk = _capacity(row, tokens)
        raw[model] = {
            "compatible": compatible,
            "estimated_task_cost_usd": estimate_task_cost_usd(row, tokens),
            "capacity_risk": capacity_risk,
            "official_intelligence_rank": _positive_int(
                row.get("official_intelligence_rank")
            )
            or 1_000_000,
            "weekly_popularity_rank": _positive_int(row.get("popularity_rank"))
            or 1_000_000,
        }

    cost_rank = _rank_map(
        [(model, values["estimated_task_cost_usd"]) for model, values in raw.items()]
    )
    intelligence_rank = _rank_map(
        [(model, values["official_intelligence_rank"]) for model, values in raw.items()]
    )
    popularity_rank = _rank_map(
        [(model, values["weekly_popularity_rank"]) for model, values in raw.items()]
    )
    capacity_rank = _rank_map(
        [(model, values["capacity_risk"]) for model, values in raw.items()]
    )

    candidate_count = len(raw)
    marginal_rows: list[tuple[str, float]] = []
    for model, values in raw.items():
        quality_utility = max(1, candidate_count + 1 - intelligence_rank[model])
        marginal_cost_per_quality = (
            values["estimated_task_cost_usd"] / quality_utility
        )
        values["quality_utility"] = quality_utility
        values["marginal_cost_per_quality"] = marginal_cost_per_quality
        marginal_rows.append((model, marginal_cost_per_quality))
    marginal_rank = _rank_map(marginal_rows)

    result: dict[str, dict[str, Any]] = {}
    for model, values in raw.items():
        ranks = {
            "task_cost": cost_rank[model],
            "intelligence": intelligence_rank[model],
            "weekly_popularity": popularity_rank[model],
            "capacity_headroom": capacity_rank[model],
            "marginal_return": marginal_rank[model],
        }
        objective = sum(weights[key] * ranks[key] for key in weights)
        result[model] = {
            **values,
            "role_id": role_id,
            "role_tokens": dict(tokens),
            "weights": dict(weights),
            "ranks": ranks,
            "objective_score": int(objective),
            "provider_metric_used": False,
        }
    return result


__all__ = [
    "PRINCIPLES",
    "RECOVERY_ROLE_ID",
    "ROLE_IDS",
    "SCHEMA_VERSION",
    "TaskAdaptiveScoringError",
    "build_role_metrics",
    "build_task_demand_profile",
    "dynamic_role_weights",
    "estimate_task_cost_usd",
    "role_token_profile",
]
