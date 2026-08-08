"""Final-payload request binding with current-run feedback floors.

The production prompt is fully assembled first.  Only then are prompt size, output
allowance and the request audit resolved.  Same-node learned floors from truncation
remain authoritative within the current run.  This closes the gap where earlier
binding estimated only the raw task/upstream text before the final system prompt
existed.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode
from v5_no_tools_policy import assert_request_has_no_tools
from v5_production_expert_policy import ProductionExpertPromptPolicy
from v5_runtime import ProductionRuntime
from v5_runtime_request_binding import (
    audit_bound_request,
    bind_request_knobs,
)
from v5_soft_resource_governance import SoftResourcePromptPolicy


def _positive_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def cost_effective_bind_request_knobs(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
    current_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    config, audit = bind_request_knobs(
        node,
        original_task,
        upstream,
        current_payload=current_payload,
    )
    profile = (
        node.parameter_profile
        if isinstance(node.parameter_profile, Mapping)
        else {}
    )
    learned_floor = _positive_int(
        profile.get("dynamic_output_allowance_floor_tokens"),
        0,
    )
    baseline = _positive_int(config.get("max_tokens"), 1)
    effective = max(baseline, learned_floor)
    config = dict(config)
    config["max_tokens"] = effective
    audit = dict(audit)
    audit.update(
        {
            "continuous_spatiotemporal_replanning": True,
            "pre_feedback_output_allowance_tokens": baseline,
            "current_run_feedback_output_floor_tokens": learned_floor or None,
            "dynamic_output_allowance_tokens": effective,
            "current_run_feedback_floor_applied": effective > baseline,
            "current_run_replan_epoch": _positive_int(
                profile.get("current_run_replan_epoch"),
                0,
            ),
            "final_payload_measured_before_effective_binding": True,
            "recompute_trigger": (
                "final-current-payload-plus-current-run-node-feedback"
            ),
        }
    )
    return config, audit


class CostEffectiveFinalPayloadPromptPolicy(ProductionExpertPromptPolicy):
    """Assemble prompt first, then bind current-request resource parameters."""

    def build_payload(
        self,
        node: SelectedNode,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        # Call the resource/prompt parent directly to avoid the older production
        # method binding knobs before it can observe the completed payload.
        payload = SoftResourcePromptPolicy.build_payload(
            self,
            node,
            original_task,
            upstream,
        )
        payload.pop("provider", None)
        request_knobs, binding_audit = cost_effective_bind_request_knobs(
            node,
            original_task,
            upstream,
            payload,
        )
        payload.update(request_knobs)
        effective_audit = audit_bound_request(node, payload)
        if effective_audit["status"] != "PASS":
            raise RuntimeError(
                "computed runtime knobs were not consumed: "
                + ",".join(effective_audit["computed_but_unused"])
            )
        assert_request_has_no_tools(
            payload,
            context=f"production expert {node.node_id} request",
        )
        # Keep the binding evidence available to the engine without adding any
        # request field that could reach OpenRouter.  The request audit writer
        # derives the same values from the payload and node parameter profile.
        self._last_cost_effective_binding_audit = dict(binding_audit)
        return payload


def install_cost_effective_final_payload_policy(
    runtime: ProductionRuntime,
) -> ProductionRuntime:
    runtime.prompt_policy = CostEffectiveFinalPayloadPromptPolicy()
    runtime.execution_engine.prompt_policy = runtime.prompt_policy
    return runtime


__all__ = [
    "CostEffectiveFinalPayloadPromptPolicy",
    "cost_effective_bind_request_knobs",
    "install_cost_effective_final_payload_policy",
]
