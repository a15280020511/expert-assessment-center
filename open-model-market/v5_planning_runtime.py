"""Explicit V5 planning policy composition without monkey patching."""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Mapping, Sequence

import v5_assignment_search_calibration as capability_calibration
import v5_candidate_diversity as candidate_diversity
import v5_company_diversity as company_diversity
import v5_cost_reliability_hardening as cost_policy
import v5_dynamic_configuration as dynamic_configuration
import v5_planner as base_planner
import v5_token_cost_policy as token_policy
import v5_truncation_budget_policy as truncation_policy
from execution_graph import GraphLimits


class PlannerPolicy:
    """Compose current-snapshot planning policies through direct calls."""

    def __init__(self, runtime_config: Any) -> None:
        self.config = runtime_config
        self.require_distinct_model_companies = (
            company_diversity.REQUIRE_DISTINCT_MODEL_COMPANIES
        )
        self.minimum_candidates_per_work = 2

    @staticmethod
    def _resource_works(
        resource_bundle: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        works: list[Mapping[str, Any]] = []
        for interpretation in resource_bundle.get("interpretations", []):
            if not isinstance(interpretation, Mapping):
                continue
            works.extend(
                row
                for row in interpretation.get("atomic_work", [])
                if isinstance(row, Mapping)
            )
        return works

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
        resource_works = self._resource_works(resource_bundle)
        reliability_floor = cost_policy.provider_reliability_floor(
            resource_works
        )
        endpoints = [
            dict(endpoint)
            for endpoint in market.get("endpoints", [])
            if isinstance(endpoint, Mapping)
            and float(endpoint.get("reliability", 0.0) or 0.0)
            >= reliability_floor
        ]
        if not endpoints:
            raise base_planner.V5PlanningError(
                "No current endpoint satisfies the production reliability floor."
            )
        result = dict(market)
        result["endpoints"] = endpoints
        result["endpoint_count"] = len(endpoints)
        result["model_company_count"] = len(
            {
                company_diversity.canonical_model_company(
                    str(endpoint.get("model_id") or "")
                )
                for endpoint in endpoints
            }
        )
        result["planning_policy"] = {
            "composition": "explicit-direct-call",
            "provider_reliability_floor": reliability_floor,
            "provider_reliability_policy": (
                "current-task-error-cost-and-importance-derived"
            ),
            "cost_estimation": "reasoning-and-truncation-aware-p95-usage",
            "candidate_configuration": "task-and-endpoint-dynamic",
            "pareto_pruning": "model-company-diversity-preserving",
            "budget_preflight_parity": "direct-risk-budgeted-optimizer-call",
            "require_distinct_model_companies": self.require_distinct_model_companies,
            "model_company_identity": "canonicalized-direct-model-author-prefix",
            "candidate_breadth_policy": (
                "minimum-assignment-slots-plus-current-adaptive-search-width"
            ),
            "candidate_breadth_revision": 4,
            "capability_pool_policy": (
                "evidence-backed-global-company-assignment-calibration"
            ),
            "capability_scores_modified": False,
            "task_demands_modified": False,
            "proxy_floor_lowered": False,
            "fixed_candidate_floor_used": False,
            "company_shortage_policy": "expand-pool-then-fail-closed",
            "cross_task_history_used": False,
        }
        return result

    @staticmethod
    def _p95_cost(
        endpoint: Mapping[str, Any],
        works: Sequence[Mapping[str, Any]],
        discount: float,
    ) -> float:
        prompt_tokens = 0
        completion_tokens = 0
        endpoint_max = int(endpoint.get("max_completion_tokens", 0) or 0)
        for work in works:
            context = work.get("context_requirements", {})
            context = context if isinstance(context, Mapping) else {}
            prompt_tokens += sum(
                max(0, int(context.get(key, 0) or 0))
                for key in (
                    "system_prompt_tokens",
                    "original_task_tokens",
                    "visible_upstream_tokens",
                )
            )
            completion_tokens += truncation_policy.estimated_completion_usage(
                work,
                endpoint_max,
            )
        prompt_tokens = int(math.ceil(prompt_tokens * discount))
        completion_tokens = int(math.ceil(completion_tokens * discount))
        prompt_price = float(
            endpoint.get("prompt_price_per_million", 0.0) or 0.0
        )
        completion_price = float(
            endpoint.get("completion_price_per_million", 0.0) or 0.0
        )
        base = (
            prompt_tokens * prompt_price
            + completion_tokens * completion_price
        ) / 1_000_000
        reliability = max(
            0.0,
            min(
                1.0,
                float(endpoint.get("reliability", 0.95) or 0.95),
            ),
        )
        reliability_floor = cost_policy.ABSOLUTE_MIN_PROVIDER_RELIABILITY
        reliability_reserve = 1.0 / max(reliability_floor, reliability)
        return round(base * reliability_reserve, 8)

    @staticmethod
    def candidate_factory(*args: Any, **kwargs: Any) -> Any:
        endpoint = (
            args[4]
            if len(args) > 4 and isinstance(args[4], Mapping)
            else {}
        )
        reliability = max(
            0.0,
            min(
                1.0,
                float(endpoint.get("reliability", 0.0) or 0.0),
            ),
        )
        works = (
            args[2]
            if len(args) > 2 and isinstance(args[2], Sequence)
            else ()
        )
        works = [work for work in works if isinstance(work, Mapping)]
        reliability_floor = cost_policy.provider_reliability_floor(works)
        if reliability < reliability_floor:
            return None
        candidate = base_planner._candidate_for(*args, **kwargs)
        if candidate is None:
            return None

        endpoint_max = int(
            endpoint.get("max_completion_tokens", 0) or 0
        )
        discount = max(
            0.1,
            float(kwargs.get("bundle_discount", 1.0)),
        )
        failure = max(
            candidate.failure_probability,
            1.0 - reliability,
        )
        failure = max(0.0, min(1.0, failure))
        p95_cost = PlannerPolicy._p95_cost(
            endpoint,
            works,
            discount,
        )
        risk_adjusted_cost = p95_cost / max(
            reliability_floor,
            1.0 - failure,
        )

        allowance = int(
            math.ceil(
                sum(
                    truncation_policy.completion_envelope(
                        work,
                        endpoint_max,
                    )
                    for work in works
                )
                * discount
            )
        )
        usage = int(
            math.ceil(
                sum(
                    truncation_policy.estimated_completion_usage(
                        work,
                        endpoint_max,
                    )
                    for work in works
                )
                * discount
            )
        )
        parameter_profile = dict(candidate.parameter_profile)
        parameter_profile.update(
            {
                "recommended_output_allowance_tokens": max(
                    1_024,
                    allowance,
                ),
                "estimated_completion_usage_tokens": max(1, usage),
                "cost_estimation_policy": (
                    "reasoning-inclusive-p95-usage-not-max-allowance-r8"
                ),
                "output_allowance_is_cost_assumption": False,
                "p95_token_usage_multiplier": (
                    token_policy.P95_TOKEN_USAGE_MULTIPLIER
                ),
                "structured_p95_token_usage_multiplier": (
                    token_policy.STRUCTURED_P95_TOKEN_USAGE_MULTIPLIER
                ),
                "truncation_pressure_policy": (
                    "reasoning-depth-contract-breadth-aware"
                ),
                "bundle_discount_applied_to_usage_estimate": round(
                    discount,
                    6,
                ),
                "provider_reliability_floor": reliability_floor,
                "provider_reliability_policy": (
                    "current-work-error-cost-and-importance-derived"
                ),
                "model_company": (
                    company_diversity.canonical_model_company(
                        candidate.model
                    )
                ),
                "cross_task_history_used": False,
            }
        )
        candidate = replace(
            candidate,
            failure_probability=round(failure, 6),
            estimated_cost=round(risk_adjusted_cost, 8),
            parameter_profile=parameter_profile,
        )

        role = dynamic_configuration._role_profile(works)
        request, decisions = dynamic_configuration._dynamic_parameters(
            works,
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
        adaptive_limit = max(2, int(maximum_per_group))
        if adaptive_limit > int(
            self.config.maximum_candidates_per_work
        ):
            raise base_planner.V5PlanningError(
                "Adaptive candidate request exceeds the configured emergency ceiling."
            )
        result = capability_calibration.generate_calibrated_candidate_graph(
            resource_bundle,
            market,
            maximum_per_group=adaptive_limit,
            candidate_factory=self.candidate_factory,
            pruner=(
                candidate_diversity.diversity_preserving_pareto_prune
            ),
        )
        result["model_company_policy"] = {
            "require_distinct_model_companies": (
                self.require_distinct_model_companies
            ),
            "candidate_pool_effective_per_work": adaptive_limit,
            "candidate_pool_emergency_ceiling_per_work": int(
                self.config.maximum_candidates_per_work
            ),
            "candidate_breadth_policy": (
                "minimum-assignment-slots-plus-current-adaptive-search-width"
            ),
            "candidate_breadth_revision": 4,
            "capability_pool_policy": (
                "evidence-backed-global-company-assignment-calibration"
            ),
            "capability_scores_modified": False,
            "task_demands_modified": False,
            "proxy_floor_lowered": False,
            "fixed_candidate_floor_used": False,
            "company_shortage_policy": "expand-pool-then-fail-closed",
        }
        return result

    def optimize_execution_graph(
        self,
        candidate_bundle: Mapping[str, Any],
        *,
        limits: GraphLimits,
        quality_tolerance_pct: float,
        solver_timeout_seconds: float,
    ) -> dict[str, Any]:
        return company_diversity.risk_budgeted_optimize_execution_graph(
            candidate_bundle,
            limits=limits,
            quality_tolerance_pct=quality_tolerance_pct,
            solver_timeout_seconds=solver_timeout_seconds,
            require_distinct_model_companies=(
                self.require_distinct_model_companies
            ),
        )
