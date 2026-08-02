"""Execution-only request, token and response hardening.

No candidate scoring, planning, optimizer, or recovery ranking exists here.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Mapping, Sequence

import v5_execution_primitives as primitives
from execution_graph import SelectedNode

COST_UNCERTAINTY_MULTIPLIER = 1.18
MAX_UPSTREAM_CHARS_PER_NODE = 6_000
MAX_UPSTREAM_CHARS_TOTAL = 24_000
MAX_OUTPUT_ALLOWANCE_TOKENS = 32_768
MIN_VISIBLE_OUTPUT_RESERVE_TOKENS = 1_024
MIN_REASONING_BUDGET_TOKENS = 1_024
_HIGH_REASONING_FUNCTIONS = {
    "synthesis",
    "quantitative_modeling",
    "implementation",
    "adversarial_reasoning",
    "counterfactual_analysis",
}
_REASONING_SHARE_BY_EFFORT = {
    "minimal": 0.10,
    "low": 0.20,
    "medium": 0.50,
    "high": 0.68,
}
_VISIBLE_FLOOR_BY_FUNCTION = {
    "synthesis": 3_072,
    "adversarial_reasoning": 2_048,
    "quantitative_modeling": 2_048,
    "implementation": 1_536,
    "counterfactual_analysis": 1_536,
}


def _integer(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def completion_envelope(
    work: Mapping[str, Any],
    endpoint_max: int,
) -> int:
    context = work.get("context_requirements", {})
    output = _integer(context.get("expected_output_tokens"), 1_024)
    reasoning = _integer(context.get("expected_reasoning_tokens"), 0)
    envelope = max(
        output + reasoning,
        int(output * 1.7 + reasoning * 1.2),
    )
    maximum = min(
        MAX_OUTPUT_ALLOWANCE_TOKENS,
        endpoint_max or MAX_OUTPUT_ALLOWANCE_TOKENS,
    )
    return max(1_024, min(envelope, maximum))


def conservative_estimated_cost(
    endpoint: Mapping[str, Any],
    works: Sequence[Mapping[str, Any]],
    bundle_discount: float = 1.0,
) -> float:
    prompt_tokens = 0
    completion_tokens = 0
    endpoint_max = _integer(endpoint.get("max_completion_tokens"))
    for work in works:
        context = work.get("context_requirements", {})
        prompt_tokens += sum(
            _integer(context.get(key))
            for key in (
                "system_prompt_tokens",
                "original_task_tokens",
                "visible_upstream_tokens",
            )
        )
        completion_tokens += completion_envelope(work, endpoint_max)
    discount = max(0.1, float(bundle_discount))
    base = (
        int(prompt_tokens * discount)
        * _finite(endpoint.get("prompt_price_per_million"))
        + int(completion_tokens * discount)
        * _finite(endpoint.get("completion_price_per_million"))
    ) / 1_000_000
    return round(base * COST_UNCERTAINTY_MULTIPLIER, 8)


def _strict_json_schema(
    node: SelectedNode,
) -> Mapping[str, Any] | None:
    if not node.output_contract.get("machine_readable_required"):
        return None
    supported = {
        str(value).casefold()
        for value in node.parameter_profile.get(
            "supported_parameters",
            [],
        )
    }
    if not supported.intersection(
        {"structured_outputs", "response_format", "json_schema"}
    ):
        return None
    fields = [
        str(value).strip()
        for value in node.output_contract.get("required_fields", [])
        if str(value).strip()
    ]
    if not fields:
        return None
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "v5_node_output",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    field: {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 8,
                    }
                    for field in fields
                },
                "required": fields,
                "additionalProperties": False,
            },
        },
    }


def _compact_json(value: Mapping[str, Any], limit: int) -> str:
    for maximum_items, maximum_chars in (
        (6, 500),
        (4, 260),
        (2, 160),
        (1, 96),
    ):
        compact: dict[str, Any] = {}
        for key, raw in value.items():
            if isinstance(raw, list):
                compact[str(key)] = [
                    str(item)[:maximum_chars]
                    for item in raw[:maximum_items]
                ]
            elif isinstance(raw, Mapping):
                compact[str(key)] = {
                    str(inner): str(item)[:maximum_chars]
                    for inner, item in list(raw.items())[:maximum_items]
                }
            else:
                compact[str(key)] = str(raw)[
                    : maximum_chars * maximum_items
                ]
        rendered = json.dumps(
            compact,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(rendered) <= limit:
            return rendered
    return json.dumps(
        {str(key): "" for key in value},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _compact_answer(answer: str, limit: int) -> str:
    if len(answer) <= limit:
        return answer
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, Mapping):
        return _compact_json(parsed, limit)
    marker = "\n\n[上游结果已确定性压缩]\n\n"
    available = max(0, limit - len(marker))
    head = available * 2 // 3
    tail = available - head
    return answer[:head] + marker + answer[-tail:]


def _output_allowance(node: SelectedNode) -> tuple[str | None, int]:
    supported = {
        str(value).casefold()
        for value in node.parameter_profile.get(
            "supported_parameters",
            [],
        )
    }
    maximum = min(
        MAX_OUTPUT_ALLOWANCE_TOKENS,
        max(
            1_024,
            _integer(
                os.getenv(
                    "V5_MAX_OUTPUT_ALLOWANCE_TOKENS",
                    str(MAX_OUTPUT_ALLOWANCE_TOKENS),
                ),
                MAX_OUTPUT_ALLOWANCE_TOKENS,
            ),
        ),
    )
    recommended = _integer(
        node.parameter_profile.get(
            "recommended_output_allowance_tokens"
        ),
        2_048,
    )
    allowance = min(maximum, max(1_024, recommended))
    if "max_completion_tokens" in supported:
        return "max_completion_tokens", allowance
    if "max_tokens" in supported:
        return "max_tokens", allowance
    return None, allowance


def _reasoning_effort(node: SelectedNode) -> str:
    request = node.request_config.get("reasoning")
    if isinstance(request, Mapping) and request.get("effort"):
        return str(request["effort"]).casefold()
    return str(
        node.reasoning_profile.get("effort") or "medium"
    ).casefold()


def completion_token_budget(
    node: SelectedNode,
    *,
    total_allowance: int | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    total = max(
        1,
        int(
            total_allowance
            if total_allowance is not None
            else _output_allowance(node)[1]
        ),
    )
    effort = str(
        reasoning_effort or _reasoning_effort(node) or "medium"
    ).casefold()
    supported = {
        str(value).casefold()
        for value in node.parameter_profile.get(
            "supported_parameters",
            [],
        )
    }
    reasoning_enabled = bool(
        node.reasoning_profile.get("reasoning_enabled", True)
    )
    reasoning_supported = (
        "reasoning" in supported
        or isinstance(node.request_config.get("reasoning"), Mapping)
    )
    fields = [
        str(value)
        for value in node.output_contract.get("required_fields", [])
    ]
    functions = {
        str(value).casefold() for value in node.functions
    }
    function_floor = max(
        (
            _VISIBLE_FLOOR_BY_FUNCTION.get(name, 0)
            for name in functions
        ),
        default=0,
    )
    contract_floor = max(len(fields) * 192, 1_024)
    share = _REASONING_SHARE_BY_EFFORT.get(effort, 0.50)
    visible = max(
        MIN_VISIBLE_OUTPUT_RESERVE_TOKENS,
        function_floor,
        contract_floor,
        int(math.ceil(total * (1.0 - share))),
    )
    if not reasoning_enabled or not reasoning_supported:
        reasoning_max = 0
        visible = total
    else:
        reasoning_max = min(
            max(0, total - visible),
            int(math.floor(total * share)),
        )
        if reasoning_max < MIN_REASONING_BUDGET_TOKENS:
            reasoning_max = 0
            visible = total
    return {
        "policy": "task-contract-visible-output-reserve",
        "total_completion_allowance_tokens": total,
        "reasoning_max_tokens": reasoning_max,
        "visible_output_reserve_tokens": max(
            1,
            min(total, visible),
        ),
        "reasoning_effort_source": effort,
        "reasoning_supported": reasoning_supported,
        "reasoning_enabled": reasoning_enabled,
    }


def hardened_build_node_payload(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    compacted: list[dict[str, Any]] = []
    remaining = MAX_UPSTREAM_CHARS_TOTAL
    for row in upstream:
        if remaining <= 0:
            break
        answer = str(row.get("answer") or "")
        if not answer:
            continue
        clipped = _compact_answer(
            answer,
            min(MAX_UPSTREAM_CHARS_PER_NODE, remaining),
        )
        compacted.append({**dict(row), "answer": clipped})
        remaining -= len(clipped)

    payload = primitives.build_node_payload(
        node,
        original_task,
        compacted,
    )
    schema = _strict_json_schema(node)
    if schema is not None:
        payload["response_format"] = schema
    field, allowance = _output_allowance(node)
    if field:
        payload.pop("max_tokens", None)
        payload.pop("max_completion_tokens", None)
        payload[field] = allowance

    reasoning = payload.get("reasoning")
    if isinstance(reasoning, Mapping):
        budget = completion_token_budget(
            node,
            total_allowance=allowance,
            reasoning_effort=str(
                reasoning.get("effort")
                or _reasoning_effort(node)
            ),
        )
        if budget["reasoning_max_tokens"]:
            payload["reasoning"] = {
                "max_tokens": budget["reasoning_max_tokens"],
                "exclude": True,
            }
        else:
            payload.pop("reasoning", None)
    provider = dict(payload.get("provider") or {})
    provider["require_parameters"] = True
    payload["provider"] = provider
    return payload


def robust_extract_answer(response: Mapping[str, Any]) -> str:
    answer = primitives.extract_answer(response)
    if answer:
        return answer
    for key in ("output_text", "text"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
