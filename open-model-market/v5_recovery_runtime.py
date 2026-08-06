"""Construct the execution-only production runtime.

Expert composition and recovery order are supplied by the signed candidate pool
and OR-Tools assignment before execution. Token and cost resources are governed
through prompts and audit telemetry rather than local rejection or truncation
gates. The installed production expert policy removes every Provider routing
field, leaving OpenRouter free to choose a Provider for each fixed model.
"""
from __future__ import annotations

from v5_production_expert_policy import install_production_expert_policy
from v5_soft_resource_governance import build_runtime
from v5_runtime import ProductionRuntime, RetryPolicy, RuntimeConfig


def build_production_runtime(config: RuntimeConfig) -> ProductionRuntime:
    """Build the production executor with unrestricted Provider routing."""
    retry_policy = RetryPolicy(
        retry_same_endpoint_categories=(),
        maximum_same_endpoint_retries_per_node=0,
    )
    runtime = build_runtime(config, retry_policy=retry_policy)
    return install_production_expert_policy(runtime)


__all__ = ["build_production_runtime"]
