"""Execution-only request, context and response hardening.

Token and cost values in this module are advisory telemetry. The module never
emits a local output-token ceiling or a reasoning-token budget. Native provider
capacity may still bound an estimate because that is an objective compatibility
fact, not a center-defined budget.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Mapping, Sequence

import v5_execution_primitives as primitives
from execution_graph import SelectedNode

COST_UNCERTAINTY_MULTIPLIER = 1.18
MAX_UPSTREAM_CHARS_PER_NODE = 6_000
MAX_UPSTREAM_CHARS_TOTAL = 24_000


def _integer(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def completion_envelope(
    work: Mapping[str, Any],
    endpoint_max: int,
) -> int:
    """Estimate completion demand without a local ceiling.

    The exact endpoint's native maximum may cap the estimate because a request
    cannot exceed an upstream service's actual capacity.
    """
    context = work.get("context_requirements", {})
    output = _integer(context.get("expected_output_tokens"), 1_024)
    reasoning = _integer(context.get("expected_reasoning_tokens"), 0)
    advisory = max(
        output + reasoning,
        int(output * 1.7 + reasoning * 1.2),
        1_024,
    )
    native_maximum = _integer(endpoint_max)
    if native_maximum > 0:
        return min(advisory, native_maximum)
    return advisory


def conservative_estimated_cost(
    endpoint: Mapping[str, Any],
    works: Sequence[Mapping[str, Any]],
    bundle_discount: float = 1.0,
) -> float:
    """Return auditable cost telemetry; callers must not use it as a stop gate."""
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
        * primitives.finite_number(endpoint.get("prompt_price_per_million"))
        + int(completion_tokens * discount)
        * primitives.finite_number(endpoint.get("completion_price_per_million"))
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


def _output_allowance(node: SelectedNode) -> tuple[None, int]:
    """Return advisory demand only; no provider request field is selected."""
    recommended = _integer(
        node.parameter_profile.get(
            "recommended_output_allowance_tokens"
        ),
        2_048,
    )
    return None, max(1, recommended)


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
    """Expose advisory telemetry without creating enforceable Token budgets."""
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
    return {
        "policy": "prompt-led-soft-governance",
        "total_completion_advisory_tokens": total,
        "total_completion_allowance_tokens": total,
        "reasoning_effort_source": effort,
        "reasoning_max_tokens": None,
        "visible_output_reserve_tokens": None,
        "local_token_ceiling_enforced": False,
        "reasoning_token_budget_enforced": False,
    }


def _request_safe_node(node: SelectedNode) -> SelectedNode:
    """Remove legacy local Token fields before the base request constructor."""
    request = dict(node.request_config)
    request.pop("max_tokens", None)
    request.pop("max_completion_tokens", None)
    reasoning = request.get("reasoning")
    if isinstance(reasoning, Mapping):
        cleaned = dict(reasoning)
        for key in (
            "max_tokens",
            "max_completion_tokens",
            "budget_tokens",
            "token_budget",
        ):
            cleaned.pop(key, None)
        if cleaned:
            request["reasoning"] = cleaned
        else:
            request.pop("reasoning", None)
    return replace(node, request_config=request)


def hardened_build_node_payload(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a provider-locked payload without local Token ceilings."""
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

    safe_node = _request_safe_node(node)
    payload = primitives.build_node_payload(
        safe_node,
        original_task,
        compacted,
    )

    reasoning = payload.get("reasoning")
    if isinstance(reasoning, Mapping):
        effort = str(reasoning.get("effort") or _reasoning_effort(node))
        payload["reasoning"] = {
            "effort": effort,
            "exclude": bool(reasoning.get("exclude", True)),
        }

    schema = _strict_json_schema(node)
    if schema is not None:
        payload["response_format"] = schema
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
