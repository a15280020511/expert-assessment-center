"""Stable public facade for company-safe cross-endpoint recovery planning.

The implementation module keeps the general planner. This facade ranks scarce
recovery calls by visible delivery and output-contract serviceability before
cost, while preserving provider and company isolation.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_cross_endpoint_planner_impl as _impl

for _export_name in dir(_impl):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_impl, _export_name)


class CrossEndpointPlannerPolicy(_impl.CrossEndpointPlannerPolicy):
    """Prefer recovery candidates capable of visible, contract-complete output."""

    @staticmethod
    def _supported_parameters(row: Mapping[str, Any]) -> set[str]:
        profile = row.get("parameter_profile")
        profile = profile if isinstance(profile, Mapping) else {}
        supported_raw = profile.get("supported_parameters")
        if not isinstance(supported_raw, Sequence) or isinstance(
            supported_raw,
            (str, bytes),
        ):
            return set()
        return {
            str(value).strip().casefold()
            for value in supported_raw
            if str(value).strip()
        }

    @classmethod
    def _reasoning_visibility_risk(cls, row: Mapping[str, Any]) -> int:
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
        supported = cls._supported_parameters(row)
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

    @classmethod
    def _contract_delivery_risk(cls, row: Mapping[str, Any]) -> int:
        """Rank model/endpoint ability to obey a broad deterministic contract."""
        contract = row.get("output_contract")
        contract = contract if isinstance(contract, Mapping) else {}
        required = contract.get("required_fields")
        required_count = (
            len(required)
            if isinstance(required, Sequence)
            and not isinstance(required, (str, bytes))
            else 0
        )
        if required_count <= 1:
            return 0

        capabilities = row.get("professional_capabilities")
        capabilities = capabilities if isinstance(capabilities, Mapping) else {}
        delivery = max(0.0, float(capabilities.get("delivery", 0.0) or 0.0))
        structured = max(
            0.0,
            float(capabilities.get("structured_output", 0.0) or 0.0),
        )
        profile = row.get("parameter_profile")
        profile = profile if isinstance(profile, Mapping) else {}
        dynamic = profile.get("dynamic_parameter_decisions")
        dynamic = dynamic if isinstance(dynamic, Mapping) else {}
        supported = cls._supported_parameters(row)
        explicit_structured_delivery = bool(
            dynamic.get("structured_delivery")
            or profile.get("structured_output_contract_sent")
        )
        strong_contract_control = bool(
            "structured_outputs" in supported
            or explicit_structured_delivery
        )
        basic_contract_control = bool(
            strong_contract_control or "response_format" in supported
        )

        if strong_contract_control and structured >= 0.85 and delivery >= 0.50:
            return 0
        if basic_contract_control and structured >= 0.85 and delivery >= 0.48:
            return 1
        return 2

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
            self._contract_delivery_risk(row),
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
        policy["contract_delivery_risk_ranked_before_cost"] = True
        policy["contract_delivery_risk_policy"] = (
            "required-field-breadth-plus-structured-control-plus-delivery"
        )
        metadata["recovery_pool_policy"] = policy
        graph["metadata"] = metadata
        result["execution_graph"] = graph
        result["recovery_pool_policy"] = policy
        return result
