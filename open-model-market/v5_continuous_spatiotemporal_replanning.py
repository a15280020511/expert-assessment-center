"""Continuous current-run spatiotemporal replanning for V5 production.

The execution graph remains finite and auditable, but every calculable recovery
and request-shaping decision is recomputed from two live dimensions:

* time: the ordered sequence of attempts, failures, quality gates, latency and
  observed completion pressure in the current run;
* space: the current node's graph position plus the still-eligible recovery and
  standby candidate space.

No state survives the current task.  Company diversity remains a soft tie-break,
Provider routing stays unrestricted, and no new model-eligibility gate is added.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Mapping, Sequence

from execution_graph import ExecutionGraph, SelectedNode
import v5_production_expert_policy as expert_policy
from v5_model_company import canonical_model_company
from v5_priority_preserving_heterogeneity import (
    PriorityPreservingHeterogeneousExecutionEngine,
)
from v5_runtime import FailureCategory, ProductionRuntime, RuntimeAttempt
from v5_runtime_request_binding import bind_request_knobs as _base_bind_request_knobs
from v5_runtime_timeout import (
    dynamic_model_timeout_seconds as _base_dynamic_model_timeout_seconds,
)


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _positive_int(value: Any, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def continuous_bind_request_knobs(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind request knobs while preserving current-run learned output floors."""
    config, audit = _base_bind_request_knobs(node, original_task, upstream)
    profile = (
        node.parameter_profile
        if isinstance(node.parameter_profile, Mapping)
        else {}
    )
    learned_floor = _positive_int(
        profile.get("dynamic_output_allowance_floor_tokens"),
        0,
    )
    baseline = _positive_int(config.get("max_tokens"), 1)
    effective = max(baseline, learned_floor)
    config = dict(config)
    config["max_tokens"] = effective
    audit = dict(audit)
    audit.update(
        {
            "continuous_spatiotemporal_replanning": True,
            "pre_feedback_output_allowance_tokens": baseline,
            "current_run_feedback_output_floor_tokens": learned_floor or None,
            "dynamic_output_allowance_tokens": effective,
            "current_run_feedback_floor_applied": effective > baseline,
            "current_run_replan_epoch": _positive_int(
                profile.get("current_run_replan_epoch"),
                0,
            ),
            "recompute_trigger": (
                "current-request-shape-plus-current-run-node-feedback"
            ),
        }
    )
    return config, audit


def continuous_dynamic_model_timeout_seconds(
    node: Any,
    payload: Mapping[str, Any],
    safety_cap_seconds: int,
) -> tuple[int, dict[str, Any]]:
    """Bind timeout while preserving the learned current-run node floor."""
    baseline, audit = _base_dynamic_model_timeout_seconds(
        node,
        payload,
        safety_cap_seconds,
    )
    profile = getattr(node, "parameter_profile", {})
    profile = profile if isinstance(profile, Mapping) else {}
    learned_floor = _positive_int(
        profile.get("dynamic_model_timeout_floor_seconds"),
        0,
    )
    cap = max(1, int(safety_cap_seconds))
    effective = min(cap, max(int(baseline), learned_floor))
    audit = dict(audit)
    audit.update(
        {
            "continuous_spatiotemporal_replanning": True,
            "pre_feedback_effective_timeout_seconds": int(baseline),
            "current_run_feedback_timeout_floor_seconds": learned_floor or None,
            "effective_timeout_seconds": int(effective),
            "current_run_feedback_floor_applied": effective > int(baseline),
            "current_run_replan_epoch": _positive_int(
                profile.get("current_run_replan_epoch"),
                0,
            ),
            "effective_timeout_source": (
                "current-request-shape-plus-current-run-node-feedback"
            ),
        }
    )
    return int(effective), audit


