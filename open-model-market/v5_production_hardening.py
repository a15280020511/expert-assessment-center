"""Compatibility surface for callers migrating to the explicit V5 runtime.

Production, dry-run and tests construct ``ProductionRuntime`` explicitly.
Calling ``install`` is intentionally a no-op and never mutates global symbols.
Behavioral callers may use ``resilient_execute_v5_graph``; it creates an
isolated runtime object for that invocation rather than patching modules.
"""
from __future__ import annotations

from typing import Any, Mapping

import v5_cost_reliability_hardening as cost_reliability
import v5_token_cost_policy as token_cost
from execution_graph import GraphLimits
from v5_runtime import MIN_DEGRADED_WORK_COVERAGE, ProductionRuntime, RuntimeConfig

MIN_PROVIDER_RELIABILITY = cost_reliability.MIN_PROVIDER_RELIABILITY
COST_UNCERTAINTY_MULTIPLIER = cost_reliability.COST_UNCERTAINTY_MULTIPLIER
_ORIGINAL_ESTIMATED_COST = cost_reliability._ORIGINAL_ESTIMATED_COST
conservative_estimated_cost = token_cost.p95_usage_estimated_cost
hardened_candidate_for = token_cost.usage_audited_candidate_for
hardened_build_node_payload = cost_reliability.hardened_build_node_payload
robust_extract_answer = cost_reliability.robust_extract_answer


def resilient_execute_v5_graph(
    graph: Any,
    run: Any,
    original_task: str,
    *,
    call_fn: Any | None = None,
    output_dir: Any | None = None,
    limits: GraphLimits | None = None,
) -> Mapping[str, Any]:
    """Execute through one isolated native runtime without global mutation."""
    limits = limits or GraphLimits()
    total = max(1, int(limits.max_model_calls))
    recovery = min(
        max(0, total - 1),
        max(0, int(limits.max_retries), int(limits.max_replacements)),
    )
    runtime = ProductionRuntime(RuntimeConfig(
        total_call_limit=total,
        recovery_call_limit=recovery,
        cost_anomaly_usd=limits.max_budget_usd,
        quality_tier="value",
        tools_allowed=False,
        live_catalog_required=False,
        provider_lock_required=True,
        max_provider_failures=max(2, int(limits.max_provider_failures)),
    ))
    return runtime.execute_graph(
        graph,
        run,
        original_task,
        call_fn=call_fn,
        output_dir=output_dir,
        limits=limits,
    )


def install() -> None:
    """Compatibility no-op; retained temporarily for stale external imports."""
    return None
