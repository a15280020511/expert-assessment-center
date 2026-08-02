"""Company-safe cross-endpoint recovery planning from the current run only."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from v5_model_company import candidate_company
from v5_planner import V5PlanningError
from v5_planning_runtime import PlannerPolicy


class CrossEndpointPlannerPolicy(PlannerPolicy):
    """Add structurally sufficient selection and company-safe recovery rows."""

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
        if not isinstance(values, Sequence) or isinstance(
            values,
            (str, bytes),
        ):
            return ()
        return tuple(sorted(str(value) for value in values))

    @staticmethod
    def _functions(row: Mapping[str, Any]) -> set[str]:
        values = row.get("functions")
        if not isinstance(values, Sequence) or isinstance(
            values,
            (str, bytes),
        ):
            return set()
        return {str(value) for value in values}

    @classmethod
    def _is_synthesis(cls, row: Mapping[str, Any]) -> bool:
        return "synthesis" in cls._functions(row)

    @staticmethod
    def _delivery_utility(row: Mapping[str, Any]) -> float:
        quality = max(
            0.01,
            float(row.get("estimated_quality", 0.0) or 0.0),
        )
        failure = max(
            0.0,
            min(
                1.0,
                float(row.get("failure_probability", 1.0) or 1.0),
            ),
        )
        uncertainty = max(
            0.0,
            min(
                1.0,
                float(row.get("quality_uncertainty", 1.0) or 1.0),
            ),
        )
        return max(0.0, quality * (1.0 - failure) - 0.10 * uncertainty)

    @staticmethod
    def _assigned_work(row: Mapping[str, Any]) -> tuple[str, ...]:
        values = row.get("assigned_work")
        if not isinstance(values, Sequence) or isinstance(
            values,
            (str, bytes),
        ):
            return ()
        return tuple(str(value) for value in values if str(value))

    @classmethod
    def _critical_work_leverage(
        cls,
        candidate_bundle: Mapping[str, Any],
    ) -> dict[tuple[str, str], int]:
        """Return task-derived aggregation leverage for final/synthesis work."""
        candidates = [
            row
            for row in candidate_bundle.get("candidates", [])
            if isinstance(row, Mapping)
        ]
        interpretations = candidate_bundle.get("interpretations", {})
        if not isinstance(interpretations, Mapping):
            return {}

        synthesis_work: dict[str, set[str]] = {}
        for row in candidates:
            if not cls._is_synthesis(row):
                continue
            interpretation_id = str(row.get("interpretation_id") or "")
            synthesis_work.setdefault(interpretation_id, set()).update(
                cls._assigned_work(row)
            )

        result: dict[tuple[str, str], int] = {}
        for raw_interpretation_id, raw_meta in interpretations.items():
            interpretation_id = str(raw_interpretation_id)
            meta = raw_meta if isinstance(raw_meta, Mapping) else {}
            work_ids = {
                str(value)
                for value in meta.get("work_ids", [])
                if str(value)
            }
            incoming: dict[str, set[str]] = {
                work_id: set() for work_id in work_ids
            }
            outgoing: dict[str, set[str]] = {
                work_id: set() for work_id in work_ids
            }
            for raw_edge in meta.get("atomic_edges", []):
                if not isinstance(raw_edge, Mapping):
                    continue
                source = str(raw_edge.get("source") or "")
                target = str(raw_edge.get("target") or "")
                if not source or not target:
                    continue
                work_ids.update((source, target))
                incoming.setdefault(target, set()).add(source)
                incoming.setdefault(source, set())
                outgoing.setdefault(source, set()).add(target)
                outgoing.setdefault(target, set())

            critical = {
                work_id
                for work_id in work_ids
                if incoming.get(work_id) and not outgoing.get(work_id)
            }
            critical.update(synthesis_work.get(interpretation_id, set()))
            for work_id in critical:
                ancestors: set[str] = set()
                pending = list(incoming.get(work_id, set()))
                while pending:
                    ancestor = pending.pop()
                    if ancestor in ancestors:
                        continue
                    ancestors.add(ancestor)
                    pending.extend(incoming.get(ancestor, set()))
                result[(interpretation_id, work_id)] = 1 + len(ancestors)
        return result

    def _filter_critical_delivery_candidates(
        self,
        candidate_bundle: Mapping[str, Any],
        *,
        max_budget_usd: float | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Keep a dynamic structurally sufficient set for final delivery work."""
        candidates = [
            dict(row)
            for row in candidate_bundle.get("candidates", [])
            if isinstance(row, Mapping)
        ]
        leverage = self._critical_work_leverage(candidate_bundle)
        evidence: dict[str, Any] = {
            "policy": "task-graph-structural-delivery-sufficiency",
            "candidate_count_before": len(candidates),
            "candidate_count_after": len(candidates),
            "removed_candidate_count": 0,
            "critical_work": [],
            "fallback_used": False,
        }
        if not candidates or not leverage:
            return dict(candidate_bundle), evidence

        budget = (
            None
            if max_budget_usd is None
            else max(0.0, float(max_budget_usd))
        )
        floors: dict[tuple[str, str], float] = {}
        for key, structural_leverage in sorted(leverage.items()):
            interpretation_id, work_id = key
            rows = [
                row
                for row in candidates
                if str(row.get("interpretation_id") or "")
                == interpretation_id
                and work_id in self._assigned_work(row)
            ]
            budget_rows = [
                row
                for row in rows
                if budget is None
                or float(row.get("estimated_cost", 0.0) or 0.0) <= budget
            ]
            reference_rows = budget_rows or rows
            if not reference_rows:
                continue
            maximum_utility = max(
                self._delivery_utility(row) for row in reference_rows
            )
            floor = maximum_utility * (
                structural_leverage / (structural_leverage + 1.0)
            )
            floors[key] = floor
            evidence["critical_work"].append(
                {
                    "interpretation_id": interpretation_id,
                    "work_id": work_id,
                    "structural_leverage": structural_leverage,
                    "maximum_budget_feasible_delivery_utility": round(
                        maximum_utility,
                        9,
                    ),
                    "minimum_delivery_utility": round(floor, 9),
                }
            )

        if not floors:
            return dict(candidate_bundle), evidence

        kept: list[dict[str, Any]] = []
        removed: list[str] = []
        for row in candidates:
            interpretation_id = str(row.get("interpretation_id") or "")
            applicable = [
                floors[(interpretation_id, work_id)]
                for work_id in self._assigned_work(row)
                if (interpretation_id, work_id) in floors
            ]
            if applicable and self._delivery_utility(row) + 1e-12 < max(applicable):
                removed.append(str(row.get("candidate_id") or ""))
                continue
            kept.append(row)

        filtered = dict(candidate_bundle)
        filtered["candidates"] = kept
        evidence["candidate_count_after"] = len(kept)
        evidence["removed_candidate_count"] = len(removed)
        evidence["removed_candidate_ids"] = removed
        return filtered, evidence

    def _recovery_cost_risk_multiplier(
        self,
        row: Mapping[str, Any],
    ) -> float:
        profile = row.get("parameter_profile")
        profile = profile if isinstance(profile, Mapping) else {}
        p95 = max(1.0, float(profile.get("p95_token_usage_multiplier", 1.0) or 1.0))
        structured = max(
            1.0,
            float(profile.get("structured_p95_token_usage_multiplier", 1.0) or 1.0),
        )
        uncertainty = max(
            0.0,
            min(1.0, float(row.get("quality_uncertainty", 0.0) or 0.0)),
        )
        serviceability = profile.get("operational_serviceability")
        serviceability = serviceability if isinstance(serviceability, Mapping) else {}
        try:
            deadline_ratio = max(
                0.0,
                float(serviceability.get("estimated_deadline_ratio") or 0.0),
            )
        except (TypeError, ValueError):
            deadline_ratio = 0.0
        deadline_multiplier = 1.0 + max(0.0, deadline_ratio - 0.50)
        return max(
            1.0,
            float(self.config.cost_risk_multiplier),
            p95 * structured,
            1.0 + uncertainty,
            deadline_multiplier,
        )

    def _risk_adjusted_recovery_cost(
        self,
        row: Mapping[str, Any],
    ) -> float:
        cost = max(0.0, float(row.get("estimated_cost", 0.0) or 0.0))
        return cost * self._recovery_cost_risk_multiplier(row)

    def _recovery_sort_key(
        self,
        row: Mapping[str, Any],
        selected_provider: str,
        *,
        critical_delivery: bool,
    ) -> tuple[Any, ...]:
        provider = self._provider(row)
        cost = max(
            0.0,
            float(row.get("estimated_cost", 0.0) or 0.0),
        )
        failure = max(
            0.0,
            min(
                1.0,
                float(row.get("failure_probability", 1.0) or 1.0),
            ),
        )
        quality = max(
            0.01,
            float(row.get("estimated_quality", 0.0) or 0.0),
        )
        uncertainty = max(
            0.0,
            min(
                1.0,
                float(row.get("quality_uncertainty", 1.0) or 1.0),
            ),
        )
        delivery_utility = max(0.01, self._delivery_utility(row))
        risk_adjusted_cost = self._risk_adjusted_recovery_cost(row)
        effective_cost_per_delivery = risk_adjusted_cost / delivery_utility
        profile = row.get("parameter_profile")
        profile = profile if isinstance(profile, Mapping) else {}
        serviceability = profile.get("operational_serviceability")
        serviceability = serviceability if isinstance(serviceability, Mapping) else {}
        try:
            deadline_ratio = max(
                0.0,
                float(serviceability.get("estimated_deadline_ratio") or 0.0),
            )
        except (TypeError, ValueError):
            deadline_ratio = 0.0
        if critical_delivery:
            return (
                provider == selected_provider,
                effective_cost_per_delivery,
                failure,
                uncertainty,
                deadline_ratio,
                cost,
                -quality,
                candidate_company(row),
                str(row.get("candidate_id") or ""),
            )
        return (
            provider == selected_provider,
            effective_cost_per_delivery,
            failure,
            deadline_ratio,
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
            selected = candidate_by_id.get(node_id)
            if selected is None:
                raise V5PlanningError(
                    "Selected execution node is absent from the frozen "
                    f"candidate graph: {node_id!r}."
                )
            selected_rows.append(selected)

        selected_ids = {
            str(row.get("candidate_id") or "")
            for row in selected_rows
        }
        selected_companies = {
            candidate_company(row) for row in selected_rows
        }
        metadata = graph.get("metadata", {})
        interpretation = str(
            metadata.get("interpretation_id")
            if isinstance(metadata, Mapping)
            else ""
        )
        if not interpretation:
            raise V5PlanningError(
                "Selected graph is missing interpretation_id metadata."
            )

        maximum_rows = (
            0
            if int(self.config.recovery_call_limit) <= 0
            else max(
                2,
                min(
                    int(self.config.maximum_candidates_per_work),
                    max(2, len(candidates)),
                ),
            )
        )
        final_node_ids = {
            str(value) for value in graph.get("final_nodes", []) if str(value)
        }
        critical_node_ids = {
            str(row.get("candidate_id") or "")
            for row in selected_rows
            if str(row.get("candidate_id") or "") in final_node_ids
            or self._is_synthesis(row)
        }
        selected_initial_cost = float(
            result.get("selected_initial_cost_usd")
            or sum(
                max(0.0, float(row.get("estimated_cost", 0.0) or 0.0))
                for row in selected_rows
            )
        )
        cost_cap = max(0.0, float(self.config.cost_anomaly_usd or 0.0))
        remaining_recovery_budget = (
            None
            if cost_cap <= 0.0
            else max(0.0, cost_cap - selected_initial_cost)
        )

        eligible_by_node: dict[str, list[dict[str, Any]]] = {}
        budget_excluded_by_node: dict[str, int] = {}
        absolute_cost_cap_excluded_by_node: dict[str, int] = {}
        estimated_above_planning_budget_by_node: dict[str, int] = {}
        for selected in selected_rows:
            node_id = str(selected.get("candidate_id") or "")
            selected_model = str(selected.get("model") or "")
            selected_provider = self._provider(selected)
            coverage = self._coverage(selected)
            if not coverage:
                raise V5PlanningError(
                    "Selected candidate is missing exact coverage keys: "
                    f"{node_id!r}."
                )
            alternatives = [
                row
                for row in candidates
                if str(row.get("candidate_id") or "") not in selected_ids
                and str(row.get("interpretation_id") or "")
                == interpretation
                and self._coverage(row) == coverage
                and str(row.get("model") or "") != selected_model
                and str(row.get("provider_endpoint") or "")
                != str(selected.get("provider_endpoint") or "")
                and candidate_company(row) not in selected_companies
            ]
            absolute_cost_cap_excluded_by_node[node_id] = (
                0
                if cost_cap <= 0.0
                else sum(
                    1
                    for row in alternatives
                    if max(
                        0.0,
                        float(row.get("estimated_cost", 0.0) or 0.0),
                    )
                    > cost_cap + 1e-12
                )
            )
            if cost_cap > 0.0:
                alternatives = [
                    row
                    for row in alternatives
                    if max(
                        0.0,
                        float(row.get("estimated_cost", 0.0) or 0.0),
                    )
                    <= cost_cap + 1e-12
                ]
            estimated_above_planning_budget_by_node[node_id] = (
                0
                if remaining_recovery_budget is None
                else sum(
                    1
                    for row in alternatives
                    if max(
                        0.0,
                        float(row.get("estimated_cost", 0.0) or 0.0),
                    )
                    > remaining_recovery_budget + 1e-12
                )
            )
            budget_excluded_by_node[node_id] = (
                0
                if remaining_recovery_budget is None
                else sum(
                    1
                    for row in alternatives
                    if self._risk_adjusted_recovery_cost(row)
                    > remaining_recovery_budget + 1e-12
                )
            )
            if remaining_recovery_budget is not None:
                alternatives = [
                    row
                    for row in alternatives
                    if self._risk_adjusted_recovery_cost(row)
                    <= remaining_recovery_budget + 1e-12
                ]
            critical_delivery = node_id in critical_node_ids
            alternatives.sort(
                key=lambda row: self._recovery_sort_key(
                    row,
                    selected_provider,
                    critical_delivery=critical_delivery,
                )
            )
            unique_by_company: list[dict[str, Any]] = []
            seen_companies: set[str] = set()
            for row in alternatives:
                company = candidate_company(row)
                if company in seen_companies:
                    continue
                payload = dict(row)
                payload["model_company"] = company
                payload["recovery_delivery_utility"] = round(
                    self._delivery_utility(row),
                    9,
                )
                estimated_cost = max(
                    0.0,
                    float(row.get("estimated_cost", 0.0) or 0.0),
                )
                risk_multiplier = self._recovery_cost_risk_multiplier(row)
                risk_adjusted_cost = estimated_cost * risk_multiplier
                payload["planning_budget_advisory_only"] = False
                payload["absolute_cost_cap_feasible"] = True
                payload["estimated_cost_above_planning_remaining_budget"] = False
                payload["recovery_cost_risk_multiplier"] = round(
                    risk_multiplier,
                    8,
                )
                payload["recovery_risk_adjusted_cost_usd"] = round(
                    risk_adjusted_cost,
                    8,
                )
                parameter_profile = payload.get("parameter_profile")
                parameter_profile = (
                    dict(parameter_profile)
                    if isinstance(parameter_profile, Mapping)
                    else {}
                )
                parameter_profile["recovery_cost_risk_multiplier"] = round(
                    risk_multiplier,
                    8,
                )
                parameter_profile["recovery_risk_adjusted_cost_usd"] = round(
                    risk_adjusted_cost,
                    8,
                )
                payload["parameter_profile"] = parameter_profile
                unique_by_company.append(payload)
                seen_companies.add(company)
            eligible_by_node[node_id] = unique_by_company

        recovery_pool = {
            str(row.get("candidate_id") or ""): []
            for row in selected_rows
        }
        reserved_companies: set[str] = set()
        selected_node_ids = [
            str(row.get("candidate_id") or "")
            for row in selected_rows
        ]
        ordered_node_ids = [
            node_id
            for node_id in selected_node_ids
            if node_id in critical_node_ids
        ] + [
            node_id
            for node_id in selected_node_ids
            if node_id not in critical_node_ids
        ]
        for _ in range(maximum_rows):
            progress = False
            for node_id in ordered_node_ids:
                rows = eligible_by_node.get(node_id, [])
                chosen = next(
                    (
                        row
                        for row in rows
                        if candidate_company(row)
                        not in reserved_companies
                    ),
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

        total_recovery_options = sum(
            len(rows) for rows in recovery_pool.values()
        )
        if int(self.config.recovery_call_limit) > 0 and total_recovery_options <= 0:
            raise V5PlanningError(
                "Recovery reserve is not executable under the absolute cost "
                "anomaly guard."
            )

        metadata = dict(graph.get("metadata") or {})
        metadata["recovery_pool"] = recovery_pool
        metadata["recovery_pool_policy"] = {
            "source": "current-run-frozen-candidate-graph",
            "provider_diversity_first": True,
            "same_endpoint_empty_response_retry": False,
            "different_model_company_required": True,
            "selected_companies_excluded": sorted(selected_companies),
            "recovery_companies_globally_unique": True,
            "reserved_recovery_companies": sorted(reserved_companies),
            "maximum_candidates_per_selected_node": maximum_rows,
            "maximum_recovery_calls": int(
                self.config.recovery_call_limit
            ),
            "candidate_options_do_not_reserve_paid_calls": True,
            "actual_recovery_calls_remain_budget_limited": True,
            "critical_delivery_cost_effectiveness_after_utility_floor": True,
            "critical_nodes_allocated_first": True,
            "critical_node_ids": sorted(critical_node_ids),
            "selected_initial_cost_usd": round(selected_initial_cost, 8),
            "cost_cap_usd": (None if cost_cap <= 0.0 else cost_cap),
            "remaining_recovery_budget_usd": (
                None
                if remaining_recovery_budget is None
                else round(remaining_recovery_budget, 8)
            ),
            "budget_excluded_by_node": budget_excluded_by_node,
            "absolute_cost_cap_excluded_by_node": (
                absolute_cost_cap_excluded_by_node
            ),
            "absolute_cost_cap_enforced_at_planning": True,
            "total_executable_recovery_options": total_recovery_options,
            "estimated_above_planning_budget_by_node": (
                estimated_above_planning_budget_by_node
            ),
            "planning_estimated_budget_advisory_only": False,
            "risk_adjusted_remaining_budget_enforced_at_planning": True,
            "runtime_budget_controller_authoritative": True,
            "recovery_candidates_retained_for_live_ledger_admission": False,
            "runtime_ledger_revalidates_frozen_risk_multiplier": True,
            "cross_task_history_used": False,
        }
        graph["metadata"] = metadata
        result["execution_graph"] = graph
        result["recovery_pool_policy"] = metadata[
            "recovery_pool_policy"
        ]
        return result

    def optimize_execution_graph(
        self,
        candidate_bundle: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        limits = kwargs.get("limits")
        max_budget_usd = (
            getattr(limits, "max_budget_usd", None)
            if limits is not None
            else None
        )
        filtered_bundle, delivery_policy = (
            self._filter_critical_delivery_candidates(
                candidate_bundle,
                max_budget_usd=max_budget_usd,
            )
        )
        active_bundle: Mapping[str, Any] = filtered_bundle
        try:
            optimization = super().optimize_execution_graph(
                filtered_bundle,
                **kwargs,
            )
        except V5PlanningError:
            if delivery_policy["removed_candidate_count"] <= 0:
                raise
            delivery_policy["fallback_used"] = True
            active_bundle = candidate_bundle
            optimization = super().optimize_execution_graph(
                candidate_bundle,
                **kwargs,
            )
        if (
            not delivery_policy["fallback_used"]
            and isinstance(candidate_bundle, dict)
        ):
            candidate_bundle["candidates"] = [
                dict(row)
                for row in filtered_bundle.get("candidates", [])
                if isinstance(row, Mapping)
            ]
            candidate_bundle[
                "candidate_count_after_critical_delivery"
            ] = len(candidate_bundle["candidates"])
            candidate_bundle["critical_delivery_pruned_count"] = int(
                delivery_policy["removed_candidate_count"]
            )
            candidate_bundle["critical_delivery_policy"] = delivery_policy
            active_bundle = candidate_bundle
        result = dict(optimization)
        result["critical_delivery_policy"] = delivery_policy
        graph = dict(result.get("execution_graph") or {})
        metadata = dict(graph.get("metadata") or {})
        metadata["critical_delivery_policy"] = delivery_policy
        graph["metadata"] = metadata
        result["execution_graph"] = graph
        return self.rebalance_recovery_pool(
            result,
            active_bundle,
        )
