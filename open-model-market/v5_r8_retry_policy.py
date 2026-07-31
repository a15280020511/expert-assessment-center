"""Delay circuit opening until one approved retry also fails."""
from __future__ import annotations

from typing import Any

import v5_r8_executor as runtime

_INSTALLED = False
_ORIGINAL_FAIL_ENDPOINT = runtime.R8ExecutionBudget.fail_endpoint
_ORIGINAL_SNAPSHOT = runtime.R8ExecutionBudget.snapshot
_RETRYABLE_PROVIDER_FAILURES = {
    "transient_provider",
    "empty_output",
    "rate_limited",
}


def fail_endpoint_after_retry(
    self: runtime.R8ExecutionBudget,
    endpoint: str,
    reason: str,
) -> None:
    if reason not in _RETRYABLE_PROVIDER_FAILURES:
        _ORIGINAL_FAIL_ENDPOINT(self, endpoint, reason)
        return

    with self._lock:
        pending = getattr(self, "_retryable_failure_counts", None)
        if pending is None:
            pending = {}
            setattr(self, "_retryable_failure_counts", pending)
        key = f"{endpoint}|{reason}"
        count = int(pending.get(key, 0)) + 1
        pending[key] = count
        self.endpoint_failure_reasons.setdefault(endpoint, []).append(
            f"{reason}-retry-pending" if count == 1 else reason
        )
        # The first retryable response anomaly remains available for the one
        # globally approved recovery call. A second failure opens the circuit.
        if count >= 2:
            self.endpoint_failures[endpoint] = self.endpoint_failures.get(endpoint, 0) + 1


def snapshot_with_retryable_state(self: runtime.R8ExecutionBudget) -> dict[str, Any]:
    snapshot = _ORIGINAL_SNAPSHOT(self)
    with self._lock:
        pending = dict(getattr(self, "_retryable_failure_counts", {}))
        snapshot["provider_circuit"]["retryable_failure_counts"] = pending
        # Compatibility field retained for existing audit consumers.
        snapshot["provider_circuit"]["transient_failure_counts"] = {
            key.split("|", 1)[0]: value
            for key, value in pending.items()
            if key.endswith("|transient_provider")
        }
    return snapshot


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    runtime.R8ExecutionBudget.fail_endpoint = fail_endpoint_after_retry
    runtime.R8ExecutionBudget.snapshot = snapshot_with_retryable_state
    _INSTALLED = True
