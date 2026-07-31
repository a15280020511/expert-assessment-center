"""Explicit cross-endpoint recovery policy for the native V5 runtime.

Provider empty responses are treated as endpoint failures, not as evidence that
an identical request should be repeated against the same endpoint. Recovery
candidates are selected from the current run's frozen candidate graph using
provider diversity and cost-performance; no cross-task history is read.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from v5_planning_runtime import PlannerPolicy
from v5_runtime import (
    FailureCategory,
    ProductionRuntime,
    RetryPolicy,
    RuntimeConfig,
)


class CrossEndpointPlannerPolicy(PlannerPolicy):
    """Add cost-performance and provider-aware recovery rows to a solved graph."""

    @staticmethod
    def _provider(row: Mapping[str, Any]) -> str:
        value = str(row.get("provider_slug") or "").strip()
        if value:
            return value
        endpoint = str(row.get("provider_endpoint") or "")
        return endpoint.rsplit("@", 1)[-1] if "@" in endpoint else endpoint

    @staticmethod
    def _coverage(row: Mapping[str, Any]) -> tuple[str, ...]:
        values = row.get("coverage_keys")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return ()
        return tuple(sorted(str(value) for value in values))

    @classmethod
    def _recovery_sort_key(
        cls,
        row: Mapping[str, Any],
        selected_provider: str,
    ) -> tuple[Any, ...]:
        provider = cls._provider(row)
        cost = max(0.0, float(row.get("estimated_cost", 0.0) or 0.0))
        failure = max(0.0, min(1.0, float(row.get("failure_probability", 1.0) or 1.0)))
        quality = max(0.01, float(row.get("estimated_quality", 0.0) or 0.0))
        effective_cost_per_quality = cost * (1.0 + failure) / quality
        return (
            provider == selected_provider,
            effective_cost_per_quality,
            failure,
            cost,
            -quality,
            str(row.get("candidate_id") or ""),
        )

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
        selected_ids = {str(row.get("node_id") or "") for row in nodes}
        interpretation = str(
            graph.get("metadata", {}).get("interpretation_id")
            if isinstance(graph.get("metadata"), Mapping)
            else ""
        )
        maximum_rows = max(2, min(4, int(self.config.maximum_candidates_per_work)))
        recovery_pool: dict[str, list[dict[str, Any]]] = {}

        for selected in nodes:
            node_id = str(selected.get("node_id") or "")
            selected_model = str(selected.get("model") or "")
            selected_provider = self._provider(selected)
            coverage = tuple(
                sorted(
                    f"{work_id}#0"
                    for work_id in selected.get("assigned_work", [])
                )
            )
            alternatives = [
                row
                for row in candidates
                if str(row.get("candidate_id") or "") not in selected_ids
                and str(row.get("interpretation_id") or "") == interpretation
                and self._coverage(row) == coverage
                and str(row.get("model") or "") != selected_model
                and str(row.get("provider_endpoint") or "")
                != str(selected.get("provider_endpoint") or "")
            ]
            alternatives.sort(
                key=lambda row: self._recovery_sort_key(row, selected_provider)
            )
            recovery_pool[node_id] = alternatives[:maximum_rows]

        metadata = dict(graph.get("metadata") or {})
        metadata["recovery_pool"] = recovery_pool
        metadata["recovery_pool_policy"] = {
            "source": "current-run-frozen-candidate-graph",
            "same_endpoint_empty_response_retry": False,
            "provider_diversity_first": True,
            "ranking": [
                "different_provider",
                "effective_expected_cost_per_quality",
                "failure_probability",
                "estimated_cost",
                "estimated_quality",
            ],
            "maximum_candidates_per_selected_node": maximum_rows,
            "cross_task_history_used": False,
        }
        graph["metadata"] = metadata
        result["execution_graph"] = graph
        result["recovery_pool_policy"] = metadata["recovery_pool_policy"]
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
