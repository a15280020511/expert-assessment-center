"""Reasoning-aware output allowance and P95 billed-token calibration.

The first post-planning production Canary showed that a 6,675-token allowance
was fully consumed by a high-reasoning node: 3,624 reasoning tokens plus the
visible answer. The old envelope treated the semantic compiler's reasoning
estimate as nearly exact and therefore under-reserved both truncation headroom
and P95 billed usage.

This policy keeps the absolute 10,000-token ceiling, but derives the allowance
from output demand, reasoning depth, verification pressure and output-contract
breadth. The request allowance remains separate from the billed-token estimate.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import v5_cost_reliability_hardening as cost
import v5_token_cost_policy as token_cost

_INSTALLED = False


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


def reasoning_pressure(work: Mapping[str, Any]) -> float:
    requirements = work.get("reasoning_requirements", {})
    requirements = requirements if isinstance(requirements, Mapping) else {}
    values = [
        _float(requirements.get(name), 0.0)
        for name in (
            "depth",
            "verification",
            "uncertainty_analysis",
            "counterfactual_depth",
            "adversarial_strength",
            "synthesis_depth",
        )
    ]
    return max(0.0, min(1.0, max(values, default=0.0)))


def contract_field_count(work: Mapping[str, Any]) -> int:
    contract = work.get("output_contract", {})
    contract = contract if isinstance(contract, Mapping) else {}
    fields = contract.get("required_fields", [])
    return len(fields) if isinstance(fields, list) else 0


def completion_envelope(work: Mapping[str, Any], endpoint_max: int) -> int:
    """Return a bounded allowance with explicit high-reasoning headroom."""
    context = work.get("context_requirements", {})
    context = context if isinstance(context, Mapping) else {}
    output = _int(context.get("expected_output_tokens"), 1_024)
    reasoning = _int(context.get("expected_reasoning_tokens"), 0)
    pressure = reasoning_pressure(work)
    field_reserve = min(1_200, contract_field_count(work) * 64)
    machine = bool(
        isinstance(work.get("output_contract"), Mapping)
        and work.get("output_contract", {}).get("machine_readable_required")
    )

    reasoning_reserve = int(math.ceil(reasoning * (1.0 + 1.50 * pressure)))
    narrative_envelope = int(
        math.ceil(
            output * 1.70
            + reasoning * (1.25 + 1.25 * pressure)
            + field_reserve
        )
    )
    direct_envelope = output + reasoning_reserve + field_reserve
    minimum = 4_096 if machine or pressure >= 0.72 else 2_048
    maximum = min(
        cost.MAX_OUTPUT_ALLOWANCE_TOKENS,
        endpoint_max or cost.MAX_OUTPUT_ALLOWANCE_TOKENS,
    )
    return max(
        1_024,
        min(maximum, max(minimum, direct_envelope, narrative_envelope)),
    )


def estimated_completion_usage(work: Mapping[str, Any], endpoint_max: int) -> int:
    """Estimate P95 billed completion usage under reasoning pressure."""
    context = work.get("context_requirements", {})
    context = context if isinstance(context, Mapping) else {}
    output = _int(context.get("expected_output_tokens"), 1_024)
    reasoning = _int(context.get("expected_reasoning_tokens"), 0)
    pressure = reasoning_pressure(work)
    field_reserve = min(600, contract_field_count(work) * 32)
    machine = bool(
        isinstance(work.get("output_contract"), Mapping)
        and work.get("output_contract", {}).get("machine_readable_required")
    )
    expected = (
        output
        + reasoning * (1.0 + 1.50 * pressure)
        + field_reserve
    )
    multiplier = (
        token_cost.STRUCTURED_P95_TOKEN_USAGE_MULTIPLIER
        if machine
        else token_cost.P95_TOKEN_USAGE_MULTIPLIER
    )
    usage = int(math.ceil(expected * multiplier))
    allowance = completion_envelope(work, endpoint_max)
    return max(
        token_cost.MIN_ESTIMATED_COMPLETION_USAGE_TOKENS,
        min(allowance, usage),
    )


def install() -> None:
    """Install after the base cost and token policies exactly once."""
    global _INSTALLED
    cost.install()
    token_cost.install()
    if _INSTALLED:
        return
    cost.completion_envelope = completion_envelope
    token_cost.estimated_completion_usage = estimated_completion_usage
    _INSTALLED = True
