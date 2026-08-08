"""Current-request model timeout binding under a finite safety cap.

The effective timeout is derived from the fully assembled current payload, the
actual output allowance and same-node current-run feedback.  Fixed conversion
rules are explicit infrastructure invariants; they are not business gates and
are not presented as predictions of future latency.
"""
from __future__ import annotations

import math
from dataclasses import is_dataclass, replace
from types import SimpleNamespace
from typing import Any, Mapping

from v5_runtime_request_binding import estimate_payload_tokens

SCHEMA_VERSION = "current-request-model-timeout-binding-2-resource-closure"
_EFFORT_ORDER = {
    "none": 0,
    "minimal": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "xhigh": 5,
    "max": 6,
}
_INFRASTRUCTURE_MINIMUM_TIMEOUT_SECONDS = 30


def _effort(payload: Mapping[str, Any]) -> str:
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, Mapping):
        value = str(reasoning.get("effort") or "medium").casefold()
        if value in _EFFORT_ORDER:
            return value
    return "medium"


def _effort_pressure(effort: str) -> float:
    rank = _EFFORT_ORDER.get(effort, _EFFORT_ORDER["medium"])
    return 1.0 + rank / (max(_EFFORT_ORDER.values()) + 1)


def _profile(node: Any) -> Mapping[str, Any]:
    raw = getattr(node, "parameter_profile", {})
    return raw if isinstance(raw, Mapping) else {}


def _multiplier(node: Any) -> float:
    profile = _profile(node)
    try:
        value = float(profile.get("dynamic_model_timeout_multiplier", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return value if math.isfinite(value) and value >= 1.0 else 1.0


def _learned_floor(node: Any) -> int:
    profile = _profile(node)
    try:
        value = int(profile.get("dynamic_model_timeout_floor_seconds") or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _timeout_parameter_id(node: Any) -> str | None:
    profile = _profile(node)
    ids = profile.get("runtime_resource_parameter_ids")
    ids = ids if isinstance(ids, Mapping) else {}
    value = str(ids.get("model-timeout-effective") or "").strip()
    return value or None


def dynamic_model_timeout_seconds(
    node: Any,
    payload: Mapping[str, Any],
    safety_cap_seconds: int,
) -> tuple[int, dict[str, Any]]:
    """Derive effective timeout from final payload and current-run feedback."""
    cap = max(1, int(safety_cap_seconds))
    prompt_tokens = max(1, estimate_payload_tokens(payload))
    try:
        output_tokens = max(1, int(payload.get("max_tokens") or 1))
    except (TypeError, ValueError):
        output_tokens = 1
    effort = _effort(payload)
    pressure = _effort_pressure(effort)
    request_mass = math.sqrt(prompt_tokens) + math.sqrt(output_tokens)
    pre_feedback = math.ceil(request_mass * pressure * _multiplier(node))
    learned_floor = _learned_floor(node)
    minimum = min(cap, _INFRASTRUCTURE_MINIMUM_TIMEOUT_SECONDS)
    effective = min(cap, max(minimum, pre_feedback, learned_floor))
    audit = {
        "schema_version": SCHEMA_VERSION,
        "type": "dynamic-model-timeout-binding",
        "status": "PASS",
        "node_id": str(getattr(node, "node_id", "")),
        "prompt_token_estimate": prompt_tokens,
        "prompt_source": "final-current-payload-before-send",
        "output_allowance_tokens": output_tokens,
        "reasoning_effort": effort,
        "reasoning_pressure": round(pressure, 8),
        "reasoning_pressure_is_exact_future_latency_prediction": False,
        "current_run_timeout_multiplier": _multiplier(node),
        "pre_feedback_timeout_seconds": pre_feedback,
        "current_run_feedback_timeout_floor_seconds": learned_floor or None,
        "effective_timeout_seconds": effective,
        "safety_cap_seconds": cap,
        "safety_cap_classification": "infrastructure_invariant",
        "safety_cap_is_business_gate": False,
        "minimum_timeout_seconds": minimum,
        "minimum_timeout_classification": "infrastructure_invariant",
        "minimum_timeout_is_business_gate": False,
        "timeout_parameter_id": _timeout_parameter_id(node),
        "timeout_parameter_consumer": "openrouter-request-timeout",
        "effective_timeout_source": (
            "final-current-request-shape-plus-current-run-feedback"
        ),
        "recompute_trigger": (
            "final-current-payload-or-current-run-timeout-feedback"
        ),
        "current_task_only": True,
        "cross_task_history_used": False,
    }
    return int(effective), audit


def with_model_timeout(run_config: Any, timeout_seconds: int) -> Any:
    """Clone run config with only the effective per-request timeout changed."""
    value = int(timeout_seconds)
    if is_dataclass(run_config):
        try:
            return replace(run_config, model_timeout_seconds=value)
        except TypeError:
            pass
    if hasattr(run_config, "__dict__"):
        clone = SimpleNamespace(**vars(run_config))
        clone.model_timeout_seconds = value
        return clone
    return SimpleNamespace(
        model_timeout_seconds=value,
        api_key=getattr(run_config, "api_key", None),
    )


__all__ = [
    "SCHEMA_VERSION",
    "dynamic_model_timeout_seconds",
    "with_model_timeout",
]
