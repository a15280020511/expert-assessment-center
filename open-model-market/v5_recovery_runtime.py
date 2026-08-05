"""Compatibility entrypoint for the governed V6 execution runtime.

The filename remains temporarily stable for reused imports, but all active
behavior is V6: no GPT/Claude planning, no same-endpoint retry, exact provider
locks, no tools, ZDR for every expert, and only preassigned recovery models.
"""
from __future__ import annotations

from v5_runtime import ProductionRuntime, RetryPolicy, RuntimeConfig
from v6_expert_runtime_policy import install_v6_expert_policy
from v6_resource_runtime import build_runtime


def build_production_runtime(config: RuntimeConfig) -> ProductionRuntime:
    retry_policy = RetryPolicy(
        retry_same_endpoint_categories=(),
        maximum_same_endpoint_retries_per_node=0,
    )
    runtime = build_runtime(config, retry_policy=retry_policy)
    return install_v6_expert_policy(runtime)


__all__ = ["build_production_runtime"]
