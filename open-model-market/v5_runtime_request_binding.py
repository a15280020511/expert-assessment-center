"""Bind current-task planning signals to concrete OpenRouter request knobs.

Request shaping is cost-effectiveness-first but never a business admission gate.
The planner designs prompt/output/timeout/resource parameters before execution; this
module resolves the effective request value from the *fully assembled* current
payload immediately before send and from same-node current-run feedback.

Token estimation remains an estimator because OpenRouter models do not share one
public tokenizer.  The estimator is therefore explicit, language-aware and audited
as an infrastructure prior rather than disguised as a known future token count.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode

SCHEMA_VERSION = "current-request-runtime-knob-binding-5-no-hidden-reasoning-default"
_EFFORT_ORDER = {
    "none": 0,
    "minimal": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "xhigh": 5,
    "max": 6,
}
_NUMBERED_DELIVERY_RE = re.compile(
    r"(?:^|[。；;！？!?\n])\s*(?:第?[一二三四五六七八九十百]+|\d+)\s*[）)\.、]",
    re.MULTILINE,
)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:%|％)?")
_TABLE_RE = re.compile(r"(?:Markdown\s*表|表格|决策表|对比表|比较表|逐项.*表)", re.I)
_OPTION_SEQUENCE_RE = re.compile(
    r"(?<![A-Za-z])([A-H])(?:\s*[/、，,]\s*([A-H])){1,7}(?![A-Za-z])",
    re.I,
)
_SCENARIO_RE = re.compile(
    r"(?:L|情景|场景)[^。；;\n]{0,160}?(?:=|为)[^。；;\n]+",
    re.I,
)


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


def estimate_text_tokens(text: Any) -> int:
    """Conservative tokenizer-agnostic estimate, especially for CJK prompts."""
    rendered = str(text or "")
    if not rendered:
        return 0
    cjk = len(_CJK_RE.findall(rendered))
    remaining = max(0, len(rendered) - cjk)
    # CJK characters are not divided by four.  Non-CJK text keeps a conservative
    # character proxy.  This is an audited infrastructure prior, not a hard gate.
    return max(1, cjk + math.ceil(remaining / 4))


def estimate_payload_tokens(payload: Mapping[str, Any] | None) -> int:
    if not isinstance(payload, Mapping):
        return 0
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return estimate_text_tokens(payload)
    total = 0
    for row in messages:
        if not isinstance(row, Mapping):
            continue
        total += estimate_text_tokens(row.get("content"))
        total += estimate_text_tokens(row.get("role"))
    return max(0, total)


def _effort(node: SelectedNode) -> str:
    profile = _mapping_attr(node, "reasoning_profile")
    if profile.get("reasoning_enabled") is False:
        return "minimal"
    raw = str(profile.get("effort") or "").strip().casefold()
    if raw in _EFFORT_ORDER:
        return raw
    raise RuntimeError(
        f"node {getattr(node, 'node_id', '')} has no valid task-derived reasoning effort; "
        "request-time medium fallback is forbidden"
    )


def _reasoning_reserve_multiplier(effort: str) -> float:
    """Use effort as an ordinal pressure prior, not a claimed reasoning-token ratio."""
    if effort not in _EFFORT_ORDER:
        raise ValueError(f"unsupported reasoning effort: {effort}")
    rank = _EFFORT_ORDER[effort]
    maximum = max(_EFFORT_ORDER.values()) + 1
    return 1.0 + rank / maximum


def _explicit_delivery_units(node: SelectedNode, original_task: str) -> int:
    if _mapping_attr(node, "output_contract").get("final_delivery_node") is not True:
        return 1
    numbered = len(_NUMBERED_DELIVERY_RE.findall(str(original_task or "")))
    contract_fields = len(
        _mapping_attr(node, "output_contract").get("required_fields", [])
    )
    return max(1, numbered, contract_fields)


def _option_count(task: str) -> int:
    rendered = str(task or "")
    labels = set(
        match.group(1).upper()
        for match in re.finditer(
            r"(?<![A-Za-z])([A-H])(?![A-Za-z])",
            rendered,
            re.I,
        )
    )
    if _OPTION_SEQUENCE_RE.search(rendered):
        return max(1, len(labels))
    return 0


def _scenario_value_count(task: str) -> int:
    values: list[str] = []
    for match in _SCENARIO_RE.finditer(str(task or "")):
        values.extend(_NUMBER_RE.findall(match.group(0)))
    return len(values)


def _structural_output_units(node: SelectedNode, original_task: str) -> dict[str, Any]:
    rendered = str(original_task or "")
    required_fields = len(
        _mapping_attr(node, "output_contract").get("required_fields", [])
    )
    work_units = max(1, len(_sequence_attr(node, "assigned_work")))
    delivery_units = _explicit_delivery_units(node, rendered)
    numeric_literals = len(_NUMBER_RE.findall(rendered))
    option_count = _option_count(rendered)
    scenario_count = _scenario_value_count(rendered)
    table_requested = bool(_TABLE_RE.search(rendered))
    matrix_cells = option_count * scenario_count if table_requested else 0
    pairwise_cells = (
        option_count * (option_count - 1) // 2
        if option_count > 1 and re.search(r"(?:全部|所有).{0,10}(?:两两|交点|临界)", rendered)
        else 0
    )
    units = (
        required_fields
        + work_units
        + delivery_units
        + math.sqrt(numeric_literals + 1)
        + math.sqrt(matrix_cells + 1)
        + math.sqrt(pairwise_cells + 1)
    )
    return {
        "required_fields": required_fields,
        "work_units": work_units,
        "delivery_units": delivery_units,
        "numeric_literal_count": numeric_literals,
        "option_count": option_count,
        "scenario_value_count": scenario_count,
        "table_requested": table_requested,
        "estimated_table_cells": matrix_cells,
        "estimated_pairwise_cells": pairwise_cells,
        "structural_output_units": round(float(units), 8),
    }


def _visible_output_requirement(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
    current_payload: Mapping[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    task_tokens = estimate_text_tokens(original_task)
    upstream_tokens = sum(
        estimate_text_tokens(row.get("answer")) for row in upstream
    )
    final_payload_tokens = estimate_payload_tokens(current_payload)
    prompt_tokens = max(1, final_payload_tokens or task_tokens + upstream_tokens)
    shape = _structural_output_units(node, original_task)
    fan_in = len(upstream)
    structural_units = max(
        1.0,
        float(shape["structural_output_units"]) + fan_in,
    )

    # Output need grows from the *actual assembled prompt* and explicit delivery
    # structure.  This replaces the old hidden chars/4 + fixed field floor rule.
    expansion = 1.0 + math.log1p(structural_units)
    visible_required = math.ceil(prompt_tokens * expansion)

    # A current-task planning value is a lower signal, not a ceiling.  It comes
    # from ParameterDesign and prevents the request layer from ignoring planning.
    profile = _mapping_attr(node, "parameter_profile")
    resource_values = profile.get("runtime_resource_parameter_values")
    resource_values = resource_values if isinstance(resource_values, Mapping) else {}
    planned = resource_values.get("output-transport-allowance")
    planned = planned if isinstance(planned, Mapping) else {}
    try:
        planned_target = max(
            0,
            int(planned.get("pre_request_visible_target_tokens") or 0),
        )
    except (TypeError, ValueError):
        planned_target = 0
    visible_required = max(visible_required, planned_target)
    audit = {
        **shape,
        "task_token_estimate": task_tokens,
        "upstream_token_estimate": upstream_tokens,
        "final_payload_token_estimate": final_payload_tokens or None,
        "final_payload_measured": final_payload_tokens > 0,
        "effective_prompt_token_estimate": prompt_tokens,
        "planned_visible_target_tokens": planned_target or None,
        "structural_expansion_multiplier": round(expansion, 8),
        "token_estimator": "cjk-aware-tokenizer-agnostic-current-payload-estimator",
        "token_estimate_is_exact": False,
    }
    return int(visible_required), audit


def dynamic_output_allowance(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
    current_payload: Mapping[str, Any] | None = None,
) -> int:
    visible_required, _ = _visible_output_requirement(
        node,
        original_task,
        upstream,
        current_payload,
    )
    effort = _effort(node)
    allowance = math.ceil(
        visible_required * _reasoning_reserve_multiplier(effort)
    )
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


def _parameter_ids(node: SelectedNode) -> Mapping[str, Any]:
    profile = _mapping_attr(node, "parameter_profile")
    value = profile.get("runtime_resource_parameter_ids")
    return value if isinstance(value, Mapping) else {}


def bind_request_knobs(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
    current_payload: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    effort = _effort(node)
    visible_required, structural_audit = _visible_output_requirement(
        node,
        original_task,
        upstream,
        current_payload,
    )
    allowance = dynamic_output_allowance(
        node,
        original_task,
        upstream,
        current_payload,
    )
    ids = _parameter_ids(node)
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
        "hidden_reasoning_effort_default_used": False,
        "reasoning_reserve_multiplier": round(
            _reasoning_reserve_multiplier(effort),
            8,
        ),
        "reasoning_reserve_is_exact_future_usage_prediction": False,
        "visible_output_requirement_tokens": visible_required,
        "dynamic_output_allowance_tokens": allowance,
        "output_allowance_is_task_admission_gate": False,
        "output_allowance_is_result_validity_gate": False,
        "soft_token_cost_efficiency": True,
        "cost_effectiveness_priority": True,
        "parameter_runtime_binding": {
            "prompt_shape_parameter_id": ids.get("prompt-shape-budgeting"),
            "output_allowance_parameter_id": ids.get("output-transport-allowance"),
            "resource_efficiency_parameter_id": ids.get("resource-efficiency-balance"),
            "output_allowance_consumer": "openrouter-request.max_tokens",
            "planned_value_to_effective_value": True,
        },
        "recompute_trigger": (
            "final-current-payload-before-send-or-current-run-truncation-feedback"
        ),
        "current_task_only": True,
        "cross_task_history_used": False,
        **structural_audit,
    }
    return config, audit


def audit_bound_request(
    node: SelectedNode,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
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
    ids = _parameter_ids(node)
    unused: list[str] = []
    if bound_effort != planned_effort:
        unused.append("role-reasoning-effort")
    if allowance_value <= 0:
        unused.append("dynamic-output-allowance")
    if ids and not ids.get("output-transport-allowance"):
        unused.append("output-transport-allowance-parameter-spec")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if not unused else "FAIL",
        "node_id": str(getattr(node, "node_id", "")),
        "computed_but_unused": unused,
        "reasoning_effort_planned": planned_effort,
        "reasoning_effort_effective": bound_effort or None,
        "hidden_reasoning_effort_default_used": False,
        "dynamic_output_allowance_tokens": allowance_value,
        "final_payload_token_estimate": estimate_payload_tokens(payload),
        "parameter_runtime_binding": {
            "output_allowance_parameter_id": ids.get("output-transport-allowance"),
            "effective_request_field": "max_tokens",
        },
    }


__all__ = [
    "SCHEMA_VERSION",
    "audit_bound_request",
    "bind_request_knobs",
    "dynamic_output_allowance",
    "estimate_payload_tokens",
    "estimate_text_tokens",
]
