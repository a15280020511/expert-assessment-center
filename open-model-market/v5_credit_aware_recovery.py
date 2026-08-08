"""Credit-aware OpenRouter recovery without reintroducing free-first gates.

Production A/B runs established three separate runtime facts:

* gov-312 / Expert #410: a paid model can fail with an account-credit HTTP 402
  while the signed candidate space still contains explicit ``:free`` models;
* gov-313 / Expert #412: zero-cost recovery works, but the first two free models
  can fail quickly with HTTP 404 because no endpoint matches the account's
  guardrail/privacy/data policy;
* a one-shot free recovery depth is not sufficient continuous replanning: every
  real transport observation must update the remaining current-run candidate
  space and the next promotion decision.

This layer changes only post-failure recovery semantics:

* normal selection remains fully dynamic and unrestricted; there is no free-first
  eligibility rule;
* after an actual account-credit 402, paid follow-up calls remain suppressed;
* the runtime performs a best-effort authenticated ``/api/v1/models/user`` GET,
  documented by OpenRouter as filtered by the current user's provider preferences,
  privacy settings and guardrails; this call is transport metadata, not a model
  call and never relaxes account privacy;
* explicit zero-cost candidates already present in the current signed
  recovery/standby space are prioritized by that live compatibility view when it
  is available; the live view never adds an unsigned candidate;
* if live compatibility metadata is unavailable, recovery remains permissive and
  learns from actual free-candidate responses;
* after every privacy/data-policy 404 the remaining zero-cost space and dynamic
  promotion depth are recomputed from current-run time/space feedback;
* non-privacy failures remain bounded by the current dynamic recovery depth so a
  sequence of slow timeouts cannot fan out through the entire free pool;
* a 402 on a zero-cost candidate stops the zero-cost path immediately;
* if no final answer exists, the authoritative terminal reason preserves the 402
  account failure instead of relabeling it as an evidence/quantity failure.

No state survives the task, no token/cost threshold becomes an admission gate, and
Provider routing remains unrestricted OpenRouter.
"""
from __future__ import annotations

import math
import os
from dataclasses import replace
from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode
import openrouter_api
import v5_task_scope_quality_circuit as task_scope
from v5_runtime import FailureCategory, ProductionRuntime


USER_MODELS_URL = "https://openrouter.ai/api/v1/models/user"
_CONTROL_SCOPE_PATCH = (
    "固定职业关键词路由",
    "关键词路由",
)
_PRIVACY_404_MARKERS = (
    "no endpoints available matching your guardrail restrictions and data policy",
    "guardrail restrictions and data policy",
    "privacy settings",
    "data policy",
)


def zero_cost_candidate_row(row: Mapping[str, Any]) -> bool:
    """Recognize explicit zero-cost runtime candidates conservatively."""
    marker = row.get("zero_cost_candidate")
    if isinstance(marker, bool):
        return marker
    model = str(row.get("model") or "").strip().casefold()
    return model.endswith(":free")


def _resource_pressure(node: SelectedNode) -> float | None:
    profile = (
        node.parameter_profile
        if isinstance(node.parameter_profile, Mapping)
        else {}
    )
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
    *,
    transport_feedback_pressure: float = 0.0,
) -> int:
    """Derive finite fallback depth from current task, live space and feedback."""
    count = max(0, int(candidate_count))
    if count <= 0:
        return 0
    spatial_base = math.sqrt(count)
    resource = _resource_pressure(node)
    feedback = min(1.0, max(0.0, float(transport_feedback_pressure)))
    if resource is None:
        pressure = max(feedback, 1.0 / max(1.0, spatial_base))
    else:
        floor = 1.0 / max(1.0, spatial_base)
        pressure = max(floor, resource, feedback)
    effective = math.ceil(spatial_base * pressure)
    return min(count, max(1, effective))


def _failure_mapping(attempt: Any) -> Mapping[str, Any]:
    failure = getattr(attempt, "failure", None)
    if isinstance(failure, Mapping):
        return failure
    if failure is None:
        return {}
    result: dict[str, Any] = {}
    for key in (
        "category",
        "http_status",
        "message",
        "response_diagnostics",
        "retryable",
    ):
        value = getattr(failure, key, None)
        if value is not None:
            result[key] = value
    return result


