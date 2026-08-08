"""Audit that planned runtime knobs reached real model requests."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "runtime-knob-coverage-audit-1"


def _requests(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _positive_allowance(request: Mapping[str, Any]) -> int:
    for key in ("max_tokens", "max_completion_tokens"):
        try:
            value = int(request.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0


def _reasoning_effort(request: Mapping[str, Any]) -> str:
    reasoning = request.get("reasoning")
    if isinstance(reasoning, Mapping):
        return str(reasoning.get("effort") or "").casefold()
    return str(request.get("reasoning_effort") or "").casefold()


def audit_runtime_knob_coverage(
    graph: Mapping[str, Any],
    requests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Prove planned reasoning and dynamic output shaping were consumed."""
    request_rows = _requests(requests)
    nodes = _requests(graph.get("nodes"))
    computed_but_unused: list[dict[str, Any]] = []
    reasoning_bindings: list[dict[str, Any]] = []

    for node in nodes:
        node_id = str(node.get("node_id") or "")
        model = str(node.get("model") or "")
        profile = node.get("reasoning_profile")
        planned = (
            str(profile.get("effort") or "").casefold()
            if isinstance(profile, Mapping)
            else ""
        )
        matches = [row for row in request_rows if str(row.get("model") or "") == model]
        effective = next(
            (
                effort
                for effort in (_reasoning_effort(row) for row in matches)
                if effort
            ),
            "",
        )
        passed = bool(planned and effective == planned)
        reasoning_bindings.append(
            {
                "node_id": node_id,
                "model": model,
                "planned": planned or None,
                "effective": effective or None,
                "status": "PASS" if passed else "FAIL",
            }
        )
        if not passed:
            computed_but_unused.append(
                {
                    "node_id": node_id,
                    "parameter": "role-reasoning-effort",
                    "planned": planned or None,
                    "effective": effective or None,
                }
            )

    request_binding_rows: list[dict[str, Any]] = []
    for index, request in enumerate(request_rows, 1):
        allowance = _positive_allowance(request)
        effort = _reasoning_effort(request)
        missing: list[str] = []
        if allowance <= 0:
            missing.append("dynamic-output-allowance")
        if not effort:
            missing.append("reasoning-effort")
        request_binding_rows.append(
            {
                "request_index": index,
                "model": str(request.get("model") or ""),
                "reasoning_effort": effort or None,
                "dynamic_output_allowance_tokens": allowance,
                "status": "PASS" if not missing else "FAIL",
                "missing": missing,
            }
        )
        for parameter in missing:
            computed_but_unused.append(
                {
                    "request_index": index,
                    "model": str(request.get("model") or ""),
                    "parameter": parameter,
                }
            )

    status = "PASS" if request_rows and not computed_but_unused else "FAIL"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "planned_node_count": len(nodes),
        "request_count": len(request_rows),
        "reasoning_binding_count": sum(
            1 for row in reasoning_bindings if row["status"] == "PASS"
        ),
        "requests_with_dynamic_output_allowance": sum(
            1 for row in request_binding_rows if row["dynamic_output_allowance_tokens"] > 0
        ),
        "requests_with_reasoning_binding": sum(
            1 for row in request_binding_rows if row["reasoning_effort"]
        ),
        "reasoning_bindings": reasoning_bindings,
        "request_bindings": request_binding_rows,
        "computed_but_unused": computed_but_unused,
        "output_allowance_is_task_admission_gate": False,
        "output_allowance_is_result_validity_gate": False,
        "cross_task_history_used": False,
    }


__all__ = ["SCHEMA_VERSION", "audit_runtime_knob_coverage"]
