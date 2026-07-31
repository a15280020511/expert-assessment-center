"""Bounded recovery for empty or transient provider responses.

A provider can occasionally return a successful HTTP envelope with no answer,
response ID, usage, or billed cost. When the ticket reserves recovery capacity,
V5 should use exactly one same-endpoint retry before considering a replacement.
When recovery is zero, fail-closed behavior is unchanged.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

import v5_executor as executor
import v5_r8_executor as runtime
from execution_graph import SelectedNode

_INSTALLED = False
_RETRY_SAME_ENDPOINT = {
    "transient_provider",
    "empty_output",
    "rate_limited",
}


def fault_aware_execute_node(
    selected: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
    run: Any,
    call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]],
    recovery_rows: Sequence[Mapping[str, Any]],
    budget: runtime.R8ExecutionBudget,
) -> executor.NodeExecutionResult:
    attempts: list[executor.NodeAttempt] = []
    best: tuple[executor.NodeAttempt, SelectedNode] | None = None
    last_attempted = selected

    def call(node: SelectedNode, kind: str) -> executor.NodeAttempt | None:
        nonlocal last_attempted
        if not budget.endpoint_available(node.provider_endpoint):
            return None
        attempt = executor._reserved_attempt(
            node,
            selected.node_id,
            kind,
            original_task,
            upstream,
            run,
            call_fn,
            len(attempts) + 1,
            budget,
        )
        if attempt is not None:
            last_attempted = node
            attempts.append(attempt)
            failure = runtime._failure_class(attempt, node)
            if failure in {
                "rate_limited",
                "transient_provider",
                "empty_output",
                "truncated_output",
                "invalid_json",
            }:
                budget.fail_endpoint(node.provider_endpoint, failure)
        return attempt

    initial = call(selected, "initial")
    if initial is not None and initial.status == "passed" and runtime._strict_json_valid(selected, initial.answer):
        return runtime._node_result(selected, selected, attempts, initial, "success")
    if runtime._degraded_usable(selected, initial):
        best = (initial, selected)

    failure = runtime._failure_class(initial, selected)
    if failure in _RETRY_SAME_ENDPOINT:
        retried = call(selected, "retry")
        if retried is not None and retried.status == "passed" and runtime._strict_json_valid(selected, retried.answer):
            return runtime._node_result(selected, selected, attempts, retried, "success_retried")
        if runtime._degraded_usable(selected, retried) and (
            best is None or retried.quality_score > best[0].quality_score
        ):
            best = (retried, selected)

    alternatives = [runtime._candidate(row, selected) for row in recovery_rows]
    alternatives.sort(
        key=lambda node: (
            runtime._provider(node) == runtime._provider(selected),
            node.failure_probability,
            node.estimated_cost,
            -node.estimated_quality,
        )
    )
    for replacement in alternatives:
        attempted = call(replacement, "replacement")
        if attempted is None:
            continue
        if attempted.status == "passed" and runtime._strict_json_valid(replacement, attempted.answer):
            return runtime._node_result(selected, replacement, attempts, attempted, "success_recovered")
        if runtime._degraded_usable(replacement, attempted) and (
            best is None or attempted.quality_score > best[0].quality_score
        ):
            best = (attempted, replacement)

    if best is not None:
        return runtime._node_result(selected, best[1], attempts, best[0], "success_degraded")
    return executor.NodeExecutionResult(
        node_id=selected.node_id,
        assigned_work=selected.assigned_work,
        status="failed",
        selected_model=selected.model,
        resolved_model=last_attempted.model,
        provider_endpoint=last_attempted.provider_endpoint,
        answer=None,
        quality_score=0.0,
        attempts=attempts,
        actual_cost_usd=round(
            sum(executor._actual_cost({"usage": row.usage}) for row in attempts),
            8,
        ),
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    runtime.fault_aware_execute_node = fault_aware_execute_node
    _INSTALLED = True
