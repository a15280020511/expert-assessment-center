"""Reasoning-inclusive cost, endpoint-risk and bounded-output hardening for V5."""
from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_executor as executor
import v5_planner as planner
from execution_graph import SelectedNode

MIN_PROVIDER_RELIABILITY = 0.90
COST_UNCERTAINTY_MULTIPLIER = 1.18
MAX_UPSTREAM_CHARS_PER_NODE = 6_000
MAX_UPSTREAM_CHARS_TOTAL = 24_000
MAX_OUTPUT_ALLOWANCE_TOKENS = 10_000
_HIGH_REASONING_FUNCTIONS = {
    "synthesis", "quantitative_modeling", "implementation",
    "adversarial_reasoning", "counterfactual_analysis",
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


def _history_rows() -> Mapping[str, Any]:
    path = os.getenv("MODEL_HISTORY_PATH", "").strip()
    if not path:
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    models = payload.get("models") if isinstance(payload, Mapping) else None
    return models if isinstance(models, Mapping) else {}


def _history_for(model: str, provider_endpoint: str) -> Mapping[str, Any]:
    rows = _history_rows()
    row = rows.get(provider_endpoint)
    if isinstance(row, Mapping):
        return row
    row = rows.get(model)
    return row if isinstance(row, Mapping) else {}


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
    """Use a reasoning-inclusive P95-style envelope before selection."""
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
    endpoint = args[4] if len(args) > 4 and isinstance(args[4], Mapping) else {}
    reliability = _clamp(_float(endpoint.get("reliability"), 0.0))
    if reliability < MIN_PROVIDER_RELIABILITY:
        return None
    candidate = _ORIGINAL_CANDIDATE_FOR(*args, **kwargs)
    if candidate is None:
        return None

    history = _history_for(candidate.model, candidate.provider_endpoint)
    calls = _int(history.get("calls") or history.get("sample_count"), 0)
    observed = max(
        _clamp(_float(history.get("failure_rate"), 0.0)),
        _clamp(_float(history.get("empty_rate"), 0.0)),
        _clamp(_float(history.get("truncation_rate"), 0.0)),
        _clamp(_float(history.get("rate_limit_rate"), 0.0)),
    )
    if history.get("success_rate") is not None:
        observed = max(observed, 1.0 - _clamp(_float(history.get("success_rate"), 1.0)))
    confidence = min(1.0, calls / 20.0)
    failure = _clamp(
        max(candidate.failure_probability, 1.0 - reliability)
        + confidence * observed * 0.80
        + (1.0 - reliability) * 0.50
    )
    multiplier = max(
        1.0,
        _float(history.get("p95_cost_multiplier"), 1.0),
        _float(history.get("cost_multiplier"), 1.0),
    )
    estimated_cost = candidate.estimated_cost * multiplier * (1.0 + failure * 0.40)

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
        "cost_estimation_policy": "reasoning-inclusive-p95-envelope-r8",
        "provider_reliability_floor": MIN_PROVIDER_RELIABILITY,
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

    field, allowance = _output_allowance(node)
    if field:
        payload.pop("max_tokens", None)
        payload.pop("max_completion_tokens", None)
        payload[field] = allowance

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
