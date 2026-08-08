"""Bind current-task planning signals to concrete OpenRouter request knobs.

This module is intentionally small and dependency-free. It does not introduce
business admission gates: token/cost/call budgets remain advisory. The output
allowance is a per-request transport reservation derived from the current
request shape and the planned reasoning effort; it never invalidates an
otherwise valid task result.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode

SCHEMA_VERSION = "current-request-runtime-knob-binding-2"
# OpenRouter's normalized gateway effort levels. The ratios mirror the gateway
# documentation and are used only to reserve enough *visible* output after
# reasoning consumes part of max_tokens.
_REASONING_RATIO = {
    "max": 0.95,
    "xhigh": 0.95,
    "high": 0.80,
    "medium": 0.50,
    "low": 0.20,
    "minimal": 0.10,
    "none": 0.0,
}
_EFFORT_ORDER = {
    "none": 0,
    "minimal": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "xhigh": 5,
    "max": 6,
}


def _mapping_attr(value: Any, name: str) -> Mapping[str, Any]:
    raw = getattr(value, name, {})
    return raw if isinstance(raw, Mapping) else {}


def _sequence_attr(value: Any, name: str) -> list[Any]:
    raw = getattr(value, name, ())
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return list(raw)
    return []


def _text_length(value: Any) -> int:
    if value in (None, ""):
        return 0
    return len(str(value))


def _effort(node: SelectedNode) -> str:
    profile = _mapping_attr(node, "reasoning_profile")
    raw = str(profile.get("effort") or "medium").casefold()
    if raw in _EFFORT_ORDER:
        return raw
    # The legacy saturation recovery asks for visible-output-only execution by
    # setting reasoning_enabled=False. Some reasoning models declare reasoning
    # mandatory and reject `none`, so `minimal` is the safe gateway-level
    # adaptation when the profile has no recognized effort value.
    if profile.get("reasoning_enabled") is False:
        return "minimal"
    return "medium"


def _visible_output_requirement(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> int:
    task_chars = max(1, _text_length(original_task))
    upstream_chars = sum(_text_length(row.get("answer")) for row in upstream)
    estimated_prompt_tokens = max(1, math.ceil((task_chars + upstream_chars) / 4))
    required_fields = len(_mapping_attr(node, "output_contract").get("required_fields", []))
    work_units = max(1, len(_sequence_attr(node, "assigned_work")))
    fan_in = len(upstream)
    effort_rank = _EFFORT_ORDER[_effort(node)]
    structural_units = max(1, required_fields + work_units + fan_in + effort_rank)
    contract_floor = max(256, 192 * max(1, required_fields))
    pressure_multiplier = 1.0 + math.log2(structural_units + 1) / 4.0
    protocol_reserve = math.ceil(math.sqrt(estimated_prompt_tokens * structural_units))
    return int(
        max(
            contract_floor,
            math.ceil(estimated_prompt_tokens * pressure_multiplier) + protocol_reserve,
        )
    )


def dynamic_output_allowance(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> int:
    """Reserve enough total output for reasoning plus current-task visible text.

    OpenRouter counts reasoning tokens inside output tokens. Therefore the
    transport allowance is reverse-computed from the visible-output requirement
    and the current role's effort ratio instead of using the provider/model's
    native maximum. This remains request shaping, not a task budget or gate.
    """
    visible_required = _visible_output_requirement(node, original_task, upstream)
    effort = _effort(node)
    reasoning_ratio = _REASONING_RATIO[effort]
    visible_share = max(0.05, 1.0 - reasoning_ratio)
    allowance = math.ceil(visible_required / visible_share)

    parameter_profile = _mapping_attr(node, "parameter_profile")
    try:
        recovery_multiplier = float(
            parameter_profile.get("dynamic_output_allowance_multiplier", 1.0)
        )
    except (TypeError, ValueError):
        recovery_multiplier = 1.0
    if not math.isfinite(recovery_multiplier) or recovery_multiplier < 1.0:
        recovery_multiplier = 1.0
    return int(math.ceil(allowance * recovery_multiplier))


def bind_request_knobs(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return sendable request fields plus an audit record."""
    effort = _effort(node)
    visible_required = _visible_output_requirement(node, original_task, upstream)
    allowance = dynamic_output_allowance(node, original_task, upstream)
    config = {
        "reasoning": {"effort": effort, "exclude": True},
        "max_tokens": allowance,
    }
    audit = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "node_id": str(getattr(node, "node_id", "")),
        "reasoning_effort_planned": effort,
        "reasoning_effort_bound": effort,
        "reasoning_output_ratio_assumption": _REASONING_RATIO[effort],
        "visible_output_requirement_tokens": visible_required,
        "dynamic_output_allowance_tokens": allowance,
        "output_allowance_is_task_admission_gate": False,
        "output_allowance_is_result_validity_gate": False,
        "recompute_trigger": "current-request-shape-or-current-run-truncation-feedback",
        "current_task_only": True,
        "cross_task_history_used": False,
    }
    return config, audit


def audit_bound_request(node: SelectedNode, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed when a computed execution knob was not consumed."""
    planned_effort = _effort(node)
    reasoning = payload.get("reasoning")
    bound_effort = (
        str(reasoning.get("effort") or "").casefold()
        if isinstance(reasoning, Mapping)
        else ""
    )
    allowance = payload.get("max_tokens")
    try:
        allowance_value = int(allowance)
    except (TypeError, ValueError):
        allowance_value = 0
    unused: list[str] = []
    if bound_effort != planned_effort:
        unused.append("role-reasoning-effort")
    if allowance_value <= 0:
        unused.append("dynamic-output-allowance")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if not unused else "FAIL",
        "node_id": str(getattr(node, "node_id", "")),
        "computed_but_unused": unused,
        "reasoning_effort_planned": planned_effort,
        "reasoning_effort_effective": bound_effort or None,
        "dynamic_output_allowance_tokens": allowance_value,
    }


__all__ = [
    "SCHEMA_VERSION",
    "audit_bound_request",
    "bind_request_knobs",
    "dynamic_output_allowance",
]