class ContinuousSpatiotemporalExecutionEngine(
    PriorityPreservingHeterogeneousExecutionEngine
):
    """Recompute current-run request and recovery decisions at every transition."""

    def _ensure_continuous_state(self) -> None:
        self._ensure_production_failure_state()
        self._ensure_feedback_state()
        if not hasattr(self, "_continuous_epoch"):
            self._continuous_epoch = 0
            self._continuous_node_state: dict[str, dict[str, Any]] = {}
            self._continuous_graph_state: dict[str, Any] = {}
            self._continuous_replan_events: list[dict[str, Any]] = []
            self._last_spatiotemporal_batch: dict[str, Any] = {}

    def _initialize_feedback(self, graph: ExecutionGraph) -> None:
        super()._initialize_feedback(graph)
        self._ensure_continuous_state()

        indegree = {node.node_id: 0 for node in graph.nodes}
        outdegree = {node.node_id: 0 for node in graph.nodes}
        for edge in graph.edges:
            if edge.target in indegree:
                indegree[edge.target] += 1
            if edge.source in outdegree:
                outdegree[edge.source] += 1

        stage_index: dict[str, int] = {}
        for index, stage in enumerate(graph.execution_stages):
            for node_id in stage:
                stage_index[str(node_id)] = index

        final_nodes = set(graph.final_nodes)
        node_state = {
            node.node_id: {
                "node_id": node.node_id,
                "attempts": 0,
                "failures": 0,
                "quality_failures": 0,
                "truncations": 0,
                "timeouts": 0,
                "output_allowance_floor_tokens": 0,
                "timeout_floor_seconds": 0,
                "max_observed_completion_tokens": 0,
                "max_observed_latency_seconds": 0.0,
                "latest_failure_category": None,
                "latest_epoch": 0,
                "stage_index": int(stage_index.get(node.node_id, 0)),
                "indegree": int(indegree.get(node.node_id, 0)),
                "outdegree": int(outdegree.get(node.node_id, 0)),
                "is_final_node": node.node_id in final_nodes,
            }
            for node in graph.nodes
        }

        with self._feedback_lock:
            self._continuous_epoch = 0
            self._continuous_node_state = node_state
            self._continuous_graph_state = {
                "node_count": len(graph.nodes),
                "edge_count": len(graph.edges),
                "stage_count": len(graph.execution_stages),
                "final_node_count": len(graph.final_nodes),
                "entry_node_count": len(graph.entry_nodes),
            }
            self._continuous_replan_events = []
            self._last_spatiotemporal_batch = {}

    @staticmethod
    def _attempt_node_id(attempt: RuntimeAttempt | Any) -> str:
        return str(getattr(attempt, "candidate_id", "") or "").strip()

    def _record_feedback(self, attempt: Any | None) -> None:
        super()._record_feedback(attempt)
        if attempt is None:
            return
        self._ensure_continuous_state()

        node_id = self._attempt_node_id(attempt)
        category = self._category(attempt)
        request = attempt.request if isinstance(attempt.request, Mapping) else {}
        usage = attempt.usage if isinstance(attempt.usage, Mapping) else {}
        allowance = _positive_int(request.get("max_tokens"), 0)
        completion = _positive_int(usage.get("completion_tokens"), 0)
        latency = max(0.0, _finite_float(getattr(attempt, "latency_seconds", 0.0), 0.0))
        timeout_binding = self._timeout_binding(attempt)
        effective_timeout = _positive_int(
            timeout_binding.get("effective_timeout_seconds"),
            0,
        )

        with self._feedback_lock:
            self._continuous_epoch += 1
            state = self._continuous_node_state.setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "attempts": 0,
                    "failures": 0,
                    "quality_failures": 0,
                    "truncations": 0,
                    "timeouts": 0,
                    "output_allowance_floor_tokens": 0,
                    "timeout_floor_seconds": 0,
                    "max_observed_completion_tokens": 0,
                    "max_observed_latency_seconds": 0.0,
                    "latest_failure_category": None,
                    "latest_epoch": 0,
                    "stage_index": 0,
                    "indegree": 0,
                    "outdegree": 0,
                    "is_final_node": False,
                },
            )
            state["attempts"] = int(state.get("attempts", 0)) + 1
            state["latest_epoch"] = int(self._continuous_epoch)
            state["max_observed_completion_tokens"] = max(
                int(state.get("max_observed_completion_tokens", 0)),
                completion,
            )
            state["max_observed_latency_seconds"] = max(
                float(state.get("max_observed_latency_seconds", 0.0)),
                latency,
            )

            failed = str(getattr(attempt, "status", "")) != "passed"
            if failed:
                state["failures"] = int(state.get("failures", 0)) + 1
                state["latest_failure_category"] = category.value

            if failed and category == FailureCategory.QUALITY_GATE_FAILED:
                state["quality_failures"] = int(state.get("quality_failures", 0)) + 1

            if failed and category == FailureCategory.OUTPUT_TRUNCATED:
                state["truncations"] = int(state.get("truncations", 0)) + 1
                pressure = min(
                    1.0,
                    completion / max(1, allowance),
                )
                learned_floor = math.ceil(
                    max(1, allowance, completion)
                    * (1.0 + max(0.25, pressure))
                )
                state["output_allowance_floor_tokens"] = max(
                    int(state.get("output_allowance_floor_tokens", 0)),
                    learned_floor,
                )

            if failed and category == FailureCategory.PROVIDER_TIMEOUT:
                state["timeouts"] = int(state.get("timeouts", 0)) + 1
                baseline = max(1, effective_timeout, math.ceil(latency))
                pressure = min(1.0, latency / max(1.0, float(effective_timeout or baseline)))
                learned_floor = math.ceil(
                    baseline * (1.0 + max(0.25, pressure))
                )
                state["timeout_floor_seconds"] = max(
                    int(state.get("timeout_floor_seconds", 0)),
                    learned_floor,
                )

    def _node_state_snapshot(self, node_id: str) -> dict[str, Any]:
        self._ensure_continuous_state()
        with self._feedback_lock:
            return dict(self._continuous_node_state.get(node_id, {}))

    def _spatial_pressure(self, node_id: str) -> float:
        state = self._node_state_snapshot(node_id)
        with self._feedback_lock:
            graph = dict(self._continuous_graph_state)
        node_count = max(1, int(graph.get("node_count", 1)))
        stage_count = max(1, int(graph.get("stage_count", 1)))
        degree = max(0, int(state.get("indegree", 0))) + max(
            0, int(state.get("outdegree", 0))
        )
        max_degree = max(1, 2 * max(1, node_count - 1))
        degree_pressure = min(1.0, degree / max_degree)
        stage_pressure = min(
            1.0,
            (int(state.get("stage_index", 0)) + 1) / stage_count,
        )
        final_pressure = 1.0 if state.get("is_final_node") else 0.0
        return min(
            1.0,
            (degree_pressure + stage_pressure + final_pressure) / 3.0,
        )

    def _temporal_pressure(self, node_id: str) -> float:
        state = self._node_state_snapshot(node_id)
        attempts = max(1, int(state.get("attempts", 0)))
        failures = int(state.get("failures", 0))
        quality_failures = int(state.get("quality_failures", 0))
        truncations = int(state.get("truncations", 0))
        timeouts = int(state.get("timeouts", 0))
        return min(
            1.0,
            max(
                failures / attempts,
                quality_failures / attempts,
                truncations / attempts,
                timeouts / attempts,
            ),
        )

    def _replacement_adaptation(
        self,
        replacement: SelectedNode,
        source: RuntimeAttempt | None,
        reasoning_saturated: bool,
    ) -> tuple[SelectedNode, dict[str, Any] | None]:
        adapted, inherited = super()._replacement_adaptation(
            replacement,
            source,
            reasoning_saturated,
        )
        self._ensure_continuous_state()
        state = self._node_state_snapshot(adapted.node_id)
        profile = dict(adapted.parameter_profile)

        learned_output_floor = int(state.get("output_allowance_floor_tokens", 0))
        learned_timeout_floor = int(state.get("timeout_floor_seconds", 0))
        if learned_output_floor > 0:
            profile["dynamic_output_allowance_floor_tokens"] = max(
                _positive_int(
                    profile.get("dynamic_output_allowance_floor_tokens"),
                    0,
                ),
                learned_output_floor,
            )
        if learned_timeout_floor > 0:
            profile["dynamic_model_timeout_floor_seconds"] = max(
                _positive_int(
                    profile.get("dynamic_model_timeout_floor_seconds"),
                    0,
                ),
                learned_timeout_floor,
            )

        with self._feedback_lock:
            epoch = int(self._continuous_epoch)
        profile["current_run_replan_epoch"] = epoch
        profile["current_run_node_failure_count"] = int(state.get("failures", 0))
        profile["current_run_spatial_pressure"] = round(
            self._spatial_pressure(adapted.node_id),
            6,
        )
        profile["current_run_temporal_pressure"] = round(
            self._temporal_pressure(adapted.node_id),
            6,
        )
        adapted = replace(adapted, parameter_profile=profile)

        continuous = {
            "schema_version": "v5-continuous-spatiotemporal-rebinding-1",
            "enabled": True,
            "node_id": adapted.node_id,
            "epoch": epoch,
            "time_dimension": {
                "attempts": int(state.get("attempts", 0)),
                "failures": int(state.get("failures", 0)),
                "quality_failures": int(state.get("quality_failures", 0)),
                "truncations": int(state.get("truncations", 0)),
                "timeouts": int(state.get("timeouts", 0)),
            },
            "space_dimension": {
                "stage_index": int(state.get("stage_index", 0)),
                "indegree": int(state.get("indegree", 0)),
                "outdegree": int(state.get("outdegree", 0)),
                "is_final_node": bool(state.get("is_final_node", False)),
                "spatial_pressure": profile["current_run_spatial_pressure"],
            },
            "learned_output_allowance_floor_tokens": learned_output_floor or None,
            "learned_timeout_floor_seconds": learned_timeout_floor or None,
            "cross_model_feedback_persistence_scope": "same-node-current-run-only",
            "cross_task_history_used": False,
        }

        if inherited is None:
            return adapted, {
                "type": "continuous-spatiotemporal-request-rebinding",
                "policy": "current-run-time-space-feedback-recompute-v1",
                "continuous_spatiotemporal_replanning": continuous,
            }
        combined = dict(inherited)
        combined["continuous_spatiotemporal_replanning"] = continuous
        return adapted, combined

    def _dynamic_promotion_depth(self, node_attempts: Sequence[Any]) -> int:
        parent_depth = int(super()._dynamic_promotion_depth(node_attempts))
        if parent_depth <= 0:
            return 0
        self._ensure_continuous_state()
        node_id = (
            self._attempt_node_id(node_attempts[-1])
            if node_attempts
            else ""
        )
        spatial = self._spatial_pressure(node_id)
        temporal = self._temporal_pressure(node_id)
        live_pressure = math.sqrt(max(0.0, spatial * temporal))

        with self._feedback_lock:
            eligible_count = sum(
                1
                for row in self._standby_inventory
                if str(row.get("model") or "").strip()
                and str(row.get("model") or "").strip() not in self._standby_claimed
                and str(row.get("model") or "").strip()
                not in self._hard_failed_model_ids
            )
        effective = min(
            eligible_count,
            max(
                1,
                math.ceil(parent_depth * (1.0 + live_pressure)),
            ),
        )
        self._last_spatiotemporal_batch = {
            "node_id": node_id,
            "epoch": int(getattr(self, "_continuous_epoch", 0)),
            "parent_dynamic_depth": parent_depth,
            "eligible_standby_count": eligible_count,
            "spatial_pressure": round(spatial, 6),
            "temporal_pressure": round(temporal, 6),
            "joint_live_pressure": round(live_pressure, 6),
            "effective_dynamic_depth": effective,
            "recompute_trigger": "before-each-recovery-selection",
            "fixed_depth_used": False,
        }
        return effective

    def _available_standby_rows(self, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        self._ensure_continuous_state()
        with self._feedback_lock:
            result: list[dict[str, Any]] = []
            for row in self._standby_inventory:
                model = str(row.get("model") or "").strip()
                if (
                    not model
                    or model in self._standby_claimed
                    or model in self._hard_failed_model_ids
                ):
                    continue
                result.append(dict(row))
                if len(result) >= limit:
                    break
            return result

    def _claim_specific_standby(self, row: Mapping[str, Any]) -> bool:
        model = str(row.get("model") or "").strip()
        if not model:
            return False
        with self._feedback_lock:
            if (
                model in self._standby_claimed
                or model in self._hard_failed_model_ids
                or self._provider_account_blocked
            ):
                return False
            self._standby_claimed.add(model)
            return True

    def _record_continuous_replan_event(
        self,
        *,
        selected: SelectedNode,
        candidate: SelectedNode,
        source_kind: str,
        category_before: Any,
        attempted: Any | None,
        initial_remaining: int,
        standby_window: int,
    ) -> None:
        self._ensure_continuous_state()
        state = self._node_state_snapshot(selected.node_id)
        event = {
            "event_type": "continuous-spatiotemporal-replan",
            "epoch": int(getattr(self, "_continuous_epoch", 0)),
            "node_id": selected.node_id,
            "selected_model": selected.model,
            "candidate_model": candidate.model,
            "candidate_company": canonical_model_company(candidate.model),
            "candidate_source": source_kind,
            "category_before": str(
                getattr(category_before, "value", category_before)
            ),
            "attempt_status": str(getattr(attempted, "status", "not-called")),
            "category_after": (
                "PASSED"
                if attempted is not None
                and str(getattr(attempted, "status", "")) == "passed"
                else (
                    self._category(attempted).value
                    if attempted is not None
                    else None
                )
            ),
            "initial_recovery_remaining": int(initial_remaining),
            "standby_dynamic_window": int(standby_window),
            "spatial_pressure": round(self._spatial_pressure(selected.node_id), 6),
            "temporal_pressure": round(self._temporal_pressure(selected.node_id), 6),
            "learned_output_allowance_floor_tokens": int(
                state.get("output_allowance_floor_tokens", 0)
            ),
            "learned_timeout_floor_seconds": int(
                state.get("timeout_floor_seconds", 0)
            ),
            "recomputed_after_every_attempt": True,
            "cross_task_history_used": False,
        }
        with self._feedback_lock:
            self._continuous_replan_events.append(event)

    def _recover_node(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        recovery_rows: Sequence[Mapping[str, Any]],
        category: Any,
        best: tuple[Any, SelectedNode] | None,
        call: Any,
    ) -> tuple[Any | None, tuple[Any, SelectedNode] | None, SelectedNode]:
        """Recompute the recovery candidate space before every replacement call."""
        self._ensure_continuous_state()
        eligible = set(self.recovery_policy.replace_categories)
        eligible.add(FailureCategory.QUALITY_GATE_FAILED)
        if category not in eligible:
            return None, best, selected

        # Preserve the current production rule: repair a truncated request shape
        # once on the same model before paying for cross-model substitution.
        if category == FailureCategory.OUTPUT_TRUNCATED and attempts:
            source = attempts[-1]
            adapted, adaptation = self._replacement_adaptation(
                selected,
                source,
                False,
            )
            retried = call(adapted, "retry")
            if retried is not None:
                if adaptation is not None:
                    retried.answer_transformations.append(adaptation)
                self._same_model_truncation_retries.append(
                    {
                        "model": selected.model,
                        "source_attempt_index": int(
                            getattr(source, "attempt_index", 0)
                        ),
                        "retry_attempt_index": int(
                            getattr(retried, "attempt_index", 0)
                        ),
                        "status": str(getattr(retried, "status", "")),
                        "policy": (
                            "same-model-feedback-rebind-before-cross-model-recovery"
                        ),
                    }
                )
                self._record_continuous_replan_event(
                    selected=selected,
                    candidate=adapted,
                    source_kind="same-model-retry",
                    category_before=category,
                    attempted=retried,
                    initial_remaining=len(recovery_rows),
                    standby_window=0,
                )
                if retried.status == "passed":
                    return (
                        self._node_result(
                            selected,
                            adapted,
                            attempts,
                            retried,
                            "success_retried",
                        ),
                        best,
                        adapted,
                    )
                best = self._better_degraded(
                    best,
                    retried,
                    adapted,
                    self._degraded_usable(adapted, retried),
                )
                category = self._category(retried)
                if category not in eligible:
                    return None, best, adapted

        remaining_initial = [
            dict(row)
            for row in recovery_rows
            if str(row.get("model") or "").strip()
            and str(row.get("model") or "").strip()
            not in self._hard_failed_model_ids
        ]
        last_node = selected

        while category in eligible:
            if getattr(self, "_provider_account_blocked", False):
                break

            attempted_models = {
                str(getattr(row, "model", "") or "").strip()
                for row in attempts
                if str(getattr(row, "model", "") or "").strip()
            }
            remaining_initial = [
                row
                for row in remaining_initial
                if str(row.get("model") or "").strip() not in attempted_models
                and str(row.get("model") or "").strip()
                not in self._hard_failed_model_ids
            ]
            ranked_initial = self._diversify_rows(remaining_initial, category)
            self._rerank_standby_for_failure(category)
            standby_window = self._dynamic_promotion_depth(attempts)
            standby_rows = self._available_standby_rows(standby_window)

            tried_companies = set(self._attempted_company_sequence)
            candidate_space: list[tuple[str, dict[str, Any]]] = [
                ("initial-recovery", dict(row)) for row in ranked_initial
            ]
            candidate_space.extend(
                ("standby", dict(row)) for row in standby_rows
            )
            if not candidate_space:
                break
            candidate_space.sort(
                key=lambda item: self._failure_rank_key(
                    item[1],
                    category,
                    tried_companies,
                )
            )
            source_kind, row = candidate_space[0]

            model = str(row.get("model") or "").strip()
            if source_kind == "standby":
                if not self._claim_specific_standby(row):
                    continue
            else:
                remaining_initial = [
                    candidate
                    for candidate in remaining_initial
                    if str(candidate.get("model") or "").strip() != model
                ]

            candidate = self._candidate(row, selected)
            original = candidate
            source = attempts[-1] if attempts else None
            saturated = self._reasoning_saturated_attempt(source)
            candidate, adaptation = self._replacement_adaptation(
                candidate,
                source,
                saturated,
            )
            attempted = call(candidate, "replacement")
            if attempted is not None and adaptation is not None:
                attempted.answer_transformations.append(adaptation)

            if source_kind == "standby":
                self._record_promotion_event(
                    selected=selected,
                    candidate=candidate,
                    trigger_category=category,
                    attempt=attempted,
                    planned_depth=standby_window,
                    ordinal=1,
                )
            self._record_continuous_replan_event(
                selected=selected,
                candidate=candidate,
                source_kind=source_kind,
                category_before=category,
                attempted=attempted,
                initial_remaining=len(remaining_initial),
                standby_window=standby_window,
            )

            if attempted is None:
                continue
            last_node = candidate
            if attempted.status == "passed":
                return (
                    self._node_result(
                        selected,
                        candidate,
                        attempts,
                        attempted,
                        "success_recovered",
                    ),
                    best,
                    last_node,
                )

            quality_node = candidate if adaptation is not None else original
            best = self._better_degraded(
                best,
                attempted,
                candidate,
                self._degraded_usable(quality_node, attempted),
            )
            category = self._category(attempted)

        return None, best, last_node

    def _feedback_snapshot(self) -> dict[str, Any]:
        value = dict(super()._feedback_snapshot())
        self._ensure_continuous_state()
        with self._feedback_lock:
            node_state = {
                node_id: dict(state)
                for node_id, state in self._continuous_node_state.items()
            }
            events = [dict(row) for row in self._continuous_replan_events]
            graph_state = dict(self._continuous_graph_state)
            epoch = int(self._continuous_epoch)
        value.update(
            {
                "continuous_spatiotemporal_replanning": True,
                "continuous_replan_schema_version": (
                    "v5-continuous-spatiotemporal-replanning-1"
                ),
                "recompute_granularity": [
                    "after-each-model-attempt",
                    "after-each-quality-gate",
                    "before-each-retry-or-replacement",
                    "when-current-run-candidate-space-changes",
                ],
                "time_dimension_current_run_only": True,
                "space_dimension_current_graph_and_candidate_space": True,
                "current_replan_epoch": epoch,
                "graph_spatial_state": graph_state,
                "node_runtime_state": node_state,
                "last_spatiotemporal_batch": dict(
                    getattr(self, "_last_spatiotemporal_batch", {})
                ),
                "continuous_replan_events": events,
                "output_allowance_floor_persists_across_replacements": True,
                "timeout_floor_persists_across_replacements": True,
                "initial_recovery_order_static": False,
                "recovery_candidate_space_recomputed_each_iteration": True,
                "finite_graph_invariant_preserved": True,
                "cross_task_history_used": False,
            }
        )
        return value


def install_continuous_spatiotemporal_replanning(
    runtime: ProductionRuntime,
) -> ProductionRuntime:
    """Install the final production engine with continuous current-run replanning."""
    # ProductionExpertPromptPolicy and EvidenceCompleteExecutionEngine resolve
    # these module globals at call time, so the install is explicit and scoped
    # to this production runtime assembly path.
    expert_policy.bind_request_knobs = continuous_bind_request_knobs
    expert_policy.dynamic_model_timeout_seconds = (
        continuous_dynamic_model_timeout_seconds
    )
    runtime.execution_engine = ContinuousSpatiotemporalExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


__all__ = [
    "ContinuousSpatiotemporalExecutionEngine",
    "continuous_bind_request_knobs",
    "continuous_dynamic_model_timeout_seconds",
    "install_continuous_spatiotemporal_replanning",
]
