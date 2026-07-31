"""Explicit cross-endpoint recovery policy for the native V5 runtime.

Provider empty responses are treated as endpoint failures, not as evidence that
an identical request should be repeated against the same endpoint. Recovery
candidates are selected from the current run's frozen candidate graph using
provider diversity and cost-performance. Selected and recovery companies remain
globally disjoint for the whole task; no cross-task history is read.
"""
from __future__ import annotations

from typing import Any, Mapping

from v5_model_company_policy import build_disjoint_recovery_pool
from v5_planner import V5PlanningError
from v5_planning_runtime import PlannerPolicy
from v5_runtime import (
    FailureCategory,
    ProductionRuntime,
    RetryPolicy,
    RuntimeConfig,
)


class CrossEndpointPlannerPolicy(PlannerPolicy):
    """Add company-safe, cost-performance recovery rows to a solved graph."""

    def rebalance_recovery_pool(
        self,
        optimization: Mapping[str, Any],
        candidate_bundle: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = dict(optimization)
        graph = dict(result.get("execution_graph") or {})
        nodes = [
            dict(row)
            for row in graph.get("nodes", [])
            if isinstance(row, Mapping)
        ]
        candidates = [
            dict(row)
            for row in candidate_bundle.get("candidates", [])
            if isinstance(row, Mapping)
        ]
        candidate_by_id = {
            str(row.get("candidate_id") or ""): row
            for row in candidates
            if str(row.get("candidate_id") or "")
        }
        selected_rows: list[dict[str, Any]] = []
        for node in nodes:
            node_id = str(node.get("node_id") or "")
            candidate = candidate_by_id.get(node_id)
            if candidate is None:
                raise V5PlanningError(
                    f"Selected node {node_id!r} is missing from the frozen candidate graph."
                )
            selected_rows.append(candidate)

        metadata = dict(graph.get("metadata") or {})
        interpretation = str(metadata.get("interpretation_id") or "")
        if not interpretation:
            raise V5PlanningError("Selected graph is missing interpretation_id metadata.")
        maximum_rows = max(
            0,
            min(4, int(self.config.maximum_candidates_per_work)),
        )
        recovery_pool, recovery_policy = build_disjoint_recovery_pool(
            selected_rows,
            candidates,
            interpretation_id=interpretation,
            maximum_rows_per_node=maximum_rows,
        )
        recovery_policy["ranking"] = [
            "different_provider",
            "effective_expected_cost_per_quality",
            "failure_probability",
            "estimated_cost",
            "estimated_quality",
        ]
        recovery_policy["exact_coverage_keys_required"] = True

        metadata["recovery_pool"] = recovery_pool
        metadata["recovery_pool_policy"] = recovery_policy
        graph["metadata"] = metadata
        result["execution_graph"] = graph
        result["recovery_pool_policy"] = recovery_policy
        return result

    def optimize_execution_graph(
        self,
        candidate_bundle: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        optimization = super().optimize_execution_graph(
            candidate_bundle,
            **kwargs,
        )
        return self.rebalance_recovery_pool(optimization, candidate_bundle)


def build_production_runtime(config: RuntimeConfig) -> ProductionRuntime:
    """Construct one explicit runtime with cross-endpoint empty-response recovery."""
    retry_policy = RetryPolicy(
        retry_same_endpoint_categories=(
            FailureCategory.PROVIDER_RATE_LIMITED,
            FailureCategory.PROVIDER_TIMEOUT,
        ),
        maximum_same_endpoint_retries_per_node=1,
    )
    return ProductionRuntime(
        config,
        retry_policy=retry_policy,
        planner_policy=CrossEndpointPlannerPolicy(config),
    )
