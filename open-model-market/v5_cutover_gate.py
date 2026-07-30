"""Evidence-only production cutover and V3-retirement gates for V5."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def _mean(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    values = [float(row.get(key, 0.0) or 0.0) for row in rows]
    return sum(values) / max(1, len(values))


def evaluate_cutover(
    records: Sequence[Mapping[str, Any]],
    *,
    phase: str = "canary",
) -> dict[str, Any]:
    """Return deterministic blockers; this function never changes production."""
    normalized = [dict(row) for row in records]
    v5 = [row for row in normalized if str(row.get("version") or row.get("strategy")) in {"5", "v5", "v5_joint_graph"}]
    v3 = [row for row in normalized if str(row.get("version") or row.get("strategy")) in {"3", "v3"}]
    required = 20 if phase == "canary" else 30 if phase == "post_default" else 3
    blockers: list[str] = []

    if len(v5) < required:
        blockers.append(f"insufficient-v5-observations:{len(v5)}/{required}")
    fatal = [
        row for row in v5
        if row.get("safety_failure")
        or row.get("budget_exceeded")
        or row.get("invalid_final_json")
        or row.get("fatal_error")
        or row.get("status") != "success"
    ]
    if fatal:
        blockers.append("v5-fatal-or-undeliverable-results-present")

    degraded_rate = sum(
        str(row.get("completion_mode") or row.get("completion_class")) in {"degraded", "degraded_success"}
        for row in v5
    ) / max(1, len(v5))
    if degraded_rate > 1.0 / 3.0 + 1e-12:
        blockers.append("v5-degraded-rate-above-one-third")

    v5_quality = _mean(v5, "blind_quality_score")
    v3_quality = _mean(v3, "blind_quality_score") if v3 else 0.0
    if v3 and v5_quality + 1e-12 < v3_quality:
        blockers.append("v5-quality-below-v3")

    v5_cost = _mean(v5, "actual_cost_usd")
    v3_cost = _mean(v3, "actual_cost_usd") if v3 else 0.0
    value_improvement = (
        (v5_quality / max(v5_cost, 1e-9)) / max(v3_quality / max(v3_cost, 1e-9), 1e-9) - 1.0
        if v3 and v3_quality > 0 and v3_cost > 0
        else 0.0
    )
    if v3 and v5_cost > v3_cost + 1e-12 and value_improvement < 0.10 - 1e-12:
        blockers.append("v5-cost-higher-without-10-percent-value-gain")

    blockers = sorted(set(blockers))
    cutover = not blockers and phase in {"benchmark", "canary"}
    retirement = not blockers and phase == "post_default"
    return {
        "version": 1,
        "phase": phase,
        "observations": {"v5": len(v5), "v3": len(v3), "required_v5": required},
        "metrics": {
            "v5_quality": round(v5_quality, 6),
            "v3_quality": round(v3_quality, 6),
            "v5_mean_cost_usd": round(v5_cost, 8),
            "v3_mean_cost_usd": round(v3_cost, 8),
            "v5_degraded_rate": round(degraded_rate, 6),
            "value_improvement_over_v3": round(value_improvement, 6),
        },
        "production_cutover_allowed": cutover,
        "v3_deletion_allowed": retirement,
        "blockers": blockers,
        "policy": {
            "benchmark_tasks": 3,
            "canary_tasks_before_default": 20,
            "post_default_tasks_before_v3_deletion": 30,
            "fatal_failures_allowed": 0,
            "maximum_degraded_rate": 1.0 / 3.0,
            "minimum_quality_ratio_to_v3": 1.0,
            "minimum_value_gain_when_cost_is_higher": 0.10,
            "automatic_fallback_to_v3": False,
        },
    }
