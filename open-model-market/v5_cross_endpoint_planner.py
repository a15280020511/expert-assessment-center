"""Stable public facade for company-safe cross-endpoint recovery planning.

The implementation module keeps the general planner. This facade adds one
fail-safe ordering signal for scarce recovery calls: candidates likely to spend
the completion allowance in excluded reasoning without an explicit reasoning
control are ranked behind candidates with controllable visible delivery.
"""
from __future__ import annotations

from typing import Any, Mapping

import v5_cross_endpoint_planner_impl as _impl

for _export_name in dir(_impl):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_impl, _export_name)


class CrossEndpointPlannerPolicy(_impl.CrossEndpointPlannerPolicy):
    """Prefer recovery candidates capable of returning visible output."""

    @staticmethod
    def _reasoning_visibility_risk(row: Mapping[str, Any]) -> int:
        profile = row.get("parameter_profile")
        profile = profile if isinstance(profile, Mapping) else {}
        parameters = profile.get("parameters")
        parameters = parameters if isinstance(parameters, Mapping) else {}
        reasoning = parameters.get("reasoning")
        reasoning = reasoning if isinstance(reasoning, Mapping) else {}
        dynamic = profile.get("dynamic_parameter_decisions")
        dynamic = dynamic if isinstance(dynamic, Mapping) else {}
        effort = str(
            reasoning.get("effort")
            or dynamic.get("reasoning_effort")
            or ""
        ).strip().casefold()
        exclude = reasoning.get("exclude") is True
        supported_raw = profile.get("supported_parameters")
        supported = {
            str(value).strip().casefold()
            for value in supported_raw
            if str(value).strip()
        } if isinstance(supported_raw, (list, tuple, set)) else set()
        explicit_control = bool(
            profile.get("reasoning_token_ceiling_sent")
            or "reasoning_effort" in supported
        )
        serviceability = profile.get("operational_serviceability")
        serviceability = (
            serviceability if isinstance(serviceability, Mapping) else {}
        )
        expected_visible = max(
            0,
            int(serviceability.get("expected_visible_output_tokens") or 0),
        )
        return int(
            exclude
            and effort in {"high", "xhigh", "maximum", "max"}
            and expected_visible > 0
            and not explicit_control
        )

    def _recovery_sort_key(
        self,
        row: Mapping[str, Any],
        selected_provider: str,
        *,
        critical_delivery: bool,
    ) -> tuple[Any, ...]:
        base = super()._recovery_sort_key(
            row,
            selected_provider,
            critical_delivery=critical_delivery,
        )
        return (
            base[0],
            self._reasoning_visibility_risk(row),
            *base[1:],
        )

    def rebalance_recovery_pool(
        self,
        optimization: Mapping[str, Any],
        candidate_bundle: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = super().rebalance_recovery_pool(
            optimization,
            candidate_bundle,
        )
        graph = dict(result.get("execution_graph") or {})
        metadata = dict(graph.get("metadata") or {})
        policy = dict(metadata.get("recovery_pool_policy") or {})
        policy["visible_delivery_risk_ranked_before_cost"] = True
        policy["reasoning_visibility_risk_policy"] = (
            "excluded-high-reasoning-without-explicit-control"
        )
        metadata["recovery_pool_policy"] = policy
        graph["metadata"] = metadata
        result["execution_graph"] = graph
        result["recovery_pool_policy"] = policy
        return result
