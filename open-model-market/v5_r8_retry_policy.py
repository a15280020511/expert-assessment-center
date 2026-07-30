"""Delay transient-provider circuit opening until the bounded retry also fails."""
from __future__ import annotations

from typing import Any

import v5_r8_executor as runtime

_INSTALLED = False
_ORIGINAL_FAIL_ENDPOINT = runtime.R8ExecutionBudget.fail_endpoint
_ORIGINAL_SNAPSHOT = runtime.R8ExecutionBudget.snapshot


def fail_endpoint_after_retry(
    self: runtime.R8ExecutionBudget,
    endpoint: str,
    reason: str,
) -> None:
    if reason != "transient_provider":
        _ORIGINAL_FAIL_ENDPOINT(self, endpoint, reason)
        return

    with self._lock:
        pending = getattr(self, "_transient_failure_counts", None)
        if pending is None:
            pending = {}
            setattr(self, "_transient_failure_counts", pending)
        count = int(pending.get(endpoint, 0)) + 1
        pending[endpoint] = count
        self.endpoint_failure_reasons.setdefault(endpoint, []).append(
            "transient-provider-retry-pending" if count == 1 else reason
        )
        # The first transient failure is deliberately left available for exactly
        # the one globally bounded retry. The second failure opens the circuit.
        if count >= 2:
            self.endpoint_failures[endpoint] = self.endpoint_failures.get(endpoint, 0) + 1


def snapshot_with_transient_state(self: runtime.R8ExecutionBudget) -> dict[str, Any]:
    snapshot = _ORIGINAL_SNAPSHOT(self)
    with self._lock:
        snapshot["provider_circuit"]["transient_failure_counts"] = dict(
            getattr(self, "_transient_failure_counts", {})
        )
    return snapshot


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    runtime.R8ExecutionBudget.fail_endpoint = fail_endpoint_after_retry
    runtime.R8ExecutionBudget.snapshot = snapshot_with_transient_state
    _INSTALLED = True
