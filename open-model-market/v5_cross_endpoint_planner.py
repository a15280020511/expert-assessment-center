"""Company-safe cross-endpoint recovery planning from the current run only."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from v5_model_company import candidate_company
from v5_planner import V5PlanningError
from v5_planning_runtime import PlannerPolicy


class CrossEndpointPlannerPolicy(PlannerPolicy):
    """Add company-safe, provider-aware recovery rows to a solved graph."""

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
            candidate_company(row),
            str(row.get("candidate_id") or ""),
        )

    def rebalance_recovery_pool(
        self,
        optimization: Mapping[str, Any],
        candidate_bundle: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = dict(optimization)
        graph = dict(result.get("execution_graph") or {})
        nodes = [dict(row) for row in graph.get("nodes", []) if isinstance(row, Mapping)]
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
            selected = candidate_by_id.get(node_id)
            if selected is None:
                raise V5PlanningError(
                    "Selected execution node is absent from the frozen candidate graph: "
                    f"{node_id!r}."
                )
            selected_rows.append(selected)

        selected_ids = {str(row.get("candidate_id") or "") for row in selected_rows}
        selected_companies = {candidate_company(row) for row in selected_rows}
        metadata = graph.get("metadata", {})
        interpretation = str(metadata.get("interpretation_id") if isinstance(metadata, Mapping) else "")
        if not interpretation:
            raise V5PlanningError("Selected graph is missing interpretation_id metadata.")

        maximum_rows = 0 if int(self.config.recovery_call_limit) <= 0 else max(
            1,
            min(int(self.config.recovery_call_limit), int(self.config.maximum_candidates_per_work)),
        )
        eligible_by_node: dict[str, list[dict[str, Any]]] = {}
        for selected in selected_rows:
            node_id = str(selected.get("candidate_id") or "")
            selected_model = str(selected.get("model") or "")
            selected_provider = self._provider(selected)
            coverage = self._coverage(selected)
            if not coverage:
                raise V5PlanningError(
                    f"Selected candidate is missing exact coverage keys: {node_id!r}."
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
                and candidate_company(row) not in selected_companies
            ]
            alternatives.sort(key=lambda row: self._recovery_sort_key(row, selected_provider))
            unique_by_company: list[dict[str, Any]] = []
            seen_companies: set[str] = set()
            for row in alternatives:
                company = candidate_company(row)
                if company in seen_companies:
                    continue
                payload = dict(row)
                payload["model_company"] = company
                unique_by_company.append(payload)
                seen_companies.add(company)
            eligible_by_node[node_id] = unique_by_company

        recovery_pool = {
            str(row.get("candidate_id") or ""): [] for row in selected_rows
        }
        reserved_companies: set[str] = set()
        ordered_node_ids = [str(row.get("candidate_id") or "") for row in selected_rows]
        for _ in range(maximum_rows):
            progress = False
            for node_id in ordered_node_ids:
                rows = eligible_by_node.get(node_id, [])
                chosen = next(
                    (row for row in rows if candidate_company(row) not in reserved_companies),
                    None,
                )
                if chosen is None:
                    continue
                recovery_pool[node_id].append(chosen)
                reserved_companies.add(candidate_company(chosen))
                rows.remove(chosen)
                progress = True
            if not progress:
                break

        metadata = dict(graph.get("metadata") or {})
        metadata["recovery_pool"] = recovery_pool
        metadata["recovery_pool_policy"] = {
            "source": "current-run-frozen-candidate-graph",
            "same_endpoint_empty_response_retry": False,
            "different_model_company_required": True,
            "selected_companies_excluded": sorted(selected_companies),
            "recovery_companies_globally_unique": True,
            "reserved_recovery_companies": sorted(reserved_companies),
            "maximum_candidates_per_selected_node": maximum_rows,
            "maximum_recovery_calls": int(self.config.recovery_call_limit),
            "candidate_options_do_not_reserve_paid_calls": True,
            "actual_recovery_calls_remain_budget_limited": True,
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
        optimization = super().optimize_execution_graph(candidate_bundle, **kwargs)
        return self.rebalance_recovery_pool(optimization, candidate_bundle)
