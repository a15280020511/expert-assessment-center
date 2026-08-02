"""Construct the execution-only production runtime.

Expert composition and recovery order are supplied by GPT and validated before
execution. This module contains no scoring, ranking, optimization or reordering.
"""
from __future__ import annotations

from v5_constitutional_runtime import build_runtime
from v5_runtime import ProductionRuntime, RetryPolicy, RuntimeConfig


def build_production_runtime(config: RuntimeConfig) -> ProductionRuntime:
    """Build a provider-locked executor with no same-endpoint retry."""
    retry_policy = RetryPolicy(
        retry_same_endpoint_categories=(),
        maximum_same_endpoint_retries_per_node=0,
    )
    return build_runtime(
        config,
        planner_policy="gpt-direct-no-local-planner",
        retry_policy=retry_policy,
    )


__all__ = ["build_production_runtime"]
