"""Unified runtime policy, cost evidence, and policy-consistent recovery."""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Dict, Mapping, Sequence

import direct_calls
import model_market
import seat_scoring
from hardened_runtime import (
    apply_judge_output_contract as _apply_judge_output_contract,
    enforce_post_judge_actual_budget as _enforce_post_judge_actual_budget,
)
from response_audit import diagnostics

_ORIGINAL_BUILD_RUN_CONFIG = model_market.build_run_config
_ACTIVE_QUALITY_TIER = "value"


def _no_limit_build_run_config(args: Any) -> Any:
    """Accept compatibility fields, remove hard cost limits, and retain active tier."""
    global _ACTIVE_QUALITY_TIER
    if hasattr(args, "max_estimated_cost_usd"):
        args.max_estimated_cost_usd = None
    previous = os.environ.pop("MAX_ESTIMATED_COST_USD", None)
    try:
        configured = _ORIGINAL_BUILD_RUN_CONFIG(args)
    finally:
        if previous is not None:
            os.environ["MAX_ESTIMATED_COST_USD"] = previous
    configured = replace(configured, max_estimated_cost_usd=None)
    _ACTIVE_QUALITY_TIER = configured.quality_tier
    return configured


if not getattr(model_market.build_run_config, "_no_hard_cost_limit", False):
    _no_limit_build_run_config._no_hard_cost_limit = True
    model_market.build_run_config = _no_limit_build_run_config


def _policy_aware_replacement_candidates(
    ranked,
    profile,
    expert,
    used_ids,
    used_authors,
    *,
    run=None,
    tier=None,
):
    """Reuse initial stability/capability gates and the active quality policy."""
    effective_tier = str(tier or getattr(run, "quality_tier", "") or _ACTIVE_QUALITY_TIER)
    if effective_tier not in seat_scoring.RULE_ORDER:
        effective_tier = _ACTIVE_QUALITY_TIER
    pool = [
        model
        for model in ranked
        if model.id not in used_ids
        and not seat_scoring._is_unstable(model)
        and seat_scoring._history_bucket(model) > 0
        and seat_scoring._within_capability_floor(model)
    ]
    pool = seat_scoring._seat_pool(pool, expert.seat_key, expert.domain_focus)
    distinct = [model for model in pool if model.author not in used_authors]
    return seat_scoring._ordered(
        distinct or pool,
        expert.seat_key,
        expert.domain_focus,
        effective_tier,
    )[:3]


# direct_calls imported the function by name, so patch both bindings.
seat_scoring.replacement_candidates = _policy_aware_replacement_candidates
direct_calls.replacement_candidates = _policy_aware_replacement_candidates


def apply_judge_output_contract(
    payload: Dict[str, Any],
    max_chinese_chars: int | None = None,
) -> Dict[str, Any]:
    """Apply the compact-complete contract; legacy length arguments are ignored."""
    return _apply_judge_output_contract(payload, max_chinese_chars)


def actual_team_cost(results: Sequence[Any], judge_response: Mapping[str, Any]) -> float:
    """Return provider-reported cost visible on final result objects."""
    experts = 0.0
    for result in results:
        usage = getattr(result, "usage", {}) or {}
        for key in ("cost", "total_cost"):
            try:
                if usage.get(key) is not None:
                    experts += max(0.0, float(usage[key]))
                    break
            except (TypeError, ValueError):
                continue
    info = diagnostics(judge_response)
    try:
        judge = max(0.0, float(info.get("cost") or 0.0))
    except (TypeError, ValueError):
        judge = 0.0
    return experts + judge


def enforce_post_judge_actual_budget(
    run: Any,
    results: Sequence[Any],
    judge_response: Mapping[str, Any],
) -> float:
    """Record all call costs without enforcing a monetary ceiling."""
    return _enforce_post_judge_actual_budget(run, results, judge_response)