def _attempt_http_status(attempt: Any) -> int:
    failure = _failure_mapping(attempt)
    try:
        return int(failure.get("http_status") or 0)
    except (TypeError, ValueError):
        return 0


def _attempt_failure_text(attempt: Any) -> str:
    failure = _failure_mapping(attempt)
    pieces = [str(failure.get("message") or "")]
    diagnostics = failure.get("response_diagnostics")
    if isinstance(diagnostics, Mapping):
        pieces.extend(str(value) for value in diagnostics.values())
    return " ".join(pieces).casefold()


def privacy_policy_endpoint_unavailable(attempt: Any) -> bool:
    """Recognize the observed OpenRouter account-policy endpoint 404."""
    if attempt is None or _attempt_http_status(attempt) != 404:
        return False
    text = _attempt_failure_text(attempt)
    return any(marker in text for marker in _PRIVACY_404_MARKERS)


def _policy_value(row: Mapping[str, Any]) -> bool | None:
    value = row.get("user_policy_compatible")
    return value if isinstance(value, bool) else None


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
            self._credit_zero_cost_replan_count = 0
            self._credit_privacy_404_count = 0
            self._credit_known_policy_incompatible_skipped = 0
            self._credit_user_policy_model_ids: set[str] | None = None
            self._credit_user_policy_probe_done = False
            self._credit_user_policy_probe_audit: dict[str, Any] = {}

    def _initialize_feedback(self, graph: Any) -> None:
        super()._initialize_feedback(graph)
        self._ensure_credit_recovery_state()
        with self._feedback_lock:
            self._credit_zero_cost_events = []
            self._credit_zero_cost_attempt_models = []
            self._credit_zero_cost_second_402 = False
            self._credit_zero_cost_candidate_count = 0
            self._credit_zero_cost_dynamic_depth = 0
            self._credit_zero_cost_replan_count = 0
            self._credit_privacy_404_count = 0
            self._credit_known_policy_incompatible_skipped = 0
            self._credit_user_policy_model_ids = None
            self._credit_user_policy_probe_done = False
            self._credit_user_policy_probe_audit = {}

    def _probe_user_policy_models(self) -> set[str] | None:
        """Best-effort current-account model compatibility probe after 402 only."""
        self._ensure_credit_recovery_state()
        with self._feedback_lock:
            if self._credit_user_policy_probe_done:
                return (
                    set(self._credit_user_policy_model_ids)
                    if self._credit_user_policy_model_ids is not None
                    else None
                )
            self._credit_user_policy_probe_done = True

        api_key = str(os.getenv("OPENROUTER_API_KEY") or "").strip()
        if not api_key:
            audit = {
                "available": False,
                "reason": "missing-openrouter-api-key",
                "source": "openrouter-authenticated-models-user",
                "model_call": False,
                "privacy_policy_changed": False,
            }
            with self._feedback_lock:
                self._credit_user_policy_probe_audit = audit
            return None
        try:
            payload = openrouter_api.request_json(
                USER_MODELS_URL,
                api_key,
                timeout_seconds=20,
                max_retries=1,
                payload=None,
            )
            rows = payload.get("data") if isinstance(payload, Mapping) else None
            if not isinstance(rows, list):
                raise ValueError("OpenRouter /models/user payload has no data list")
            model_ids = {
                str(row.get("id") or "").strip()
                for row in rows
                if isinstance(row, Mapping)
                and str(row.get("id") or "").strip()
            }
            audit = {
                "available": True,
                "source": "openrouter-authenticated-models-user",
                "model_count": len(model_ids),
                "model_call": False,
                "privacy_policy_changed": False,
                "used_as_normal_candidate_gate": False,
                "used_only_after_actual_credit_402": True,
            }
            with self._feedback_lock:
                self._credit_user_policy_model_ids = set(model_ids)
                self._credit_user_policy_probe_audit = audit
            return model_ids
        except Exception as exc:  # noqa: BLE001 - metadata probe cannot gate recovery
            audit = {
                "available": False,
                "source": "openrouter-authenticated-models-user",
                "error_type": type(exc).__name__,
                "model_call": False,
                "privacy_policy_changed": False,
                "used_as_normal_candidate_gate": False,
                "used_only_after_actual_credit_402": True,
            }
            with self._feedback_lock:
                self._credit_user_policy_probe_audit = audit
            return None

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
                "user_policy_compatible": _policy_value(row),
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
        profile = (
            node.parameter_profile
            if isinstance(node.parameter_profile, Mapping)
            else {}
        )
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
            prior_blocked = bool(
                getattr(self, "_provider_account_blocked", False)
            )
            prior_reason = str(
                getattr(self, "_provider_account_block_reason", "") or ""
            )
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

    def _compatibility_rank(
        self,
        row: Mapping[str, Any],
        live_model_ids: set[str] | None,
    ) -> int:
        model = str(row.get("model") or "").strip()
        if live_model_ids is not None:
            return 0 if model in live_model_ids else 2
        row_value = _policy_value(row)
        if row_value is True:
            return 0
        if row_value is False:
            return 2
        return 1

    def _available_zero_cost_space(
        self,
        recovery_rows: Sequence[Mapping[str, Any]],
        attempts: Sequence[Any],
        live_model_ids: set[str] | None,
    ) -> list[tuple[str, dict[str, Any]]]:
        attempted_models = {
            str(getattr(attempt, "model", "") or "").strip()
            for attempt in attempts
            if str(getattr(attempt, "model", "") or "").strip()
        }
        result: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        known_incompatible = 0
        for source, rows in (
            ("initial-recovery", recovery_rows),
            ("standby", getattr(self, "_standby_inventory", [])),
        ):
            for raw in rows:
                if (
                    not isinstance(raw, Mapping)
                    or not zero_cost_candidate_row(raw)
                ):
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
                rank = self._compatibility_rank(raw, live_model_ids)
                if rank == 2:
                    known_incompatible += 1
                    continue
                row = dict(raw)
                row["current_user_policy_compatibility_rank"] = rank
                result.append((source, row))
        result.sort(
            key=lambda item: (
                int(item[1].get("current_user_policy_compatibility_rank", 1)),
                float(item[1].get("cost_rank_signal") or 0.0),
                str(item[1].get("model") or ""),
            )
        )
        with self._feedback_lock:
            self._credit_known_policy_incompatible_skipped = max(
                self._credit_known_policy_incompatible_skipped,
                known_incompatible,
            )
        return result

    def _transport_feedback_pressure(self) -> float:
        with self._feedback_lock:
            attempts = len(self._credit_zero_cost_attempt_models)
            privacy = int(self._credit_privacy_404_count)
        if attempts <= 0:
            return 0.0
        return min(1.0, privacy / attempts)

    def _record_credit_replan(
        self,
        *,
        candidate_count: int,
        depth: int,
        feedback_pressure: float,
        live_model_ids: set[str] | None,
    ) -> None:
        with self._feedback_lock:
            self._credit_zero_cost_replan_count += 1
            self._credit_zero_cost_candidate_count = max(
                self._credit_zero_cost_candidate_count,
                candidate_count,
            )
            self._credit_zero_cost_dynamic_depth = depth
            self._credit_zero_cost_events.append(
                {
                    "event_type": "provider-credit-zero-cost-recovery-replanned",
                    "replan_epoch": self._credit_zero_cost_replan_count,
                    "remaining_candidate_count": candidate_count,
                    "dynamic_depth": depth,
                    "transport_feedback_pressure": round(feedback_pressure, 6),
                    "user_policy_live_probe_available": live_model_ids is not None,
                    "depth_fixed": False,
                    "recompute_trigger": (
                        "current-signed-space-plus-latest-free-transport-feedback"
                    ),
                    "normal_free_first_gate_enabled": False,
                    "paid_followup_suppressed": True,
                    "cross_task_history_used": False,
                }
            )

    def _recover_zero_cost_after_credit_failure(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        recovery_rows: Sequence[Mapping[str, Any]],
        best: tuple[Any, SelectedNode] | None,
        call: Any,
    ) -> tuple[Any | None, tuple[Any, SelectedNode] | None, SelectedNode]:
        self._ensure_credit_recovery_state()
        live_model_ids = self._probe_user_policy_models()
        last_node = selected
        nonprivacy_failures = 0
        initial_nonprivacy_limit: int | None = None
        ordinal = 0

        while True:
            candidate_space = self._available_zero_cost_space(
                recovery_rows,
                attempts,
                live_model_ids,
            )
            if not candidate_space:
                break
            feedback_pressure = self._transport_feedback_pressure()
            depth = dynamic_zero_cost_recovery_depth(
                selected,
                len(candidate_space),
                transport_feedback_pressure=feedback_pressure,
            )
            if initial_nonprivacy_limit is None:
                initial_nonprivacy_limit = depth
            self._record_credit_replan(
                candidate_count=len(candidate_space),
                depth=depth,
                feedback_pressure=feedback_pressure,
                live_model_ids=live_model_ids,
            )

            source_kind, row = candidate_space[0]
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
            ordinal += 1
            category = (
                self._category(attempted)
                if attempted is not None
                else None
            )
            privacy_404 = privacy_policy_endpoint_unavailable(attempted)
            with self._feedback_lock:
                self._credit_zero_cost_attempt_models.append(model)
                if privacy_404:
                    self._credit_privacy_404_count += 1
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
                            category.value
                            if category is not None
                            else None
                        ),
                        "http_status": (
                            _attempt_http_status(attempted)
                            if attempted is not None
                            else None
                        ),
                        "privacy_policy_endpoint_unavailable": privacy_404,
                        "current_user_policy_compatibility_rank": row.get(
                            "current_user_policy_compatibility_rank"
                        ),
                        "replan_after_attempt": True,
                        "cross_task_history_used": False,
                    }
                )
            if attempted is None:
                nonprivacy_failures += 1
            elif attempted.status == "passed":
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
            else:
                best = self._better_degraded(
                    best,
                    attempted,
                    candidate,
                    self._degraded_usable(candidate, attempted),
                )
                if category == FailureCategory.BUDGET_INSUFFICIENT:
                    with self._feedback_lock:
                        self._credit_zero_cost_second_402 = True
                        self._credit_zero_cost_events.append(
                            {
                                "event_type": (
                                    "provider-credit-zero-cost-path-blocked"
                                ),
                                "model": model,
                                "reason": (
                                    "zero-cost-candidate-also-returned-http-402"
                                ),
                                "further_zero_cost_attempts_suppressed": True,
                            }
                        )
                    break
                if not privacy_404:
                    nonprivacy_failures += 1

            # Privacy/data-policy 404 is a fast endpoint-compatibility observation,
            # so it explicitly triggers another current-run replan. Other failure
            # categories remain bounded by the initially computed current-task
            # depth to avoid turning a free pool into a long timeout fan-out.
            if (
                not privacy_404
                and initial_nonprivacy_limit is not None
                and nonprivacy_failures >= initial_nonprivacy_limit
            ):
                with self._feedback_lock:
                    self._credit_zero_cost_events.append(
                        {
                            "event_type": "provider-credit-zero-cost-path-bounded",
                            "reason": "nonprivacy-failure-dynamic-depth-reached",
                            "dynamic_limit": initial_nonprivacy_limit,
                            "fixed_limit": False,
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
                "candidate_detection": (
                    "explicit-zero-cost-marker-or-openrouter-free-model-id"
                ),
                "candidate_count": int(self._credit_zero_cost_candidate_count),
                "dynamic_depth": int(self._credit_zero_cost_dynamic_depth),
                "depth_fixed": False,
                "continuous_replan_count": int(
                    self._credit_zero_cost_replan_count
                ),
                "attempt_models": list(self._credit_zero_cost_attempt_models),
                "attempt_count": len(self._credit_zero_cost_attempt_models),
                "privacy_policy_404_count": int(self._credit_privacy_404_count),
                "known_user_policy_incompatible_skipped": int(
                    self._credit_known_policy_incompatible_skipped
                ),
                "user_policy_probe": dict(
                    self._credit_user_policy_probe_audit
                ),
                "zero_cost_candidate_also_returned_402": bool(
                    self._credit_zero_cost_second_402
                ),
                "events": [dict(row) for row in self._credit_zero_cost_events],
                "paid_followup_suppressed_after_credit_402": True,
                "privacy_policy_relaxed_or_overridden": False,
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
    "USER_MODELS_URL",
    "dynamic_zero_cost_recovery_depth",
    "install_credit_aware_recovery",
    "privacy_policy_endpoint_unavailable",
    "zero_cost_candidate_row",
]
