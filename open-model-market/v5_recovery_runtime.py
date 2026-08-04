"""Construct the execution-only production runtime.

Expert composition and recovery order are supplied by GPT and validated before
execution. This module contains no scoring, ranking, optimization or reordering.
Token and cost resources are governed through prompts and audit telemetry rather
than local rejection or truncation gates. Expert requests use an explicit ZDR
and no-data-collection policy that matches the live endpoint eligibility view.
"""
from __future__ import annotations

from v5_production_expert_policy import install_production_expert_policy
from v5_soft_resource_governance import build_runtime
from v5_runtime import ProductionRuntime, RetryPolicy, RuntimeConfig


def build_production_runtime(config: RuntimeConfig) -> ProductionRuntime:
    """Build a provider-locked executor with no same-endpoint retry."""
    retry_policy = RetryPolicy(
        retry_same_endpoint_categories=(),
        maximum_same_endpoint_retries_per_node=0,
    )
    runtime = build_runtime(config, retry_policy=retry_policy)
    return install_production_expert_policy(runtime)


__all__ = ["build_production_runtime"]
