"""Explicit cross-endpoint recovery runtime with constitutional quality gates."""
from __future__ import annotations

from v5_constitutional_runtime import build_runtime
from v5_operational_resilience import OperationalResiliencePlannerPolicy
from v5_runtime import ProductionRuntime, RetryPolicy, RuntimeConfig

CrossEndpointPlannerPolicy = OperationalResiliencePlannerPolicy


def build_production_runtime(config: RuntimeConfig) -> ProductionRuntime:
    """Construct one explicit runtime with company-safe contract recovery."""
    retry_policy = RetryPolicy(
        retry_same_endpoint_categories=(),
        maximum_same_endpoint_retries_per_node=0,
    )
    return build_runtime(
        config,
        planner_policy=CrossEndpointPlannerPolicy(config),
        retry_policy=retry_policy,
    )


__all__ = ["CrossEndpointPlannerPolicy", "build_production_runtime"]
