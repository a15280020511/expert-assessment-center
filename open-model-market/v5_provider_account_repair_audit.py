"""Clarify provider-account repairability after signed zero-cost recovery exists.

The base runtime correctly marks the paid OpenRouter account as blocked after an
insufficient-credit 402, but its historical audit field says model replacement can
never repair that condition.  Since #411/#413, that is too broad: paid replacement
remains blocked, while a signed zero-cost candidate can repair the transport path.

This layer changes audit semantics only.  It does not change recovery selection,
call eligibility, Provider routing, privacy settings, or task outcome.
"""
from __future__ import annotations

from typing import Any, Mapping

from v5_credit_aware_recovery import CreditAwareTaskScopedExecutionEngine
from v5_runtime import ProductionRuntime


def refine_provider_account_transport_state(result: Mapping[str, Any]) -> dict[str, Any]:
    """Split paid-account repairability from signed zero-cost transport repair."""
    updated = dict(result)
    transport_raw = updated.get("provider_account_transport_state")
    transport = dict(transport_raw) if isinstance(transport_raw, Mapping) else {}
    if not transport.get("blocked"):
        return updated

    feedback_raw = updated.get("runtime_feedback_replanning")
    feedback = feedback_raw if isinstance(feedback_raw, Mapping) else {}
    credit_raw = feedback.get("provider_credit_zero_cost_recovery")
    credit = credit_raw if isinstance(credit_raw, Mapping) else {}
    candidate_count = int(credit.get("candidate_count") or 0)
    events = credit.get("events")
    events = events if isinstance(events, list) else []
    transport_repair_observed = any(
        isinstance(event, Mapping)
        and event.get("event_type") == "provider-credit-zero-cost-recovery-attempt"
        and str(event.get("attempt_status") or "") in {"passed", "quality_gate_failed"}
        for event in events
    )
    zero_cost_available = candidate_count > 0

    transport.update(
        {
            "paid_model_replacement_can_repair": False,
            "signed_zero_cost_recovery_available": zero_cost_available,
            "signed_zero_cost_transport_repair_observed": transport_repair_observed,
            "model_replacement_can_repair": (
                True
                if transport_repair_observed
                else (None if zero_cost_available else False)
            ),
            "repairability_scope": (
                "paid-account-blocked-but-current-signed-zero-cost-path-distinguished"
            ),
            "privacy_policy_relaxed_or_overridden": False,
            "cross_task_history_used": False,
        }
    )
    updated["provider_account_transport_state"] = transport
    return updated


class ProviderAccountRepairAuditExecutionEngine(CreditAwareTaskScopedExecutionEngine):
    """Refine transport-state audit after the full inherited execution result."""

    def _execution_result(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = super()._execution_result(*args, **kwargs)
        return refine_provider_account_transport_state(result)


def install_provider_account_repair_audit(runtime: ProductionRuntime) -> ProductionRuntime:
    runtime.execution_engine = ProviderAccountRepairAuditExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


__all__ = [
    "ProviderAccountRepairAuditExecutionEngine",
    "install_provider_account_repair_audit",
    "refine_provider_account_transport_state",
]
