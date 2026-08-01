"""Current-catalog cost, endpoint-risk and bounded-output policies for V5.

No cross-task model or provider history is read. Every estimate is derived only
from the current task, current catalog/endpoint snapshot, and current run state.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from typing import Any, Mapping, Sequence

import v5_executor as executor
import v5_planner as planner
from execution_graph import SelectedNode

MIN_PROVIDER_RELIABILITY = 0.90
COST_UNCERTAINTY_MULTIPLIER = 1.18
MAX_UPSTREAM_CHARS_PER_NODE = 6_000
MAX_UPSTREAM_CHARS_TOTAL = 24_000
MAX_OUTPUT_ALLOWANCE_TOKENS = 32_768
MIN_VISIBLE_OUTPUT_RESERVE_TOKENS = 1_024
MIN_REASONING_BUDGET_TOKENS = 1_024
_HIGH_REASONING_FUNCTIONS = {
    "synthesis", "quantitative_modeling", "implementation",
    "adversarial_reasoning", "counterfactual_analysis",
}
_REASONING_SHARE_BY_EFFORT = {
    "minimal": 0.10,
    "low": 0.20,
    "medium": 0.50,
    "high": 0.68,
    "xhigh": 0.72,
    "max": 0.72,
}
_VISIBLE_FLOOR_BY_FUNCTION = {
    "synthesis": 3_072,
    "adversarial_reasoning": 2_048,
    "quantitative_modeling": 2_048,
    "implementation": 1_536,
    "counterfactual_analysis": 1_536,
}

_ORIGINAL_ESTIMATED_COST = planner._estimated_cost
_ORIGINAL_CANDIDATE_FOR = planner._candidate_for
_ORIGINAL_BUILD_NODE_PAYLOAD = executor.build_node_payload
_ORIGINAL_EXTRACT_ANSWER = executor._extract_answer
_INSTALLED = False


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


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


def completion_envelope(work: Mapping[str, Any], endpoint_max: int) -> int:
    """Return a conservative but bounded completion envelope."""
    context = work.get("context_requirements", {})
    output = _int(context.get("expected_output_tokens"), 1024)
    reasoning = _int(context.get("expected_reasoning_tokens"), 0)
    machine = bool(work.get("output_contract", {}).get("machine_readable_required"))
    envelope = max(
        output + reasoning,
        int(math.ceil(output * 1.70 + reasoning * 1.20)),
        3_072 if machine else 2_048,
    )
    maximum = min(MAX_OUTPUT_ALLOWANCE_TOKENS, endpoint_max or MAX_OUTPUT_ALLOWANCE_TOKENS)
    return max(1_024, min(envelope, maximum))


def conservative_estimated_cost(
    endpoint: Mapping[str, Any],
    works: Sequence[Mapping[str, Any]],
    bundle_discount: float = 1.0,
) -> float:
    """Use a reasoning-inclusive P95-style estimate from the current snapshot."""
    prompt_tokens = 0
    completion_tokens = 0
    endpoint_max = _int(endpoint.get("max_completion_tokens"), 0)
    for work in works:
        context = work.get("context_requirements", {})
        prompt_tokens += (
            _int(context.get("system_prompt_tokens"))
            + _int(context.get("original_task_tokens"))
            + _int(context.get("visible_upstream_tokens"))
        )
        completion_tokens += completion_envelope(work, endpoint_max)
    discount = max(0.1, float(bundle_discount))
    prompt_tokens = int(math.ceil(prompt_tokens * discount))
    completion_tokens = int(math.ceil(completion_tokens * discount))
    base = (
        prompt_tokens * _float(endpoint.get("prompt_price_per_million"))
        + completion_tokens * _float(endpoint.get("completion_price_per_million"))
    ) / 1_000_000
    reliability = _clamp(_float(endpoint.get("reliability"), 0.95))
    reliability_reserve = 1.0 + max(0.0, 0.98 - reliability) * 1.75
    return round(base * COST_UNCERTAINTY_MULTIPLIER * reliability_reserve, 8)


def hardened_candidate_for(*args: Any, **kwargs: Any) -> Any:
    """Qualify a candidate only from current endpoint and current task data."""
    endpoint = args[4] if len(args) > 4 and isinstance(args[4], Mapping) else {}
    reliability = _clamp(_float(endpoint.get("reliability"), 0.0))
    if reliability < MIN_PROVIDER_RELIABILITY:
        return None
    candidate = _ORIGINAL_CANDIDATE_FOR(*args, **kwargs)
    if candidate is None:
        return None

    failure = _clamp(
        max(candidate.failure_probability, 1.0 - reliability)
        + (1.0 - reliability) * 0.50
    )
    estimated_cost = candidate.estimated_cost * (1.0 + failure * 0.40)

    works = args[2] if len(args) > 2 and isinstance(args[2], Sequence) else ()
    endpoint_max = _int(endpoint.get("max_completion_tokens"), 0)
    recommended = sum(
        completion_envelope(work, endpoint_max)
        for work in works
        if isinstance(work, Mapping)
    )
    maximum = min(MAX_OUTPUT_ALLOWANCE_TOKENS, endpoint_max or MAX_OUTPUT_ALLOWANCE_TOKENS)
    profile = dict(candidate.parameter_profile)
    profile.update({
        "recommended_output_allowance_tokens": min(maximum, max(1_024, recommended)),
        "cost_estimation_policy": "current-snapshot-reasoning-inclusive-p95-r8",
        "provider_reliability_floor": MIN_PROVIDER_RELIABILITY,
        "cross_task_history_used": False,
    })
    return replace(
        candidate,
        failure_probability=round(failure, 6),
        estimated_cost=round(estimated_cost, 8),
        parameter_profile=profile,
    )


def _strict_json_schema(node: SelectedNode) -> Mapping[str, Any] | None:
    if not node.output_contract.get("machine_readable_required"):
        return None
    supported = {
        str(value).casefold()
        for value in node.parameter_profile.get("supported_parameters", [])
    }
    if not supported.intersection({"structured_outputs", "response_format", "json_schema"}):
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
    """Shrink values while preserving a valid JSON object and all top-level keys."""
    def shrink(max_items: int, max_chars: int) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key, raw in value.items():
            if isinstance(raw, list):
                rows = [str(item)[:max_chars] for item in raw[:max_items]]
                compact[str(key)] = rows or [""]
            elif isinstance(raw, Mapping):
                compact[str(key)] = {
                    str(inner): str(item)[:max_chars]
                    for inner, item in list(raw.items())[:max_items]
                }
            elif isinstance(raw, str):
                compact[str(key)] = raw[: max_chars * max_items]
            else:
                compact[str(key)] = raw
        return compact

    for max_items, max_chars in ((6, 500), (4, 260), (2, 160), (1, 96), (1, 48)):
        rendered = json.dumps(shrink(max_items, max_chars), ensure_ascii=False, separators=(",", ":"))
        if len(rendered) <= limit:
            return rendered
    skeleton = {
        str(key): ([] if isinstance(raw, list) else "")
        for key, raw in value.items()
    }
    return json.dumps(skeleton, ensure_ascii=False, separators=(",", ":"))


def _compact_answer(answer: str, limit: int) -> str:
    if len(answer) <= limit:
        return answer
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, Mapping):
        return _compact_json(parsed, limit)
    marker = "\n\n[中间省略：上游结果已确定性压缩]\n\n"
    available = max(0, limit - len(marker))
    head = available * 2 // 3
    tail = available - head
    return answer[:head] + marker + answer[-tail:] if tail else answer[:limit]


def _output_allowance(node: SelectedNode) -> tuple[str | None, int]:
    supported = {
        str(value).casefold()
        for value in node.parameter_profile.get("supported_parameters", [])
    }
    raw_limit = os.getenv("V5_MAX_OUTPUT_ALLOWANCE_TOKENS", str(MAX_OUTPUT_ALLOWANCE_TOKENS))
    maximum = min(MAX_OUTPUT_ALLOWANCE_TOKENS, max(1_024, _int(raw_limit, MAX_OUTPUT_ALLOWANCE_TOKENS)))
    recommended = _int(
        node.parameter_profile.get("recommended_output_allowance_tokens"),
        4_096 if node.output_contract.get("machine_readable_required") else 2_048,
    )
    floor = 4_096 if (
        node.output_contract.get("machine_readable_required")
        or set(node.functions) & _HIGH_REASONING_FUNCTIONS
    ) else 2_048
    allowance = min(maximum, max(floor, recommended))
    if "max_completion_tokens" in supported:
        return "max_completion_tokens", allowance
    if "max_tokens" in supported:
        return "max_tokens", allowance
    return None, allowance


def _reasoning_effort(node: SelectedNode) -> str:
    request_reasoning = node.request_config.get("reasoning")
    if isinstance(request_reasoning, Mapping):
        effort = str(request_reasoning.get("effort") or "").casefold()
        if effort:
            return effort
    decisions = node.parameter_profile.get("dynamic_parameter_decisions")
    if isinstance(decisions, Mapping):
        effort = str(decisions.get("reasoning_effort") or "").casefold()
        if effort:
            return effort
    return str(node.reasoning_profile.get("effort") or "medium").casefold()


def completion_token_budget(
    node: SelectedNode,
    *,
    total_allowance: int | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Split completion permission into capped reasoning and protected visible output.

    The total allowance remains endpoint- and task-derived. The visible reserve is
    computed from the selected node's delivery contract and functions. Reasoning
    receives only the remaining bounded share, so it cannot consume the entire
    completion allowance before a deliverable answer is emitted.
    """
    total = max(
        1,
        int(total_allowance if total_allowance is not None else _output_allowance(node)[1]),
    )
    effort = str(reasoning_effort or _reasoning_effort(node) or "medium").casefold()
    reasoning_enabled = bool(node.reasoning_profile.get("reasoning_enabled", True))
    supported = {
        str(value).casefold()
        for value in node.parameter_profile.get("supported_parameters", [])
    }
    reasoning_supported = "reasoning" in supported or isinstance(
        node.request_config.get("reasoning"), Mapping
    )

    fields = [
        str(value).strip()
        for value in node.output_contract.get("required_fields", [])
        if str(value).strip()
    ]
    explicit_sections = _int(
        node.output_contract.get("task_explicit_delivery_section_count"),
        0,
    )
    functions = {str(value).casefold() for value in node.functions}
    function_floor = max(
        (_VISIBLE_FLOOR_BY_FUNCTION.get(name, 0) for name in functions),
        default=0,
    )
    contract_floor = max(
        len(fields) * 192,
        min(6_400, explicit_sections * 320),
    )
    if node.output_contract.get("machine_readable_required"):
        contract_floor = max(contract_floor, 2_048)
    if node.output_contract.get("task_explicit_long_form_required"):
        contract_floor = max(contract_floor, 4_096)

    share = _REASONING_SHARE_BY_EFFORT.get(effort, 0.50)
    ratio_floor = int(math.ceil(total * (1.0 - share)))
    visible_reserve = max(
        MIN_VISIBLE_OUTPUT_RESERVE_TOKENS,
        function_floor,
        contract_floor,
        ratio_floor,
    )

    if not reasoning_enabled or not reasoning_supported:
        reasoning_max = 0
        visible_reserve = total
    else:
        maximum_reasoning_space = max(0, total - visible_reserve)
        desired_reasoning = int(math.floor(total * share))
        reasoning_max = min(maximum_reasoning_space, desired_reasoning)
        if reasoning_max < MIN_REASONING_BUDGET_TOKENS:
            if total >= MIN_REASONING_BUDGET_TOKENS + MIN_VISIBLE_OUTPUT_RESERVE_TOKENS:
                reasoning_max = MIN_REASONING_BUDGET_TOKENS
                visible_reserve = total - reasoning_max
            else:
                reasoning_max = 0
                visible_reserve = total

    if reasoning_max:
        reasoning_max = min(reasoning_max, total - 1)
        visible_reserve = min(visible_reserve, total - reasoning_max)
    visible_reserve = max(1, min(total, visible_reserve))

    if reasoning_max >= total:
        raise RuntimeError("reasoning token budget must be below total completion allowance")
    if total - reasoning_max < visible_reserve:
        raise RuntimeError("visible output reserve is not protected by completion budget")

    return {
        "policy": "task-contract-reasoning-visible-output-split-v1",
        "total_completion_allowance_tokens": total,
        "reasoning_max_tokens": reasoning_max,
        "visible_output_reserve_tokens": visible_reserve,
        "reasoning_effort_source": effort,
        "reasoning_supported": reasoning_supported,
        "reasoning_enabled": reasoning_enabled,
        "required_field_count": len(fields),
        "explicit_delivery_section_count": explicit_sections,
        "functions": sorted(functions),
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
        clipped = _compact_answer(answer, min(MAX_UPSTREAM_CHARS_PER_NODE, remaining))
        compacted.append({**dict(row), "answer": clipped})
        remaining -= len(clipped)

    payload = _ORIGINAL_BUILD_NODE_PAYLOAD(node, original_task, compacted)
    schema = _strict_json_schema(node)
    if schema is not None:
        payload["response_format"] = schema

    reasoning = payload.get("reasoning")
    functions = {str(value).casefold() for value in node.functions}
    if isinstance(reasoning, Mapping) and str(reasoning.get("effort") or "").casefold() == "high":
        if not functions.intersection(_HIGH_REASONING_FUNCTIONS):
            payload["reasoning"] = {**dict(reasoning), "effort": "medium"}
            reasoning = payload["reasoning"]

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
            reasoning_effort=str(reasoning.get("effort") or _reasoning_effort(node)),
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
    answer = _ORIGINAL_EXTRACT_ANSWER(response)
    if answer:
        return answer
    for key in ("output_text", "text"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    output = response.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for row in content:
                if isinstance(row, Mapping) and isinstance(row.get("text"), str):
                    parts.append(row["text"])
        if parts:
            return "\n".join(parts).strip()
    return ""


def install() -> None:
    """Compatibility installer for non-production callers; no history is used."""
    global _INSTALLED
    global _ORIGINAL_CANDIDATE_FOR
    global _ORIGINAL_BUILD_NODE_PAYLOAD
    global _ORIGINAL_EXTRACT_ANSWER
    if _INSTALLED:
        return
    _ORIGINAL_CANDIDATE_FOR = planner._candidate_for
    _ORIGINAL_BUILD_NODE_PAYLOAD = executor.build_node_payload
    _ORIGINAL_EXTRACT_ANSWER = executor._extract_answer
    _INSTALLED = True
    planner._estimated_cost = conservative_estimated_cost
    planner._candidate_for = hardened_candidate_for
    executor.build_node_payload = hardened_build_node_payload
    executor._extract_answer = robust_extract_answer
