"""Credit-aware OpenRouter recovery without reintroducing free-first gates.

Production A/B run gov-312-expert / Expert #410 proved that a paid candidate can
receive an account-level OpenRouter HTTP 402 even though the current governance
candidate pool still contains explicit ``:free`` models.  The prior runtime opened
an account circuit that suppressed *all* subsequent calls, so a zero-cost model
could never repair the transport condition.  The same run also showed that the
constitutional evidence gate could overwrite the already-known 402 root cause
with ``unsupported-evidence-or-quantity`` simply because no model answer existed.

This layer changes only post-failure recovery semantics:

* normal selection remains fully dynamic and unrestricted; there is no free-first
  eligibility rule;
* after an actual account-credit 402, paid follow-up calls remain suppressed;
* explicit OpenRouter ``:free`` candidates already present in the current signed
  recovery/standby space may be tried in their current-task order;
* the number of zero-cost attempts is derived from current candidate-space size and
  the current task resource pressure rather than a fixed call count;
* a second 402 on a zero-cost candidate stops the zero-cost path immediately;
* if no final answer exists, the authoritative terminal reason preserves the 402
  account failure instead of relabeling it as an evidence/quantity failure.

No state survives the task, no token/cost threshold becomes an admission gate, and
Provider routing remains unrestricted OpenRouter.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode
import v5_task_scope_quality_circuit as task_scope
from v5_runtime import FailureCategory, ProductionRuntime


_CONTROL_SCOPE_PATCH = (
    "固定职业关键词路由",
    "关键词路由",
)


def zero_cost_candidate_row(row: Mapping[str, Any]) -> bool:
    """Recognize explicit zero-cost runtime candidates conservatively.

    Current execution-graph standby materialization does not yet preserve all raw
    catalog price fields, so a generic ``estimated_cost == 0`` is unsafe: run #410
    demonstrated that paid standby rows can currently carry that placeholder.  An
    explicit ``zero_cost_candidate`` marker is preferred when present; otherwise
    the OpenRouter ``:free`` model identity is the authoritative signal.
    """
    marker = row.get("zero_cost_candidate")
    if isinstance(marker, bool):
        return marker
    model = str(row.get("model") or "").strip().casefold()
    return model.endswith(":free")


def _resource_pressure(node: SelectedNode) -> float | None:
    profile = node.parameter_profile if isinstance(node.parameter_profile, Mapping) else {}
    values = profile.get("runtime_resource_parameter_values")
    values = values if isinstance(values, Mapping) else {}
    balance = values.get("resource-efficiency-balance")
    balance = balance if isinstance(balance, Mapping) else {}
    try:
        pressure = float(balance.get("overall_pressure"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pressure):
        return None
    return min(1.0, max(0.0, pressure))


def dynamic_zero_cost_recovery_depth(
    node: SelectedNode,
    candidate_count: int,
) -> int:
    """Derive finite fallback depth from current task pressure and live space."""
    count = max(0, int(candidate_count))
    if count <= 0:
        return 0
    spatial_base = math.sqrt(count)
    pressure = _resource_pressure(node)
    if pressure is None:
        effective = math.ceil(spatial_base)
    else:
        # The lower bound is itself candidate-space-derived, avoiding a hidden
        # fixed medium/default pressure while still allowing one repair attempt.
        floor = 1.0 / max(1.0, spatial_base)
        effective = math.ceil(spatial_base * max(floor, pressure))
    return min(count, max(1, effective))


class CreditAwareTaskScopedExecutionEngine(
    task_scope.TaskScopedCostEffectiveExecutionEngine
):
    """Permit only explicit zero-cost recovery after an account-credit 402."""

    def _ensure_credit_recovery_state(self) -> None:
        self._ensure_quality_circuit_state()
        if not hasattr(self, "_credit_zero_cost_events"):
            self._credit_zero_cost_events: list[dict[str, Any]] = []
            self._credit_zero_cost_attempt_models: list[str] = []
            self._credit_zero_cost_second_402 = False
            self._credit_zero_cost_candidate_count = 0
            self._credit_zero_cost_dynamic_depth = 0

    def _initialize_feedback(self, graph: Any) -> None:
        super()._initialize_feedback(graph)
        self._ensure_credit_recovery_state()
        with self._feedback_lock:
            self._credit_zero_cost_events = []
            self._credit_zero_cost_attempt_models = []
            self._credit_zero_cost_second_402 = False
            self._credit_zero_cost_candidate_count = 0
            self._credit_zero_cost_dynamic_depth = 0

    @staticmethod
    def _zero_cost_selected_node(
        row: Mapping[str, Any],
        selected: SelectedNode,
        candidate: SelectedNode,
    ) -> SelectedNode:
        profile = dict(candidate.parameter_profile)
        profile.update(
            {
                "provider_account_zero_cost_recovery": True,
                "provider_account_zero_cost_recovery_source": (
                    "current-signed-candidate-space-after-http-402"
                ),
                "cross_task_history_used": False,
            }
        )
        return replace(
            candidate,
            estimated_cost=0.0,
            parameter_profile=profile,
            provider_endpoint=(
                str(row.get("provider_endpoint") or "").strip()
                or f"{candidate.model}@openrouter-auto"
            ),
        )

    def _recorded_call(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
        run: Any,
        call_fn: Any,
        budget: Any,
        node: SelectedNode,
        kind: str,
    ) -> Any:
        profile = node.parameter_profile if isinstance(node.parameter_profile, Mapping) else {}
        zero_cost_credit_recovery = bool(
            profile.get("provider_account_zero_cost_recovery")
        )
        if not zero_cost_credit_recovery:
            return super()._recorded_call(
                selected,
                attempts,
                original_task,
                upstream,
                run,
                call_fn,
                budget,
                node,
                kind,
            )

        self._ensure_credit_recovery_state()
        with self._feedback_lock:
            prior_blocked = bool(getattr(self, "_provider_account_blocked", False))
            prior_reason = str(
                getattr(self, "_provider_account_block_reason", "") or ""
            )
            # Temporarily bypass the blanket account circuit for this explicitly
            # free candidate only. Paid candidates still hit the inherited guard.
            if prior_blocked:
                self._provider_account_blocked = False

        try:
            attempt = super()._recorded_call(
                selected,
                attempts,
                original_task,
                upstream,
                run,
                call_fn,
                budget,
                node,
                kind,
            )
        finally:
            with self._feedback_lock:
                # Preserve the paid-account block even if the free call succeeds or
                # fails for a non-credit reason. If the free call itself got 402,
                # the inherited transport layer has already reopened the circuit.
                if prior_blocked and not self._provider_account_blocked:
                    self._provider_account_blocked = True
                    self._provider_account_block_reason = prior_reason
        return attempt

    def _claim_zero_cost_standby(self, row: Mapping[str, Any]) -> bool:
        model = str(row.get("model") or "").strip()
        if not model or not zero_cost_candidate_row(row):
            return False
        self._ensure_credit_recovery_state()
        with self._feedback_lock:
            if (
                model in self._standby_claimed
                or model in self._hard_failed_model_ids
            ):
                return False
            self._standby_claimed.add(model)
            return True

    def _available_zero_cost_space(
        self,
        recovery_rows: Sequence[Mapping[str, Any]],
        attempts: Sequence[Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        attempted_models = {
            str(getattr(attempt, "model", "") or "").strip()
            for attempt in attempts
            if str(getattr(attempt, "model", "") or "").strip()
        }
        result: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        for source, rows in (
            ("initial-recovery", recovery_rows),
            ("standby", getattr(self, "_standby_inventory", [])),
        ):
            for raw in rows:
                if not isinstance(raw, Mapping) or not zero_cost_candidate_row(raw):
                    continue
                model = str(raw.get("model") or "").strip()
                if (
                    not model
                    or model in seen
                    or model in attempted_models
                    or model in self._hard_failed_model_ids
                    or model in self._standby_claimed
                ):
                    continue
                seen.add(model)
                result.append((source, dict(raw)))
        return result

    def _recover_zero_cost_after_credit_failure(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        recovery_rows: Sequence[Mapping[str, Any]],
        best: tuple[Any, SelectedNode] | None,
        call: Any,
    ) -> tuple[Any | None, tuple[Any, SelectedNode] | None, SelectedNode]:
        self._ensure_credit_recovery_state()
        candidate_space = self._available_zero_cost_space(recovery_rows, attempts)
        depth = dynamic_zero_cost_recovery_depth(selected, len(candidate_space))
        with self._feedback_lock:
            self._credit_zero_cost_candidate_count = len(candidate_space)
            self._credit_zero_cost_dynamic_depth = depth
            self._credit_zero_cost_events.append(
                {
                    "event_type": "provider-credit-zero-cost-recovery-planned",
                    "candidate_count": len(candidate_space),
                    "dynamic_depth": depth,
                    "depth_fixed": False,
                    "candidate_source": "current-signed-recovery-plus-standby-space",
                    "normal_free_first_gate_enabled": False,
                    "paid_followup_suppressed": True,
                    "cross_task_history_used": False,
                }
            )

        last_node = selected
        for ordinal, (source_kind, row) in enumerate(candidate_space[:depth], 1):
            model = str(row.get("model") or "").strip()
            if source_kind == "standby" and not self._claim_zero_cost_standby(row):
                continue
            candidate = self._zero_cost_selected_node(
                row,
                selected,
                self._candidate(row, selected),
            )
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
            last_node = candidate
            with self._feedback_lock:
                self._credit_zero_cost_attempt_models.append(model)
                self._credit_zero_cost_events.append(
                    {
                        "event_type": "provider-credit-zero-cost-recovery-attempt",
                        "ordinal": ordinal,
                        "model": model,
                        "source": source_kind,
                        "attempt_status": str(
                            getattr(attempted, "status", "not-called")
                        ),
                        "failure_category": (
                            self._category(attempted).value
                            if attempted is not None
                            else None
                        ),
                        "cross_task_history_used": False,
                    }
                )
            if attempted is None:
                continue
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

            best = self._better_degraded(
                best,
                attempted,
                candidate,
                self._degraded_usable(candidate, attempted),
            )
            if self._category(attempted) == FailureCategory.BUDGET_INSUFFICIENT:
                with self._feedback_lock:
                    self._credit_zero_cost_second_402 = True
                    self._credit_zero_cost_events.append(
                        {
                            "event_type": "provider-credit-zero-cost-path-blocked",
                            "model": model,
                            "reason": "zero-cost-candidate-also-returned-http-402",
                            "further_zero_cost_attempts_suppressed": True,
                        }
                    )
                break
        return None, best, last_node

    def _recover_node(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        recovery_rows: Sequence[Mapping[str, Any]],
        category: Any,
        best: tuple[Any, SelectedNode] | None,
        call: Any,
    ) -> tuple[Any | None, tuple[Any, SelectedNode] | None, SelectedNode]:
        self._ensure_credit_recovery_state()
        if (
            category == FailureCategory.BUDGET_INSUFFICIENT
            and bool(getattr(self, "_provider_account_blocked", False))
        ):
            return self._recover_zero_cost_after_credit_failure(
                selected,
                attempts,
                recovery_rows,
                best,
                call,
            )
        return super()._recover_node(
            selected,
            attempts,
            recovery_rows,
            category,
            best,
            call,
        )

    def _feedback_snapshot(self) -> dict[str, Any]:
        value = dict(super()._feedback_snapshot())
        self._ensure_credit_recovery_state()
        with self._feedback_lock:
            value["provider_credit_zero_cost_recovery"] = {
                "enabled": True,
                "trigger": "actual-openrouter-account-credit-402-only",
                "normal_free_first_gate_enabled": False,
                "candidate_detection": "explicit-zero-cost-marker-or-openrouter-free-model-id",
                "candidate_count": int(self._credit_zero_cost_candidate_count),
                "dynamic_depth": int(self._credit_zero_cost_dynamic_depth),
                "depth_fixed": False,
                "attempt_models": list(self._credit_zero_cost_attempt_models),
                "attempt_count": len(self._credit_zero_cost_attempt_models),
                "zero_cost_candidate_also_returned_402": bool(
                    self._credit_zero_cost_second_402
                ),
                "events": [dict(row) for row in self._credit_zero_cost_events],
                "paid_followup_suppressed_after_credit_402": True,
                "cost_or_token_threshold_used": False,
                "cross_task_history_used": False,
            }
        return value

    def _constitutional_failure_reason(
        self,
        result: Mapping[str, Any],
        company_audit: Mapping[str, Any],
        evidence_audit: Mapping[str, Any],
        constraints: Any,
    ) -> str | None:
        transport = result.get("provider_account_transport_state")
        transport = transport if isinstance(transport, Mapping) else {}
        if (
            result.get("status") == "failed"
            and not str(result.get("final_answer") or "").strip()
            and bool(transport.get("blocked"))
        ):
            return "provider-account-credit-insufficient"
        return super()._constitutional_failure_reason(
            result,
            company_audit,
            evidence_audit,
            constraints,
        )


def install_credit_aware_recovery(
    runtime: ProductionRuntime,
) -> ProductionRuntime:
    """Install post-402 zero-cost recovery after task-scoping hardening."""
    # Extend the already-tested task projection with the one control clause that
    # run #410 proved still leaked into the business-facing surface.
    existing = tuple(task_scope._CONTROL_CLAUSE_TOKENS)
    task_scope._CONTROL_CLAUSE_TOKENS = tuple(
        dict.fromkeys([*existing, *_CONTROL_SCOPE_PATCH])
    )
    runtime.execution_engine = CreditAwareTaskScopedExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


__all__ = [
    "CreditAwareTaskScopedExecutionEngine",
    "dynamic_zero_cost_recovery_depth",
    "install_credit_aware_recovery",
    "zero_cost_candidate_row",
]
