"""Bind current-task planning signals to concrete OpenRouter request knobs.

This module is intentionally small and dependency-free. It does not introduce
business admission gates: token/cost/call budgets remain advisory. The output
allowance below is a per-request transport reservation derived from the current
request shape; it may be increased on truncation and never invalidates an
otherwise valid task result.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode

SCHEMA_VERSION = "current-request-runtime-knob-binding-1"
_EFFORT_ORDER = {"low": 1, "medium": 2, "high": 3}


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
    raw = str(_mapping_attr(node, "reasoning_profile").get("effort") or "medium").casefold()
    return raw if raw in _EFFORT_ORDER else "medium"


def dynamic_output_allowance(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> int:
    """Derive a finite request reservation from only current-run structure.

    This is not a task budget and is not an eligibility gate. Its purpose is to
    avoid provider-side worst-case reservation (for example a native 65k output
    default) while leaving enough room for the current contract. A truncation
    remains recoverable and may receive a larger allowance from recovery logic.
    """
    task_chars = max(1, _text_length(original_task))
    upstream_chars = sum(_text_length(row.get("answer")) for row in upstream)
    estimated_prompt_tokens = max(1, math.ceil((task_chars + upstream_chars) / 4))
    required_fields = len(_mapping_attr(node, "output_contract").get("required_fields", []))
    work_units = max(1, len(_sequence_attr(node, "assigned_work")))
    fan_in = len(upstream)
    effort_rank = _EFFORT_ORDER[_effort(node)]

    # Contract floor is protocol-shaped, not business-shaped. The variable
    # terms are entirely current-request derived.
    structural_units = max(1, required_fields + work_units + fan_in + effort_rank)
    contract_floor = max(256, 192 * max(1, required_fields))
    pressure_multiplier = 1.0 + math.log2(structural_units + 1) / 4.0
    protocol_reserve = math.ceil(math.sqrt(estimated_prompt_tokens * structural_units))
    allowance = math.ceil(estimated_prompt_tokens * pressure_multiplier) + protocol_reserve
    return int(max(contract_floor, allowance))


def bind_request_knobs(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return sendable request fields plus an audit record."""
    effort = _effort(node)
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
        "dynamic_output_allowance_tokens": allowance,
        "output_allowance_is_task_admission_gate": False,
        "output_allowance_is_result_validity_gate": False,
        "recompute_trigger": "current-request-shape-or-truncation-change",
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
