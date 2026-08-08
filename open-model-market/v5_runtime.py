"""Task-dynamic production runtime facade.

The legacy executor is retained for I/O and state-machine compatibility, while
this facade removes historical business ceilings. Call/recovery capacity and
runtime resilience telemetry are derived from the current finite execution
graph. Provider routing stays open and company identity is audit telemetry only.
Structural safety, no-tools, evidence contracts and finite DAG execution remain
enforced.

Initial recovery depth is still computed before execution, but it is no longer
the end of the recovery process. If current-run failures or quality-gate failures
exhaust the initially activated recovery rows, the runtime recomputes a finite
promotion depth from live feedback and may promote additional candidates from
the current task's ordered standby inventory. No cross-task history is used.
Account-level OpenRouter credit failures are transport-wide conditions rather
than model failures, so the current run stops issuing further model requests
instead of wasting standby promotions that cannot repair the account state.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any, Callable, Mapping, Sequence

import v5_runtime_legacy as _legacy
from execution_graph import ExecutionGraph, GraphLimits, SelectedNode
from execution_graph_validator import validate_execution_graph
from v5_no_tools_policy import (
    assert_request_has_no_tools,
    assert_response_has_no_tools,
)

_LegacyExecutionEngine = _legacy.ExecutionEngine

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


@dataclass(frozen=True)
class RuntimeConfig:
    """Compatibility telemetry; fixed call counts are not execution gates."""

    total_call_limit: int
    recovery_call_limit: int
    cost_anomaly_usd: float | None
    tools_allowed: bool = False
    live_catalog_required: bool = False
    provider_lock_required: bool = False
    cost_risk_multiplier: float = 1.0
    max_provider_failures: int = 1

    def __post_init__(self) -> None:
        if int(self.total_call_limit) < 1:
            raise ValueError("total_call_limit telemetry must be positive")
        if int(self.recovery_call_limit) < 0:
            raise ValueError("recovery_call_limit telemetry must be non-negative")
        if self.cost_anomaly_usd is not None and not math.isfinite(
            float(self.cost_anomaly_usd)
        ):
            raise ValueError("cost_anomaly_usd must be finite when supplied")
        if self.tools_allowed:
            raise ValueError("expert runtime external tools remain disabled")
        if self.provider_lock_required:
            raise ValueError(
                "active runtime uses unrestricted OpenRouter Provider routing"
            )
        if not math.isfinite(float(self.cost_risk_multiplier)) or float(
            self.cost_risk_multiplier
        ) <= 0:
            raise ValueError("cost_risk_multiplier must be finite and positive")
        if int(self.max_provider_failures) < 0:
            raise ValueError("max_provider_failures must be non-negative")

    @property
    def initial_call_limit(self) -> int:
        # Compatibility only. The BudgetController below derives real capacity
        # from the current graph and never uses this property as admission.
        return max(1, int(self.total_call_limit) - int(self.recovery_call_limit))

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "initial_call_limit": self.initial_call_limit,
            "runtime_version": _legacy.RUNTIME_VERSION,
            "provider_routing_mode": "unrestricted-openrouter",
            "fixed_call_ceiling_applied": False,
            "team_size_source": "current-execution-graph",
            "recovery_capacity_source": (
                "current-run-active-recovery-plus-runtime-promotable-standby"
            ),
            "runtime_resilience_parameters_dynamic": True,
            "runtime_feedback_replanning_enabled": True,
            "standby_promotion_depth_fixed": False,
            "failed_model_circuit_scope": "current-run-only",
            "cross_task_history_used": False,
            "tool_use_forbidden": True,
        }


def _recovery_capacity(graph: ExecutionGraph) -> int:
    """Count unique initially activated recovery identities."""
    metadata = graph.metadata if isinstance(graph.metadata, Mapping) else {}
    pool = metadata.get("recovery_pool")
    if not isinstance(pool, Mapping):
        return 0
    identities: set[tuple[str, str]] = set()
    for rows in pool.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            model = str(row.get("model") or "").strip()
            endpoint = str(row.get("provider_endpoint") or "").strip()
            if model:
                identities.add((model, endpoint))
    return len(identities)


def _standby_capacity(graph: ExecutionGraph) -> int:
    """Count unique current-task standby identities available for promotion."""
    metadata = graph.metadata if isinstance(graph.metadata, Mapping) else {}
    rows = metadata.get("standby_inventory")
    if not isinstance(rows, list):
        return 0
    identities: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        model = str(row.get("model") or "").strip()
        endpoint = str(row.get("provider_endpoint") or "").strip()
        if model:
            identities.add((model, endpoint))
    return len(identities)


def _dynamic_resilience_parameters(
    graph: ExecutionGraph,
) -> tuple[float, int]:
    """Derive runtime resilience telemetry from this execution graph only."""
    initial = max(1, len(graph.nodes))
    recovery = max(0, _recovery_capacity(graph))
    standby = max(0, _standby_capacity(graph))
    # Standby is only potential recovery. Use its square-root breadth for
    # circuit telemetry so merely having a large inventory does not create an
    # artificially huge same-endpoint failure tolerance.
    adaptive_breadth = recovery + math.ceil(math.sqrt(standby)) if standby else recovery
    graph_width = max(1, initial + adaptive_breadth)
    cost_risk_multiplier = 1.0 + min(
        1.0,
        math.log2(graph_width + 1) / 10.0,
    )
    max_provider_failures = max(
        1,
        math.ceil(adaptive_breadth / max(1, initial)),
    )
    return float(cost_risk_multiplier), int(max_provider_failures)


def _dynamic_config(config: RuntimeConfig, graph: ExecutionGraph) -> RuntimeConfig:
    initial = max(1, len(graph.nodes))
    recovery = max(0, _recovery_capacity(graph))
    standby = max(0, _standby_capacity(graph))
    cost_risk_multiplier, max_provider_failures = _dynamic_resilience_parameters(
        graph
    )
    return RuntimeConfig(
        # This is a finite structural capacity for the current graph, not a
        # business admission quota. Standby calls are not made unless runtime
        # feedback promotes them.
        total_call_limit=initial + recovery + standby,
        recovery_call_limit=recovery + standby,
        cost_anomaly_usd=config.cost_anomaly_usd,
        tools_allowed=False,
        live_catalog_required=config.live_catalog_required,
        provider_lock_required=False,
        cost_risk_multiplier=cost_risk_multiplier,
        max_provider_failures=max_provider_failures,
    )


class BudgetController(_legacy.BudgetController):
    """Use graph-derived finite capacity instead of CLI/config call ceilings."""

    def __init__(self, config: RuntimeConfig, graph: ExecutionGraph) -> None:
        self.requested_config = config
        self.active_recovery_capacity = _recovery_capacity(graph)
        self.standby_capacity = _standby_capacity(graph)
        super().__init__(_dynamic_config(config, graph), graph)

    def snapshot(self) -> dict[str, Any]:
        value = dict(super().snapshot())
        value.update(
            {
                "fixed_call_ceiling_applied": False,
                "call_capacity_source": "current-finite-execution-graph",
                "recovery_capacity_source": (
                    "active-recovery-plus-runtime-promotable-standby"
                ),
                "active_recovery_capacity": int(self.active_recovery_capacity),
                "runtime_promotable_standby_capacity": int(self.standby_capacity),
                "runtime_resilience_parameters_dynamic": True,
                "runtime_feedback_replanning_enabled": True,
                "standby_promotion_depth_fixed": False,
                "failed_model_circuit_scope": "current-run-only",
                "failed_model_circuit_threshold": int(
                    self.config.max_provider_failures
                ),
                "requested_total_call_telemetry": int(
                    self.requested_config.total_call_limit
                ),
                "requested_recovery_call_telemetry": int(
                    self.requested_config.recovery_call_limit
                ),
            }
        )
        return value


class ExecutionEngine(_LegacyExecutionEngine):
    """Validate intrinsic graph safety and adapt recovery from live feedback."""

    _IGNORED_BUSINESS_LIMIT_CODES = {
        "budget_limit",
        "node_limit",
        "edge_limit",
        "call_limit",
        "stage_limit",
        "model_company_reuse",
    }

    def _ensure_feedback_state(self) -> None:
        if not hasattr(self, "_feedback_lock"):
            self._feedback_lock = Lock()
            self._standby_inventory: list[dict[str, Any]] = []
            self._standby_claimed: set[str] = set()
            self._feedback_attempts = 0
            self._feedback_failures = 0
            self._feedback_quality_failures = 0
            self._feedback_promotions = 0
            self._feedback_primary_count = 1
            self._feedback_events: list[dict[str, Any]] = []
            self._provider_account_blocked = False
            self._provider_account_block_reason = ""

    def _initialize_feedback(self, graph: ExecutionGraph) -> None:
        self._ensure_feedback_state()
        metadata = graph.metadata if isinstance(graph.metadata, Mapping) else {}
        rows = metadata.get("standby_inventory")
        inventory = [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []
        with self._feedback_lock:
            self._standby_inventory = inventory
            self._standby_claimed = set()
            self._feedback_attempts = 0
            self._feedback_failures = 0
            self._feedback_quality_failures = 0
            self._feedback_promotions = 0
            self._feedback_primary_count = max(1, len(graph.nodes))
            self._feedback_events = []
            self._provider_account_blocked = False
            self._provider_account_block_reason = ""

    def _feedback_snapshot(self) -> dict[str, Any]:
        self._ensure_feedback_state()
        with self._feedback_lock:
            attempts = int(self._feedback_attempts)
            failures = int(self._feedback_failures)
            quality_failures = int(self._feedback_quality_failures)
            standby_total = len(self._standby_inventory)
            claimed = len(self._standby_claimed)
            return {
                "schema_version": "v5-current-run-feedback-replanning-2",
                "enabled": bool(standby_total),
                "promotion_trigger": (
                    "initial-recovery-exhausted-plus-current-run-failure-feedback"
                ),
                "promotion_depth_fixed": False,
                "promotion_depth_recomputed_from_current_run": True,
                "observed_attempts": attempts,
                "observed_failures": failures,
                "observed_failure_rate": round(failures / max(1, attempts), 6),
                "observed_quality_gate_failures": quality_failures,
                "observed_quality_gate_failure_rate": round(
                    quality_failures / max(1, attempts), 6
                ),
                "standby_total": standby_total,
                "standby_promoted_or_claimed": claimed,
                "standby_remaining": max(0, standby_total - claimed),
                "promotion_attempts": int(self._feedback_promotions),
                "provider_account_blocked": bool(self._provider_account_blocked),
                "provider_account_block_reason": self._provider_account_block_reason,
                "account_level_failure_stops_model_recovery": True,
                "events": [dict(row) for row in self._feedback_events],
                "cross_task_history_used": False,
            }

    def _record_feedback(self, attempt: Any | None) -> None:
        if attempt is None:
            return
        self._ensure_feedback_state()
        category = self._category(attempt)
        with self._feedback_lock:
            self._feedback_attempts += 1
            if str(getattr(attempt, "status", "")) != "passed":
                self._feedback_failures += 1
                if category == _legacy.FailureCategory.QUALITY_GATE_FAILED:
                    self._feedback_quality_failures += 1

    def _dynamic_promotion_depth(self, node_attempts: Sequence[Any]) -> int:
        """Recompute finite standby depth from current-run observations."""
        self._ensure_feedback_state()
        with self._feedback_lock:
            if self._provider_account_blocked:
                return 0
            remaining = max(
                0,
                len(self._standby_inventory) - len(self._standby_claimed),
            )
            if remaining <= 0:
                return 0
            attempts = max(1, int(self._feedback_attempts))
            failure_rate = self._feedback_failures / attempts
            quality_rate = self._feedback_quality_failures / attempts
            node_failures = sum(
                1
                for row in node_attempts
                if str(getattr(row, "status", "")) != "passed"
            )
            primary = max(1, int(self._feedback_primary_count))
            node_pressure = min(1.0, node_failures / primary)
            feedback_pressure = min(
                1.0,
                (failure_rate + quality_rate + node_pressure) / 3.0,
            )
            structural_breadth = math.sqrt(remaining / primary)
            depth = math.ceil(structural_breadth * (1.0 + feedback_pressure))
            return min(remaining, max(1, int(depth)))

    def _claim_next_standby(self) -> dict[str, Any] | None:
        self._ensure_feedback_state()
        with self._feedback_lock:
            if self._provider_account_blocked:
                return None
            for row in self._standby_inventory:
                model = str(row.get("model") or "").strip()
                if not model or model in self._standby_claimed:
                    continue
                self._standby_claimed.add(model)
                return dict(row)
        return None

    def _record_promotion_event(
        self,
        *,
        selected: SelectedNode,
        candidate: SelectedNode,
        trigger_category: Any,
        attempt: Any | None,
        planned_depth: int,
        ordinal: int,
    ) -> None:
        self._ensure_feedback_state()
        event = {
            "event_type": "standby-promotion",
            "node_id": selected.node_id,
            "selected_model": selected.model,
            "promoted_model": candidate.model,
            "trigger_category": str(getattr(trigger_category, "value", trigger_category)),
            "planned_dynamic_promotion_depth": int(planned_depth),
            "promotion_ordinal": int(ordinal),
            "attempt_status": str(getattr(attempt, "status", "not-called")),
            "passed": bool(attempt is not None and getattr(attempt, "status", "") == "passed"),
        }
        with self._feedback_lock:
            self._feedback_promotions += 1
            self._feedback_events.append(event)

    @staticmethod
    def _failure_from_exception(
        exc: BaseException,
        node: SelectedNode,
    ) -> Any:
        """Classify account-level 402 responses separately from model failures."""
        status = getattr(exc, "http_status", None)
        message = str(exc)
        account_credit_failure = (
            status == 402
            or "insufficient credits" in message.casefold()
        )
        if account_credit_failure:
            diagnostics = dict(
                getattr(exc, "response_diagnostics", {}) or {}
            )
            diagnostics.update(
                {
                    "provider_account_credit_insufficient": True,
                    "failure_scope": "current-openrouter-account",
                    "model_replacement_can_repair": False,
                }
            )
            retry_after = getattr(exc, "retry_after_seconds", None)
            return _legacy.ExecutionFailure(
                category=_legacy.FailureCategory.BUDGET_INSUFFICIENT,
                retryable=False,
                http_status=int(status) if status is not None else 402,
                retry_after_seconds=(
                    float(retry_after) if retry_after is not None else None
                ),
                model=node.model,
                provider_endpoint=node.provider_endpoint,
                request_sent=bool(getattr(exc, "request_sent", True)),
                response_received=bool(
                    getattr(exc, "response_received", True)
                ),
                usage_received=False,
                actual_cost_usd=0.0,
                message=message,
                response_diagnostics=diagnostics,
            )
        return _LegacyExecutionEngine._failure_from_exception(exc, node)

    def _mark_provider_account_blocked(self, attempt: Any | None) -> None:
        if attempt is None:
            return
        failure = getattr(attempt, "failure", None)
        if not isinstance(failure, Mapping):
            return
        try:
            status = int(failure.get("http_status") or 0)
        except (TypeError, ValueError):
            status = 0
        diagnostics = failure.get("response_diagnostics", {})
        diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
        if status != 402 and not diagnostics.get(
            "provider_account_credit_insufficient"
        ):
            return
        self._ensure_feedback_state()
        with self._feedback_lock:
            if self._provider_account_blocked:
                return
            self._provider_account_blocked = True
            self._provider_account_block_reason = (
                "openrouter-http-402-insufficient-credits"
            )
            self._feedback_events.append(
                {
                    "event_type": "provider-account-circuit-opened",
                    "http_status": status or 402,
                    "reason": self._provider_account_block_reason,
                    "further_model_requests_suppressed": True,
                    "standby_promotion_suppressed": True,
                }
            )

    def _validated_graph(
        self,
        graph: ExecutionGraph | Mapping[str, Any],
        limits: GraphLimits | None,
    ) -> tuple[ExecutionGraph, GraphLimits]:
        parsed = (
            graph
            if isinstance(graph, ExecutionGraph)
            else ExecutionGraph.from_mapping(graph)
        )
        active_limits = limits or GraphLimits()
        structural = [
            issue
            for issue in validate_execution_graph(parsed, active_limits)
            if issue.code not in self._IGNORED_BUSINESS_LIMIT_CODES
        ]
        if structural:
            raise RuntimeError(
                "Invalid execution graph: "
                + "; ".join(
                    f"{issue.code}:{issue.message}" for issue in structural
                )
            )
        return parsed, active_limits

    def _recorded_call(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
        run: Any,
        call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]],
        budget: BudgetController,
        node: SelectedNode,
        kind: str,
    ) -> Any:
        self._ensure_feedback_state()
        with self._feedback_lock:
            if self._provider_account_blocked:
                return None

        # Explicit top-level no-tools enforcement wraps the actual model request
        # and raw response. openrouter_api enforces the same boundary again at
        # transport level, giving production a two-layer fail-closed guarantee.
        def guarded_call_fn(
            run_config: Any,
            payload: Mapping[str, Any],
        ) -> tuple[Mapping[str, Any], float]:
            assert_request_has_no_tools(payload)
            response, cost = call_fn(run_config, payload)
            assert_response_has_no_tools(response)
            return response, cost

        attempt = super()._recorded_call(
            selected,
            attempts,
            original_task,
            upstream,
            run,
            guarded_call_fn,
            budget,
            node,
            kind,
        )
        self._record_feedback(attempt)
        self._mark_provider_account_blocked(attempt)
        invalid = _legacy.FailureCategory.PROVIDER_INVALID_RESPONSE
        if attempt is not None and self._category(attempt) == invalid:
            budget.fail_endpoint(node.provider_endpoint, invalid)
        return attempt

    def _recover_node(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        recovery_rows: Sequence[Mapping[str, Any]],
        category: Any,
        best: tuple[Any, SelectedNode] | None,
        call: Callable[[SelectedNode, str], Any | None],
    ) -> tuple[Any | None, tuple[Any, SelectedNode] | None, SelectedNode]:
        """Use initial recovery, then replan standby promotion from live feedback."""
        last_node = selected
        eligible = set(self.recovery_policy.replace_categories)
        eligible.add(_legacy.FailureCategory.QUALITY_GATE_FAILED)
        if category not in eligible:
            return None, best, last_node

        source = attempts[-1] if attempts else None
        saturated = self._reasoning_saturated_attempt(source)
        for row in recovery_rows:
            candidate = self._candidate(row, selected)
            original = candidate
            candidate, adaptation = self._replacement_adaptation(
                candidate, source, saturated
            )
            attempted = call(candidate, "replacement")
            if attempted is None:
                continue
            if adaptation is not None:
                attempted.answer_transformations.append(adaptation)
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
            source = attempted
            saturated = self._reasoning_saturated_attempt(source)

        promotion_depth = self._dynamic_promotion_depth(attempts)
        for ordinal in range(1, promotion_depth + 1):
            row = self._claim_next_standby()
            if row is None:
                break
            candidate = self._candidate(row, selected)
            original = candidate
            source = attempts[-1] if attempts else source
            saturated = self._reasoning_saturated_attempt(source)
            candidate, adaptation = self._replacement_adaptation(
                candidate, source, saturated
            )
            attempted = call(candidate, "replacement")
            if attempted is not None and adaptation is not None:
                attempted.answer_transformations.append(adaptation)
            self._record_promotion_event(
                selected=selected,
                candidate=candidate,
                trigger_category=category,
                attempt=attempted,
                planned_depth=promotion_depth,
                ordinal=ordinal,
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
        return None, best, last_node

    def _execution_result(
        self,
        graph: ExecutionGraph,
        outputs: Mapping[str, Any],
        stage_records: Sequence[Mapping[str, Any]],
        budget: BudgetController,
        preflight: Mapping[str, Any],
        limits: GraphLimits,
        state: Mapping[str, Any],
        blockers: list[str],
        missing_non_degradable: list[str],
    ) -> dict[str, Any]:
        result = super()._execution_result(
            graph,
            outputs,
            stage_records,
            budget,
            preflight,
            limits,
            state,
            blockers,
            missing_non_degradable,
        )
        feedback = self._feedback_snapshot()
        result["runtime_feedback_replanning"] = feedback
        result["provider_account_transport_state"] = {
            "blocked": bool(feedback.get("provider_account_blocked")),
            "reason": str(feedback.get("provider_account_block_reason") or ""),
            "model_replacement_can_repair": False
            if feedback.get("provider_account_blocked")
            else None,
        }
        if feedback.get("provider_account_blocked") and result.get("status") == "failed":
            result["stop_reason"] = "provider-account-credit-insufficient"
        return result

    def execute_graph(
        self,
        graph: ExecutionGraph | Mapping[str, Any],
        run: Any,
        original_task: str,
        *,
        call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]] | None = None,
        output_dir: str | Any | None = None,
        limits: GraphLimits | None = None,
    ) -> dict[str, Any]:
        parsed = (
            graph
            if isinstance(graph, ExecutionGraph)
            else ExecutionGraph.from_mapping(graph)
        )
        self._initialize_feedback(parsed)
        return super().execute_graph(
            parsed,
            run,
            original_task,
            call_fn=call_fn,
            output_dir=output_dir,
            limits=limits,
        )


# Patch legacy module globals because legacy classes resolve these names at
# runtime. This is an explicit compatibility bridge, not a production gate.
_legacy.RuntimeConfig = RuntimeConfig
_legacy.BudgetController = BudgetController
_legacy.ExecutionEngine = ExecutionEngine

__all__ = [name for name in globals() if not name.startswith("_")]
