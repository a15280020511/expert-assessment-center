"""Current-request model timeout binding under a finite safety cap."""
from __future__ import annotations

import math
from dataclasses import is_dataclass, replace
from types import SimpleNamespace
from typing import Any, Mapping

SCHEMA_VERSION = "current-request-model-timeout-binding-1"
_REASONING_RATIO = {
    "max": 0.95,
    "xhigh": 0.95,
    "high": 0.80,
    "medium": 0.50,
    "low": 0.20,
    "minimal": 0.10,
    "none": 0.0,
}


def _message_characters(payload: Mapping[str, Any]) -> int:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return 0
    total = 0
    for row in messages:
        if isinstance(row, Mapping):
            total += len(str(row.get("content") or ""))
    return total


def _effort(payload: Mapping[str, Any]) -> str:
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, Mapping):
        value = str(reasoning.get("effort") or "medium").casefold()
        if value in _REASONING_RATIO:
            return value
    return "medium"


def _multiplier(node: Any) -> float:
    profile = getattr(node, "parameter_profile", {})
    if not isinstance(profile, Mapping):
        return 1.0
    try:
        value = float(profile.get("dynamic_model_timeout_multiplier", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return value if math.isfinite(value) and value >= 1.0 else 1.0


def dynamic_model_timeout_seconds(
    node: Any,
    payload: Mapping[str, Any],
    safety_cap_seconds: int,
) -> tuple[int, dict[str, Any]]:
    """Derive effective timeout from current request shape, never above cap."""
    cap = max(1, int(safety_cap_seconds))
    prompt_tokens = max(1, math.ceil(_message_characters(payload) / 4))
    try:
        output_tokens = max(1, int(payload.get("max_tokens") or 1))
    except (TypeError, ValueError):
        output_tokens = 1
    effort = _effort(payload)
    reasoning_ratio = _REASONING_RATIO[effort]

    # Square-root scaling grows with request size without turning large token
    # allowances into equally large wall-clock reservations. The safety cap is
    # an infrastructure invariant; the effective value is current-request data.
    request_mass = math.sqrt(prompt_tokens) + math.sqrt(output_tokens)
    effort_pressure = 1.0 + reasoning_ratio
    derived = math.ceil(request_mass * effort_pressure * _multiplier(node))
    effective = min(cap, max(30, derived))
    audit = {
        "schema_version": SCHEMA_VERSION,
        "type": "dynamic-model-timeout-binding",
        "status": "PASS",
        "node_id": str(getattr(node, "node_id", "")),
        "prompt_token_estimate": prompt_tokens,
        "output_allowance_tokens": output_tokens,
        "reasoning_effort": effort,
        "reasoning_pressure": reasoning_ratio,
        "current_run_timeout_multiplier": _multiplier(node),
        "effective_timeout_seconds": effective,
        "safety_cap_seconds": cap,
        "safety_cap_is_business_gate": False,
        "effective_timeout_source": "current-request-shape",
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
