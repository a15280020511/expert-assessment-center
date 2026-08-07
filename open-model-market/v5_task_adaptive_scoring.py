"""Current-task mathematical scoring for fully dynamic expert composition.

All planning values that can be derived from the current ticket are recomputed
per task: workload pressure, prompt/output estimates, protocol reserve, role
fan-in, native-capacity demand, role weights, task cost, quality/cost marginal
return and capacity-shortfall penalty.  No domain keyword, Provider metric,
cross-task history, fixed team count or fixed model class participates.

Native model limits are telemetry/objective risk, not a hard model-admission
gate.  The only hard model-execution boundary is enforced elsewhere by the
no-tools policy.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "v5-fully-dynamic-task-value-scoring-2"
# Compatibility names only.  Runtime values are derived into the task profile;
# these constants are deliberately not used as planning gates.
BASE_PROTOCOL_RESERVE = 0
MIN_PROMPT_TOKENS = 0
MIN_COMPLETION_TOKENS = 0
MAX_COMPLETION_ESTIMATE = 0
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
    return (
        value
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
        else ()
    )


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
    for key in (
        "required_outputs",
        "outputs",
        "deliverables",
        "required_fields",
    ):
        total += len(_sequence(task.get(key)))
    return total


def _candidate_native_statistics(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    contexts = sorted(
        _positive_int(row.get("context_length"))
        for row in candidates
        if isinstance(row, Mapping) and _positive_int(row.get("context_length"))
    )
    completions = sorted(
        _positive_int(row.get("max_completion_tokens"))
        for row in candidates
        if isinstance(row, Mapping)
        and _positive_int(row.get("max_completion_tokens"))
    )

    def median(values: list[int]) -> int:
        if not values:
            return 0
        return int(values[len(values) // 2])

    return {
        "known_context_count": len(contexts),
        "known_completion_count": len(completions),
        "median_context_tokens": median(contexts),
        "median_completion_tokens": median(completions),
        "maximum_context_tokens": max(contexts, default=0),
        "maximum_completion_tokens": max(completions, default=0),
    }


def build_task_demand_profile(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one auditable workload profile from the current ticket only."""
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

    # Token planning is derived from this task plus governance's own context
    # estimate.  The governance floor influences reserve sizes but is not used
    # as a candidate eligibility threshold.
    dynamic_prompt_floor = max(64, math.ceil(governance_context_floor / 32))
    dynamic_completion_floor = max(
        128, math.ceil(governance_context_floor / 16)
    )
    protocol_reserve_tokens = max(
        256,
        math.ceil(governance_context_floor / 8),
        64 * (1 + requirement_count + acceptance_count + delivery_item_count),
    )
    expected_prompt_tokens = max(
        dynamic_prompt_floor,
        math.ceil((task_characters + evidence_characters) / 2),
    )
    expected_completion_tokens = max(
        dynamic_completion_floor,
        math.ceil(task_characters / 10)
        + 96 * requirement_count
        + 72 * acceptance_count
        + 96 * delivery_item_count
        + 32 * max(1, evidence_count),
    )

    structural_units = (
        1
        + requirement_count
        + acceptance_count
        + delivery_item_count
        + evidence_count
        + math.ceil((task_characters + evidence_characters) / 4000)
        + math.ceil(overall_pressure / 15)
    )
    dependency_fan_in_estimate = max(
        1,
        math.ceil(math.sqrt(max(1, structural_units))),
    )
    native_stats = _candidate_native_statistics(candidates)

    profile: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "principles": list(PRINCIPLES),
        "source": "current-ticket-structural-signals-only",
        "semantic_keyword_routing_used": False,
        "domain_hardcoding_used": False,
        "cross_task_history_used": False,
        "provider_metric_used": False,
        "all_calculable_planning_parameters_dynamic": True,
        "hard_model_eligibility_gates": [],
        "only_hard_model_boundary": "no-tools",
        "task_characters": task_characters,
        "evidence_characters": evidence_characters,
        "requirement_count": requirement_count,
        "evidence_count": evidence_count,
        "acceptance_count": acceptance_count,
        "delivery_item_count": delivery_item_count,
        "expected_prompt_tokens": expected_prompt_tokens,
        "expected_completion_tokens": expected_completion_tokens,
        "dynamic_prompt_floor": dynamic_prompt_floor,
        "dynamic_completion_floor": dynamic_completion_floor,
        "protocol_reserve_tokens": protocol_reserve_tokens,
        "governance_context_floor": governance_context_floor,
        "dependency_fan_in_estimate": dependency_fan_in_estimate,
        "candidate_native_statistics": native_stats,
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


