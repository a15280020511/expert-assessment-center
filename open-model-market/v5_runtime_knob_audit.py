"""Audit that planned runtime knobs reached real model execution."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "runtime-knob-coverage-audit-3-resource-closure"
_REQUIRED_RESOURCE_SURFACES = (
    "prompt-shape-budgeting",
    "resource-efficiency-balance",
    "output-transport-allowance",
    "model-timeout-effective",
)


def _rows(value: Any) -> list[Mapping[str, Any]]:
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


def _timeout_binding(attempt: Mapping[str, Any]) -> Mapping[str, Any]:
    transformations = _rows(attempt.get("answer_transformations"))
    for row in reversed(transformations):
        if row.get("type") == "dynamic-model-timeout-binding":
            return row
    return {}


def _attempt_rows(
    node_results: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for node in _rows(node_results or []):
        node_id = str(node.get("node_id") or "")
        for attempt in _rows(node.get("attempts")):
            request = attempt.get("request")
            if not isinstance(request, Mapping) or not str(
                request.get("model") or ""
            ).strip():
                continue
            flattened.append(
                {
                    "node_id": node_id,
                    "attempt_index": int(attempt.get("attempt_index") or 0),
                    "model": str(
                        attempt.get("model") or request.get("model") or ""
                    ),
                    "request": dict(request),
                    "timeout_binding": dict(_timeout_binding(attempt)),
                }
            )
    return flattened


def _resource_parameter_ids(node: Mapping[str, Any]) -> dict[str, str]:
    profile = node.get("parameter_profile")
    profile = profile if isinstance(profile, Mapping) else {}
    ids = profile.get("runtime_resource_parameter_ids")
    ids = ids if isinstance(ids, Mapping) else {}
    return {
        surface: str(ids.get(surface) or "").strip()
        for surface in _REQUIRED_RESOURCE_SURFACES
    }


def _resource_closure_required(graph: Mapping[str, Any]) -> bool:
    metadata = graph.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    # New production materialization always emits this field, including False.
    # Legacy/unit fixtures that predate the closure omit it and are audited only
    # for the knobs they actually declare.
    return "request_resource_parameter_profile_bound" in metadata


def audit_runtime_knob_coverage(
    graph: Mapping[str, Any],
    requests: Sequence[Mapping[str, Any]],
    node_results: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prove ParameterDesign values are bound and observed at real attempts."""
    request_rows = _rows(requests)
    nodes = _rows(graph.get("nodes"))
    closure_required = _resource_closure_required(graph)
    computed_but_unused: list[dict[str, Any]] = []
    reasoning_bindings: list[dict[str, Any]] = []
    resource_parameter_bindings: list[dict[str, Any]] = []

    for node in nodes:
        node_id = str(node.get("node_id") or "")
        model = str(node.get("model") or "")
        profile = node.get("reasoning_profile")
        planned = (
            str(profile.get("effort") or "").casefold()
            if isinstance(profile, Mapping)
            else ""
        )
        matches = [
            row
            for row in request_rows
            if str(row.get("model") or "") == model
        ]
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

        ids = _resource_parameter_ids(node)
        missing_ids = [surface for surface, value in ids.items() if not value]
        resource_parameter_bindings.append(
            {
                "node_id": node_id,
                "model": model,
                "parameter_ids": ids,
                "required_surfaces": list(_REQUIRED_RESOURCE_SURFACES),
                "required_for_this_graph": closure_required,
                "status": (
                    "PASS"
                    if not missing_ids
                    else "FAIL"
                    if closure_required
                    else "NOT_EVALUATED"
                ),
                "missing_parameter_surfaces": missing_ids,
            }
        )
        if closure_required:
            for surface in missing_ids:
                computed_but_unused.append(
                    {
                        "node_id": node_id,
                        "model": model,
                        "parameter": surface,
                        "reason": (
                            "first-class-resource-ParameterSpec-id-not-bound-to-node"
                        ),
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

    attempts = _attempt_rows(node_results)
    timeout_bindings: list[dict[str, Any]] = []
    for row in attempts:
        binding = row["timeout_binding"]
        try:
            effective_timeout = int(
                binding.get("effective_timeout_seconds") or 0
            )
        except (TypeError, ValueError):
            effective_timeout = 0
        try:
            safety_cap = int(binding.get("safety_cap_seconds") or 0)
        except (TypeError, ValueError):
            safety_cap = 0
        parameter_id = str(binding.get("timeout_parameter_id") or "").strip()
        valid = bool(
            binding
            and binding.get("status") == "PASS"
            and effective_timeout > 0
            and safety_cap > 0
            and effective_timeout <= safety_cap
            and (parameter_id or not closure_required)
        )
        timeout_bindings.append(
            {
                "node_id": row["node_id"],
                "attempt_index": row["attempt_index"],
                "model": row["model"],
                "timeout_parameter_id": parameter_id or None,
                "effective_timeout_seconds": effective_timeout,
                "safety_cap_seconds": safety_cap,
                "status": "PASS" if valid else "FAIL",
            }
        )
        if not valid:
            computed_but_unused.append(
                {
                    "node_id": row["node_id"],
                    "attempt_index": row["attempt_index"],
                    "model": row["model"],
                    "parameter": "dynamic-model-timeout",
                }
            )

    timeout_status = (
        "PASS"
        if attempts and all(
            row["status"] == "PASS" for row in timeout_bindings
        )
        else "FAIL"
        if node_results is not None
        else "NOT_EVALUATED"
    )
    status = "PASS" if request_rows and not computed_but_unused else "FAIL"
    if node_results is not None and timeout_status != "PASS":
        status = "FAIL"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "planned_node_count": len(nodes),
        "request_count": len(request_rows),
        "attempt_count": len(attempts),
        "reasoning_binding_count": sum(
            1 for row in reasoning_bindings if row["status"] == "PASS"
        ),
        "request_resource_parameter_closure_required": closure_required,
        "request_resource_parameter_node_count": sum(
            1
            for row in resource_parameter_bindings
            if row["status"] == "PASS"
        ),
        "requests_with_dynamic_output_allowance": sum(
            1
            for row in request_binding_rows
            if row["dynamic_output_allowance_tokens"] > 0
        ),
        "requests_with_reasoning_binding": sum(
            1 for row in request_binding_rows if row["reasoning_effort"]
        ),
        "attempts_with_dynamic_timeout_binding": sum(
            1 for row in timeout_bindings if row["status"] == "PASS"
        ),
        "dynamic_timeout_binding_status": timeout_status,
        "reasoning_bindings": reasoning_bindings,
        "resource_parameter_bindings": resource_parameter_bindings,
        "request_bindings": request_binding_rows,
        "timeout_bindings": timeout_bindings,
        "computed_but_unused": computed_but_unused,
        "all_request_resource_controls_first_class_parameters": closure_required,
        "parameter_design_to_runtime_binding_required": closure_required,
        "output_allowance_is_task_admission_gate": False,
        "output_allowance_is_result_validity_gate": False,
        "timeout_safety_cap_is_business_gate": False,
        "token_and_cost_soft_control": True,
        "cost_effectiveness_priority": True,
        "cross_task_history_used": False,
    }


__all__ = ["SCHEMA_VERSION", "audit_runtime_knob_coverage"]
