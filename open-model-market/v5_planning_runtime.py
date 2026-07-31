"""Explicit V5 planning policy composition without monkey patching."""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Mapping, Sequence

import v5_capability_calibration as capability_calibration
import v5_candidate_diversity as candidate_diversity
import v5_cost_reliability_hardening as cost_policy
import v5_dynamic_configuration as dynamic_configuration
import v5_planner as base_planner
import v5_token_cost_policy as token_policy
import v5_value_optimizer as value_optimizer
from execution_graph import GraphLimits


class PlannerPolicy:
    """Compose current-snapshot planning policies through direct calls."""

    def __init__(self, runtime_config: Any) -> None:
        self.config = runtime_config

    def compile_market(
        self,
        ranked: Sequence[Any],
        resource_bundle: Mapping[str, Any],
        *,
        endpoint_payloads: Mapping[str, Mapping[str, Any]],
        ranking_limit: int,
        allow_synthetic_fixture: bool,
    ) -> dict[str, Any]:
        market = base_planner.compile_model_endpoint_market(
            ranked,
            resource_bundle,
            endpoint_payloads=endpoint_payloads,
            ranking_limit=ranking_limit,
            allow_synthetic_fixture=allow_synthetic_fixture,
        )
        endpoints = [
            dict(endpoint)
            for endpoint in market.get("endpoints", [])
            if isinstance(endpoint, Mapping)
            and float(endpoint.get("reliability", 0.0) or 0.0)
            >= cost_policy.MIN_PROVIDER_RELIABILITY
        ]
        if not endpoints:
            raise base_planner.V5PlanningError(
                "No current endpoint satisfies the production reliability floor."
            )
        result = dict(market)
        result["endpoints"] = endpoints
        result["endpoint_count"] = len(endpoints)
        result["planning_policy"] = {
            "composition": "explicit-direct-call",
            "provider_reliability_floor": cost_policy.MIN_PROVIDER_RELIABILITY,
            "cost_estimation": "reasoning-inclusive-p95-usage",
            "candidate_configuration": "task-and-endpoint-dynamic",
            "pareto_pruning": "model-diversity-preserving",
            "cross_task_history_used": False,
        }
        return result

    @staticmethod
    def candidate_factory(*args: Any, **kwargs: Any) -> Any:
        """Apply base, reliability, P95-cost, usage and dynamic configuration."""
        endpoint = args[4] if len(args) > 4 and isinstance(args[4], Mapping) else {}
        reliability = max(0.0, min(1.0, float(endpoint.get("reliability", 0.0) or 0.0)))
        if reliability < cost_policy.MIN_PROVIDER_RELIABILITY:
            return None
        candidate = base_planner._candidate_for(*args, **kwargs)
        if candidate is None:
            return None

        works = args[2] if len(args) > 2 and isinstance(args[2], Sequence) else ()
        works = [work for work in works if isinstance(work, Mapping)]
        endpoint_max = int(endpoint.get("max_completion_tokens", 0) or 0)
        discount = max(0.1, float(kwargs.get("bundle_discount", 1.0)))
        failure = max(
            candidate.failure_probability,
            1.0 - reliability,
        )
        failure = max(0.0, min(1.0, failure + (1.0 - reliability) * 0.50))
        p95_cost = token_policy.p95_usage_estimated_cost(
            endpoint,
            works,
            bundle_discount=discount,
        )
        risk_adjusted_cost = p95_cost * (1.0 + failure * 0.40)

        allowance = int(
            math.ceil(
                sum(cost_policy.completion_envelope(work, endpoint_max) for work in works)
                * discount
            )
        )
        usage = int(
            math.ceil(
                sum(token_policy.estimated_completion_usage(work, endpoint_max) for work in works)
                * discount
            )
        )
        parameter_profile = dict(candidate.parameter_profile)
        parameter_profile.update({
            "recommended_output_allowance_tokens": max(1_024, allowance),
            "estimated_completion_usage_tokens": max(1, usage),
            "cost_estimation_policy": "reasoning-inclusive-p95-usage-not-max-allowance-r8",
            "output_allowance_is_cost_assumption": False,
            "p95_token_usage_multiplier": token_policy.P95_TOKEN_USAGE_MULTIPLIER,
            "structured_p95_token_usage_multiplier": token_policy.STRUCTURED_P95_TOKEN_USAGE_MULTIPLIER,
            "bundle_discount_applied_to_usage_estimate": round(discount, 6),
            "provider_reliability_floor": cost_policy.MIN_PROVIDER_RELIABILITY,
            "cross_task_history_used": False,
        })
        candidate = replace(
            candidate,
            failure_probability=round(failure, 6),
            estimated_cost=round(risk_adjusted_cost, 8),
            parameter_profile=parameter_profile,
        )

        works_for_role = list(works)
        role = dynamic_configuration._role_profile(works_for_role)
        request, decisions = dynamic_configuration._dynamic_parameters(
            works_for_role,
            endpoint,
            candidate,
        )
        prompt_profile = dict(candidate.prompt_profile)
        prompt_profile.update(role)
        parameter_profile = dict(candidate.parameter_profile)
        parameter_profile["dynamic_parameter_decisions"] = decisions
        parameter_profile["fixed_request_parameter_profile_used"] = False
        return replace(
            candidate,
            prompt_profile=prompt_profile,
            parameter_profile=parameter_profile,
            request_config=request,
        )

    def generate_candidate_graph(
        self,
        resource_bundle: Mapping[str, Any],
        market: Mapping[str, Any],
        *,
        maximum_per_group: int,
    ) -> dict[str, Any]:
        return capability_calibration.generate_calibrated_candidate_graph(
            resource_bundle,
            market,
            maximum_per_group=maximum_per_group,
            candidate_factory=self.candidate_factory,
            pruner=candidate_diversity.diversity_preserving_pareto_prune,
        )

    def optimize_execution_graph(
        self,
        candidate_bundle: Mapping[str, Any],
        *,
        limits: GraphLimits,
        quality_tolerance_pct: float,
        solver_timeout_seconds: float,
    ) -> dict[str, Any]:
        return value_optimizer.optimize_execution_graph(
            candidate_bundle,
            limits=limits,
            quality_tolerance_pct=quality_tolerance_pct,
            solver_timeout_seconds=solver_timeout_seconds,
        )
