"""Reasoning-inclusive cost, endpoint-risk and output hardening for V5."""
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
COST_UNCERTAINTY_MULTIPLIER = 1.12
MAX_UPSTREAM_CHARS_PER_NODE = 8_000
MAX_UPSTREAM_CHARS_TOTAL = 28_000
MAX_OUTPUT_ALLOWANCE_TOKENS = 10_000
MIN_OUTPUT_ALLOWANCE_TOKENS = 1_024
DELIVERY_FUNCTIONS = frozenset({"synthesis", "delivery", "adjudication"})

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
    context = work.get("context_requirements", {})
    output = _int(context.get("expected_output_tokens"), 1024)
    reasoning = _int(context.get("expected_reasoning_tokens"), 0)
    machine = bool(work.get("output_contract", {}).get("machine_readable_required"))
    envelope = max(
        output + reasoning,
        int(math.ceil(output * 2.35 + reasoning * 1.35)),
        3_072 if machine else 2_048,
    )
    return min(envelope, endpoint_max) if endpoint_max > 0 else envelope


def conservative_estimated_cost(
    endpoint: Mapping[str, Any],
    works: Sequence[Mapping[str, Any]],
    bundle_discount: float = 1.0,
) -> float:
    """Use a P95-style completion envelope that includes reasoning-token spend."""
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
    reserve = 1.0 + max(0.0, 0.98 - reliability) * 1.5
    return round(base * COST_UNCERTAINTY_MULTIPLIER * reserve, 8)


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
    estimated_cost = candidate.estimated_cost * multiplier * (1.0 + failure * 0.35)

    works = args[2] if len(args) > 2 and isinstance(args[2], Sequence) else ()
    endpoint_max = _int(endpoint.get("max_completion_tokens"), 0)
    recommended = sum(
        completion_envelope(work, endpoint_max)
        for work in works
        if isinstance(work, Mapping)
    )
    profile = dict(candidate.parameter_profile)
    profile.update({
        "recommended_output_allowance_tokens": min(
            endpoint_max or MAX_OUTPUT_ALLOWANCE_TOKENS,
            max(MIN_OUTPUT_ALLOWANCE_TOKENS, recommended),
            MAX_OUTPUT_ALLOWANCE_TOKENS,
        ),
        "cost_estimation_policy": "reasoning-inclusive-p95-envelope",
        "provider_reliability_floor": MIN_PROVIDER_RELIABILITY,
    })
    return replace(
        candidate,
        failure_probability=round(failure, 6),
        estimated_cost=round(estimated_cost, 8),
        parameter_profile=profile,
    )


def _is_delivery_node(node: SelectedNode) -> bool:
    return bool(DELIVERY_FUNCTIONS.intersection(str(value) for value in node.functions))


def _effective_output_node(node: SelectedNode) -> SelectedNode:
    """Defer strict machine-readable delivery to final synthesis nodes.

    R6 showed that forcing every intermediate analysis node to emit a large JSON
    object sharply increased empty responses, truncation and cost. Intermediate
    nodes therefore return stable prose; final delivery nodes retain the user-facing
    structured contract.
    """
    if _is_delivery_node(node) or not node.output_contract.get("machine_readable_required"):
        return node
    output_contract = dict(node.output_contract)
    output_contract["machine_readable_required"] = False
    output_contract["structured_delivery_deferred_to_final"] = True
    request_config = dict(node.request_config)
    request_config.pop("response_format", None)
    request_config.pop("json_schema", None)
    parameter_profile = dict(node.parameter_profile)
    parameters = dict(parameter_profile.get("parameters", {}))
    parameters.pop("response_format", None)
    parameters.pop("json_schema", None)
    parameter_profile["parameters"] = parameters
    return replace(
        node,
        output_contract=output_contract,
        request_config=request_config,
        parameter_profile=parameter_profile,
    )


def _strict_json_schema(node: SelectedNode) -> Mapping[str, Any] | None:
    if not _is_delivery_node(node) or not node.output_contract.get("machine_readable_required"):
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
            "name": "v5_final_delivery",
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


def _output_allowance(node: SelectedNode) -> int:
    recommended = _int(
        node.parameter_profile.get("recommended_output_allowance_tokens"),
        3_072 if _is_delivery_node(node) else 2_048,
    )
    return max(
        MIN_OUTPUT_ALLOWANCE_TOKENS,
        min(MAX_OUTPUT_ALLOWANCE_TOKENS, recommended),
    )


def _output_allowance_field(node: SelectedNode) -> str:
    supported = {
        str(value).casefold()
        for value in node.parameter_profile.get("supported_parameters", [])
    }
    return "max_completion_tokens" if "max_completion_tokens" in supported else "max_tokens"


def hardened_build_node_payload(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    effective_node = _effective_output_node(node)
    compacted: list[dict[str, Any]] = []
    remaining = MAX_UPSTREAM_CHARS_TOTAL
    for row in sorted(
        upstream,
        key=lambda value: (
            -_float(value.get("quality_score"), 0.0),
            str(value.get("node_id") or ""),
        ),
    ):
        if remaining <= 0:
            break
        answer = str(row.get("answer") or "")
        if not answer:
            continue
        clipped = answer[: min(MAX_UPSTREAM_CHARS_PER_NODE, remaining)]
        compacted.append({**dict(row), "answer": clipped})
        remaining -= len(clipped)
    payload = _ORIGINAL_BUILD_NODE_PAYLOAD(effective_node, original_task, compacted)
    schema = _strict_json_schema(effective_node)
    if schema is not None:
        payload["response_format"] = schema
        provider = dict(payload.get("provider") or {})
        provider["require_parameters"] = True
        payload["provider"] = provider

    # 10000 is an absolute permission, not a requested output length. Every node
    # receives a lower task-derived maximum whenever possible.
    payload.pop("max_tokens", None)
    payload.pop("max_completion_tokens", None)
    payload[_output_allowance_field(effective_node)] = _output_allowance(effective_node)
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
    # Bind after capability calibration and output-contract delivery have been
    # installed, so hardening composes with those policies instead of bypassing them.
    _ORIGINAL_CANDIDATE_FOR = planner._candidate_for
    _ORIGINAL_BUILD_NODE_PAYLOAD = executor.build_node_payload
    _ORIGINAL_EXTRACT_ANSWER = executor._extract_answer
    _INSTALLED = True
    planner._estimated_cost = conservative_estimated_cost
    planner._candidate_for = hardened_candidate_for
    executor.build_node_payload = hardened_build_node_payload
    executor._extract_answer = robust_extract_answer