def role_token_profile(
    profile: Mapping[str, Any], role_id: str
) -> dict[str, int]:
    prompt = _positive_int(profile.get("expected_prompt_tokens")) or 1
    completion = _positive_int(profile.get("expected_completion_tokens")) or 1
    governance_floor = _positive_int(profile.get("governance_context_floor"))
    reserve = _positive_int(profile.get("protocol_reserve_tokens"))
    fan_in = max(1, _positive_int(profile.get("dependency_fan_in_estimate")))
    pressure = _mapping(profile.get("pressure"))
    overall = _clamp(_positive_int(pressure.get("overall")), 0, 100)

    if role_id in {"evidence", "options"}:
        prompt_tokens = prompt
        completion_tokens = completion
    elif role_id == "review":
        review_inputs = max(1, fan_in - 1)
        prompt_tokens = prompt + review_inputs * completion
        completion_tokens = math.ceil(
            completion * (1.05 + overall / 1000.0)
        )
    elif role_id in {"synthesis", RECOVERY_ROLE_ID}:
        # Recovery reserves the heaviest dynamically estimated role so it can
        # replace any primary expert without a fixed role assumption.
        prompt_tokens = prompt + fan_in * completion
        completion_tokens = math.ceil(
            completion * (1.15 + overall / 500.0)
        )
    else:
        raise TaskAdaptiveScoringError(f"unknown role_id: {role_id}")

    # Governance's task estimate and the local protocol reserve are additive;
    # this is an objective capacity requirement, not candidate admission.
    required_context_tokens = (
        governance_floor + reserve + prompt_tokens + completion_tokens
    )
    return {
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "required_context_tokens": int(required_context_tokens),
        "dependency_fan_in": fan_in,
        "protocol_reserve_tokens": reserve,
    }


