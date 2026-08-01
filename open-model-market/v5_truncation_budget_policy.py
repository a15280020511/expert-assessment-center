"""Reasoning-aware output allowance and P95 billed-token calibration.

Request allowance is derived from visible output demand, hidden reasoning,
verification pressure, node contract breadth and the task's explicit final
delivery breadth. Allowance remains separate from expected billed usage and is
bounded by both the endpoint limit and the global 32,768-token permission cap.
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


def _contract(work: Mapping[str, Any]) -> Mapping[str, Any]:
    value = work.get("output_contract", {})
    return value if isinstance(value, Mapping) else {}


def contract_field_count(work: Mapping[str, Any]) -> int:
    fields = _contract(work).get("required_fields", [])
    return len(fields) if isinstance(fields, list) else 0


def explicit_delivery_section_count(work: Mapping[str, Any]) -> int:
    return _int(_contract(work).get("task_explicit_delivery_section_count"), 0)


def explicit_long_form_required(work: Mapping[str, Any]) -> bool:
    return bool(_contract(work).get("task_explicit_long_form_required"))


def _allowance_field_reserve(work: Mapping[str, Any]) -> int:
    base = min(1_200, contract_field_count(work) * 64)
    explicit = explicit_delivery_section_count(work)
    if not explicit:
        return base
    return max(base, min(4_800, explicit * 320))


def _usage_field_reserve(work: Mapping[str, Any]) -> int:
    base = min(600, contract_field_count(work) * 32)
    explicit = explicit_delivery_section_count(work)
    if not explicit:
        return base
    return max(base, min(2_400, explicit * 160))


def completion_envelope(work: Mapping[str, Any], endpoint_max: int) -> int:
    """Return endpoint-bounded permission with long-form completion headroom."""
    context = work.get("context_requirements", {})
    context = context if isinstance(context, Mapping) else {}
    output = _int(context.get("expected_output_tokens"), 1_024)
    reasoning = _int(context.get("expected_reasoning_tokens"), 0)
    pressure = reasoning_pressure(work)
    field_reserve = _allowance_field_reserve(work)
    contract = _contract(work)
    machine = bool(contract.get("machine_readable_required"))
    long_form = explicit_long_form_required(work)

    reasoning_reserve = int(math.ceil(reasoning * (1.0 + 1.50 * pressure)))
    output_factor = 2.10 if long_form else 1.70
    reasoning_base = 1.35 if long_form else 1.25
    reasoning_pressure_factor = 1.35 if long_form else 1.25
    narrative_envelope = int(
        math.ceil(
            output * output_factor
            + reasoning * (reasoning_base + reasoning_pressure_factor * pressure)
            + field_reserve
        )
    )
    direct_envelope = output + reasoning_reserve + field_reserve
    if long_form and explicit_delivery_section_count(work) >= 8:
        minimum = 8_192
    else:
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
    """Estimate P95 billed usage without equating it to request permission."""
    context = work.get("context_requirements", {})
    context = context if isinstance(context, Mapping) else {}
    output = _int(context.get("expected_output_tokens"), 1_024)
    reasoning = _int(context.get("expected_reasoning_tokens"), 0)
    pressure = reasoning_pressure(work)
    field_reserve = _usage_field_reserve(work)
    contract = _contract(work)
    machine = bool(contract.get("machine_readable_required"))
    long_form = explicit_long_form_required(work)
    expected = (
        output * (1.10 if long_form else 1.0)
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
