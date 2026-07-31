"""Enforce one approved total-call ceiling with one shared recovery pool."""
from __future__ import annotations

from typing import Any

import v5_executor as executor
import v5_r8_executor as r8

_INSTALLED = False


def _maximum_total_calls(self: Any) -> int:
    """The approved total is never expanded by recovery allowances."""
    return max(0, int(self.max_planned_calls))


def _maximum_recovery_calls(self: Any) -> int:
    """Retry and replacement are alternate uses of the same approved reserve."""
    return max(0, int(self.max_retries), int(self.max_replacements))


def _recovery_calls_reserved(self: Any) -> int:
    return max(0, int(self.retries_reserved)) + max(0, int(self.replacements_reserved))


def _maximum_initial_calls(self: Any) -> int:
    return max(0, _maximum_total_calls(self) - _maximum_recovery_calls(self))


def _reserve(self: Any, kind: str, estimated_cost_usd: float, node_id: str) -> tuple[bool, str]:
    risk = max(0.0, float(estimated_cost_usd)) * self.risk_multiplier
    with self._lock:
        reason = ""
        if kind == "initial" and self.initial_calls_reserved >= _maximum_initial_calls(self):
            reason = "initial-call-cap-reserved-for-recovery"
        elif kind in {"retry", "replacement"} and _recovery_calls_reserved(self) >= _maximum_recovery_calls(self):
            reason = "global-recovery-limit-exhausted"
        elif self.calls_reserved >= _maximum_total_calls(self):
            reason = "global-total-call-limit-exhausted"
        else:
            protected = sum(self.protected_final.values()) - self.protected_final.get(node_id, 0.0)
            projected = self.actual_cost_usd + sum(self.pending) + risk + protected
            if self.max_budget_usd is not None and projected > self.max_budget_usd + 1e-12:
                reason = "global-risk-adjusted-budget-exhausted"
        if reason:
            self.denials.append({
                "node_id": node_id,
                "kind": kind,
                "estimated_cost_usd": round(float(estimated_cost_usd), 8),
                "risk_adjusted_cost_usd": round(risk, 8),
                "reason": reason,
            })
            return False, reason
        self.calls_reserved += 1
        self.pending.append(risk)
        self.protected_final.pop(node_id, None)
        if kind == "initial":
            self.initial_calls_reserved += 1
        elif kind == "retry":
            self.retries_reserved += 1
        else:
            self.replacements_reserved += 1
        return True, ""


def _snapshot(self: Any) -> dict[str, Any]:
    with self._lock:
        recovery_reserved = _recovery_calls_reserved(self)
        return {
            "max_planned_calls": self.max_planned_calls,
            "maximum_initial_calls": _maximum_initial_calls(self),
            "maximum_recovery_calls": _maximum_recovery_calls(self),
            "max_retries": self.max_retries,
            "max_replacements": self.max_replacements,
            "maximum_total_calls": _maximum_total_calls(self),
            "max_budget_usd": self.max_budget_usd,
            "risk_multiplier": self.risk_multiplier,
            "calls_reserved": self.calls_reserved,
            "initial_calls_reserved": self.initial_calls_reserved,
            "recovery_calls_reserved": recovery_reserved,
            "retries_reserved": self.retries_reserved,
            "replacements_reserved": self.replacements_reserved,
            "estimated_cost_reserved_usd": round(sum(self.pending), 8),
            "protected_final_cost_usd": round(sum(self.protected_final.values()), 8),
            "actual_cost_usd": round(self.actual_cost_usd, 8),
            "denials": list(self.denials),
            "provider_circuit": {
                "max_failures": self.max_provider_failures,
                "failures": dict(self.endpoint_failures),
                "reasons": {
                    key: list(value)
                    for key, value in self.endpoint_failure_reasons.items()
                },
            },
        }


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    cls = r8.R8ExecutionBudget
    cls.maximum_total_calls = property(_maximum_total_calls)
    cls.maximum_recovery_calls = property(_maximum_recovery_calls)
    cls.recovery_calls_reserved = property(_recovery_calls_reserved)
    cls.maximum_initial_calls = property(_maximum_initial_calls)
    cls.reserve = _reserve
    cls.snapshot = _snapshot
    executor.ExecutionBudget = cls
    _INSTALLED = True