def dynamic_role_weights(
    profile: Mapping[str, Any], role_id: str
) -> dict[str, int]:
    pressure = _mapping(profile.get("pressure"))
    overall = _clamp(_positive_int(pressure.get("overall")), 0, 100)
    input_pressure = _clamp(_positive_int(pressure.get("input")), 0, 100)
    constraint_pressure = _clamp(
        _positive_int(pressure.get("constraints")), 0, 100
    )
    evidence_pressure = _clamp(
        _positive_int(pressure.get("evidence")), 0, 100
    )
    delivery_pressure = _clamp(
        _positive_int(pressure.get("delivery")), 0, 100
    )

    downstream = 0
    if role_id == "review":
        downstream = 15
    elif role_id == "synthesis":
        downstream = 28
    elif role_id == RECOVERY_ROLE_ID:
        downstream = 10
    elif role_id not in {"evidence", "options"}:
        raise TaskAdaptiveScoringError(f"unknown role_id: {role_id}")

    # Every coefficient below is recomputed from current pressure dimensions.
    # Cost dominates simple tasks; intelligence/capacity rise with structural
    # pressure; popularity remains a modest real-use signal; marginal return
    # stays important unless complexity justifies higher spend.
    task_cost = max(5, 70 - round(0.45 * overall) - downstream // 3)
    intelligence = max(
        5,
        12
        + round(0.35 * overall)
        + round(0.12 * constraint_pressure)
        + downstream,
    )
    popularity = max(
        3,
        8 + round(0.08 * evidence_pressure) - downstream // 6,
    )
    capacity = max(
        5,
        8
        + round(0.18 * input_pressure)
        + round(0.10 * delivery_pressure)
        + downstream // 2,
    )
    marginal = max(
        5,
        38 - round(0.20 * overall) + (8 if role_id == RECOVERY_ROLE_ID else 0),
    )
    return {
        "task_cost": int(task_cost),
        "intelligence": int(intelligence),
        "weekly_popularity": int(popularity),
        "capacity_headroom": int(capacity),
        "marginal_return": int(marginal),
    }


def estimate_task_cost_usd(
    candidate: Mapping[str, Any], role_tokens: Mapping[str, Any]
) -> float:
    prompt_rate = _finite_nonnegative(
        candidate.get("prompt_usd_per_million")
    )
    completion_rate = _finite_nonnegative(
        candidate.get("completion_usd_per_million")
    )
    request_fee = _finite_nonnegative(candidate.get("request_usd"))
    prompt_tokens = _positive_int(role_tokens.get("prompt_tokens"))
    completion_tokens = _positive_int(role_tokens.get("completion_tokens"))
    return (
        prompt_rate * prompt_tokens / 1_000_000
        + completion_rate * completion_tokens / 1_000_000
        + request_fee
    )


def _capacity(
    candidate: Mapping[str, Any], role_tokens: Mapping[str, Any]
) -> tuple[bool, float, float]:
    required_context = _positive_int(role_tokens.get("required_context_tokens"))
    required_completion = _positive_int(role_tokens.get("completion_tokens"))
    context_length = _positive_int(candidate.get("context_length"))
    maximum_completion = _positive_int(candidate.get("max_completion_tokens"))

    context_ratio = (
        required_context / context_length if context_length else 1.25
    )
    completion_ratio = (
        required_completion / maximum_completion
        if maximum_completion
        else 1.00
    )
    compatible = not (
        (context_length and required_context > context_length)
        or (
            maximum_completion
            and required_completion > maximum_completion
        )
    )
    shortfall = max(0.0, context_ratio - 1.0) + max(
        0.0, completion_ratio - 1.0
    )
    return compatible, float(context_ratio + completion_ratio), float(shortfall)


def _rank_map(rows: Sequence[tuple[str, Any]]) -> dict[str, int]:
    ordered = sorted(rows, key=lambda item: (item[1], item[0]))
    return {
        model: rank for rank, (model, _) in enumerate(ordered, 1)
    }


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
            raise TaskAdaptiveScoringError(
                "candidate model identities must be unique"
            )
        compatible, capacity_risk, capacity_shortfall = _capacity(
            row, tokens
        )
        popularity = (
            _positive_int(row.get("popularity_rank")) or 1_000_000
        )
        raw[model] = {
            "compatible": compatible,
            "estimated_task_cost_usd": estimate_task_cost_usd(row, tokens),
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
        [
            (model, values["estimated_task_cost_usd"])
            for model, values in raw.items()
        ]
    )
    intelligence_rank = _rank_map(
        [
            (model, values["official_intelligence_rank"])
            for model, values in raw.items()
        ]
    )
    popularity_rank = _rank_map(
        [
            (model, values["weekly_popularity_rank"])
            for model, values in raw.items()
        ]
    )
    capacity_rank = _rank_map(
        [(model, values["capacity_risk"]) for model, values in raw.items()]
    )

    candidate_count = len(raw)
    marginal_rows: list[tuple[str, float]] = []
    for model, values in raw.items():
        quality_utility = max(
            1, candidate_count + 1 - intelligence_rank[model]
        )
        marginal_cost_per_quality = (
            values["estimated_task_cost_usd"] / quality_utility
        )
        values["quality_utility"] = quality_utility
        values["marginal_cost_per_quality"] = marginal_cost_per_quality
        marginal_rows.append((model, marginal_cost_per_quality))
    marginal_rank = _rank_map(marginal_rows)

    pressure = _positive_int(
        _mapping(profile.get("pressure")).get("overall")
    )
    result: dict[str, dict[str, Any]] = {}
    for model, values in raw.items():
        ranks = {
            "task_cost": cost_rank[model],
            "intelligence": intelligence_rank[model],
            "weekly_popularity": popularity_rank[model],
            "capacity_headroom": capacity_rank[model],
            "marginal_return": marginal_rank[model],
        }
        base_objective = sum(
            weights[key] * ranks[key] for key in weights
        )
        # Capacity shortfall is a dynamic risk cost, not an admission gate.
        shortfall_scale = max(
            1,
            sum(weights.values())
            * max(1, candidate_count)
            * (1 + pressure) // 50,
        )
        shortfall_penalty = int(
            round(values["capacity_shortfall"] * shortfall_scale)
        )
        result[model] = {
            **values,
            "role_id": role_id,
            "role_tokens": dict(tokens),
            "weights": dict(weights),
            "ranks": ranks,
            "base_objective_score": int(base_objective),
            "capacity_shortfall_penalty": shortfall_penalty,
            "objective_score": int(base_objective + shortfall_penalty),
            "provider_metric_used": False,
            "capacity_is_hard_gate": False,
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
