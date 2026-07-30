"""Separate request token allowance from risk-adjusted billed-token estimation.

OpenRouter bills tokens actually used. A node's maximum completion allowance is a
safety ceiling, not an assumption that every request consumes the entire ceiling.
R8 keeps the generous bounded allowance for truncation prevention, while planning
uses a P95-style estimate derived from expected output plus expected reasoning.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Mapping, Sequence

import v5_cost_reliability_hardening as cost
import v5_planner as planner

P95_TOKEN_USAGE_MULTIPLIER = cost.COST_UNCERTAINTY_MULTIPLIER
STRUCTURED_P95_TOKEN_USAGE_MULTIPLIER = 1.22
MIN_ESTIMATED_COMPLETION_USAGE_TOKENS = 1_024
_INSTALLED = False
_ORIGINAL_HARDENED_CANDIDATE_FOR: Any = None


def _int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def estimated_completion_usage(work: Mapping[str, Any], endpoint_max: int) -> int:
    """Estimate P95 billed completion usage below the request allowance.

    Expected reasoning tokens are included explicitly. The request allowance
    remains the larger truncation-protection ceiling produced by
    ``completion_envelope``. Structured delivery receives a slightly larger
    usage reserve, but neither path assumes the entire maximum allowance is used.
    """
    context = work.get("context_requirements", {})
    context = context if isinstance(context, Mapping) else {}
    output = _int(context.get("expected_output_tokens"), 1_024)
    reasoning = _int(context.get("expected_reasoning_tokens"), 0)
    machine = bool(
        isinstance(work.get("output_contract"), Mapping)
        and work.get("output_contract", {}).get("machine_readable_required")
    )
    multiplier = (
        STRUCTURED_P95_TOKEN_USAGE_MULTIPLIER
        if machine
        else P95_TOKEN_USAGE_MULTIPLIER
    )
    expected = max(1, output + reasoning)
    usage = int(math.ceil(expected * multiplier))
    allowance = cost.completion_envelope(work, endpoint_max)
    return max(
        MIN_ESTIMATED_COMPLETION_USAGE_TOKENS,
        min(allowance, usage),
    )


def p95_usage_estimated_cost(
    endpoint: Mapping[str, Any],
    works: Sequence[Mapping[str, Any]],
    bundle_discount: float = 1.0,
) -> float:
    """Estimate billed cost from P95 usage, not maximum request allowance."""
    prompt_tokens = 0
    completion_tokens = 0
    endpoint_max = _int(endpoint.get("max_completion_tokens"), 0)
    for work in works:
        context = work.get("context_requirements", {})
        context = context if isinstance(context, Mapping) else {}
        prompt_tokens += (
            _int(context.get("system_prompt_tokens"))
            + _int(context.get("original_task_tokens"))
            + _int(context.get("visible_upstream_tokens"))
        )
        completion_tokens += estimated_completion_usage(work, endpoint_max)

    discount = max(0.1, float(bundle_discount))
    prompt_tokens = int(math.ceil(prompt_tokens * discount))
    completion_tokens = int(math.ceil(completion_tokens * discount))
    base = (
        prompt_tokens * _float(endpoint.get("prompt_price_per_million"))
        + completion_tokens * _float(endpoint.get("completion_price_per_million"))
    ) / 1_000_000
    reliability = max(0.0, min(1.0, _float(endpoint.get("reliability"), 0.95)))
    reliability_reserve = 1.0 + max(0.0, 0.98 - reliability) * 1.75
    return round(base * reliability_reserve, 8)


def usage_audited_candidate_for(*args: Any, **kwargs: Any) -> Any:
    """Preserve R8 allowance behavior and add separate usage evidence."""
    if _ORIGINAL_HARDENED_CANDIDATE_FOR is None:
        raise RuntimeError("v5_token_cost_policy.install() has not been called")
    candidate = _ORIGINAL_HARDENED_CANDIDATE_FOR(*args, **kwargs)
    if candidate is None:
        return None

    endpoint = args[4] if len(args) > 4 and isinstance(args[4], Mapping) else {}
    works = args[2] if len(args) > 2 and isinstance(args[2], Sequence) else ()
    endpoint_max = _int(endpoint.get("max_completion_tokens"), 0)
    usage = sum(
        estimated_completion_usage(work, endpoint_max)
        for work in works
        if isinstance(work, Mapping)
    )
    allowance = sum(
        cost.completion_envelope(work, endpoint_max)
        for work in works
        if isinstance(work, Mapping)
    )
    profile = dict(candidate.parameter_profile)
    profile.update(
        {
            "estimated_completion_usage_tokens": max(1, usage),
            "recommended_output_allowance_tokens": min(
                cost.MAX_OUTPUT_ALLOWANCE_TOKENS,
                max(1_024, allowance),
            ),
            "cost_estimation_policy": (
                "reasoning-inclusive-p95-usage-not-max-allowance-r8"
            ),
            "output_allowance_is_cost_assumption": False,
            "p95_token_usage_multiplier": P95_TOKEN_USAGE_MULTIPLIER,
            "structured_p95_token_usage_multiplier": (
                STRUCTURED_P95_TOKEN_USAGE_MULTIPLIER
            ),
        }
    )
    return replace(candidate, parameter_profile=profile)


def install() -> None:
    """Install after the original R8 cost and payload hardening."""
    global _INSTALLED
    global _ORIGINAL_HARDENED_CANDIDATE_FOR
    cost.install()
    if _INSTALLED:
        return
    _ORIGINAL_HARDENED_CANDIDATE_FOR = planner._candidate_for
    planner._estimated_cost = p95_usage_estimated_cost
    planner._candidate_for = usage_audited_candidate_for
    _INSTALLED = True
