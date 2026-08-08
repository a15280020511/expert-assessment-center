"""Enforce consistency between node quality gates and run-level status.

Audited degraded output may be delivered, but it must never be represented as
``full_success``. A failed non-critical node may coexist with degraded success
only when the runtime delivery gate has already proved sufficient coverage,
strict successful content, no missing non-degradable work and a usable report.
A status-only shell such as "nothing was covered" is never usable delivery: an
audited degraded success requires positive work coverage and at least one strict
successful content node.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from v5_json_io import load_json_or_default, write_json
from v5_runtime_knob_audit import audit_runtime_knob_coverage

STRICT_SUCCESS_STATUSES = {
    "success",
    "success_retried",
    "success_recovered",
}
DEGRADED_SUCCESS_STATUSES = {"success_degraded"}


def _attempt_quality_failures(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    attempts = row.get("attempts", [])
    if not isinstance(attempts, list):
        return failures
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            continue
        status = str(attempt.get("status") or "")
        reasons = attempt.get("gate_reasons", [])
        reasons = [str(value) for value in reasons] if isinstance(reasons, list) else []
        if status == "quality_gate_failed" or reasons:
            failures.append(
                {
                    "attempt_index": int(attempt.get("attempt_index") or 0),
                    "status": status,
                    "gate_reasons": reasons,
                    "quality_score": float(attempt.get("quality_score") or 0.0),
                }
            )
    return failures


def _classify_node_rows(
    rows: list[Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    degraded_nodes: list[dict[str, Any]] = []
    strict_nodes: list[str] = []
    failed_nodes: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        node_id = str(row.get("node_id") or "")
        status = str(row.get("status") or "")
        attempt_failures = _attempt_quality_failures(row)
        contract = row.get("contract", {})
        contract_complete = (
            isinstance(contract, Mapping)
            and contract.get("required_fields_complete") is True
        )
        if status in STRICT_SUCCESS_STATUSES and contract_complete:
            strict_nodes.append(node_id)
        elif status in DEGRADED_SUCCESS_STATUSES or status.startswith("success"):
            degraded_nodes.append(
                {
                    "node_id": node_id,
                    "status": status,
                    "quality_score": float(row.get("quality_score") or 0.0),
                    "attempt_quality_failures": attempt_failures,
                    "contract_incomplete": not contract_complete,
                }
            )
        else:
            failed_nodes.append(node_id)
    return degraded_nodes, strict_nodes, failed_nodes


def _declares_degraded_status(result: Mapping[str, Any]) -> bool:
    return (
        result.get("status") == "success"
        and result.get("completion_mode") == "degraded"
        and result.get("quality_status") == "degraded_success"
    )


def _run_declares_audited_degradation(result: Mapping[str, Any]) -> bool:
    if not _declares_degraded_status(result):
        return False
    if not str(result.get("final_answer") or "").strip():
        return False
    delivery = result.get("delivery_policy")
    if not isinstance(delivery, Mapping):
        return False
    if delivery.get("allow_degraded_success") is not True:
        return False
    if delivery.get("blockers"):
        return False
    if delivery.get("missing_non_degradable_work_ids"):
        return False
    coverage = result.get("work_coverage")
    if not isinstance(coverage, Mapping):
        return False
    try:
        observed = float(coverage.get("coverage_ratio") or 0.0)
        minimum = float(coverage.get("minimum_degraded_coverage") or 1.0)
        strict_nodes = int(coverage.get("successful_content_nodes") or 0)
    except (TypeError, ValueError):
        return False
    return (
        observed > 0.0
        and observed + 1e-12 >= minimum
        and strict_nodes >= 1
    )


def _apply_degradation(
    normalized: dict[str, Any],
    degraded_nodes: list[dict[str, Any]],
    failed_nodes: list[str],
) -> None:
    normalized["completion_mode"] = "degraded"
    normalized["quality_status"] = "degraded_success"
    normalized["stop_reason"] = "audited-usable-delivery-with-disclosed-degradation"
    degradation = dict(normalized.get("degradation") or {})
    degradation.update(
        {
            "used": True,
            "mode": "audited-deterministic-successful-node-synthesis",
            "extra_model_calls": int(degradation.get("extra_model_calls") or 0),
            "degraded_node_ids": [row["node_id"] for row in degraded_nodes],
            "unavailable_node_ids": list(failed_nodes),
            "failed_or_degraded_items_disclosed": True,
            "full_success_claimed": False,
        }
    )
    normalized["degradation"] = degradation


def _provider_account_failure_reason(result: Mapping[str, Any]) -> str:
    transport = result.get("provider_account_transport_state")
    if not isinstance(transport, Mapping) or transport.get("blocked") is not True:
        return ""
    reason = str(transport.get("reason") or "").strip()
    if reason == "openrouter-http-402-insufficient-credits":
        return "provider-account-credit-insufficient"
    return "provider-account-unavailable" if reason else ""


def _reject_invalid_degraded_delivery(normalized: dict[str, Any]) -> None:
    """Convert an unaudited degraded-success claim into a real failure."""
    previous_answer = str(normalized.get("final_answer") or "").strip()
    provider_reason = _provider_account_failure_reason(normalized)
    normalized["status"] = "failed"
    normalized["completion_mode"] = "none"
    normalized["quality_status"] = "failed"
    normalized["stop_reason"] = (
        provider_reason or "degraded-delivery-without-usable-content"
    )
    normalized["final_answer"] = None

    delivery = normalized.get("delivery_policy")
    delivery = dict(delivery) if isinstance(delivery, Mapping) else {}
    blockers = [str(value) for value in delivery.get("blockers", [])]
    marker = "degraded-delivery-requires-positive-covered-work-and-strict-content"
    if marker not in blockers:
        blockers.append(marker)
    delivery["blockers"] = blockers
    normalized["delivery_policy"] = delivery

    degradation = normalized.get("degradation")
    degradation = dict(degradation) if isinstance(degradation, Mapping) else {}
    degradation.update(
        {
            "used": False,
            "rejected": True,
            "rejection_reason": marker,
            "status_shell_was_not_delivery": True,
            "previous_final_answer_was_nonempty": bool(previous_answer),
            "root_cause_preserved": bool(provider_reason),
            "full_success_claimed": False,
        }
    )
    normalized["degradation"] = degradation


def _integrity_status(
    degraded_nodes: list[dict[str, Any]],
    failed_nodes: list[str],
    all_nodes_strict: bool,
    run_degraded: bool,
    invalid_degraded: bool,
) -> str:
    if invalid_degraded:
        return "FAIL"
    if run_degraded or degraded_nodes:
        return "DEGRADED"
    if failed_nodes:
        return "FAIL"
    return "PASS" if all_nodes_strict else "UNKNOWN"


def enforce_result_integrity(result: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    raw_rows = normalized.get("node_results", [])
    rows = raw_rows if isinstance(raw_rows, list) else []
    degraded_nodes, strict_nodes, failed_nodes = _classify_node_rows(rows)
    all_nodes_strict = bool(rows) and len(strict_nodes) == len(rows)
    declared_degraded = _declares_degraded_status(normalized)
    run_degraded = _run_declares_audited_degradation(normalized)
    invalid_degraded = bool(declared_degraded and not run_degraded)

    if invalid_degraded:
        _reject_invalid_degraded_delivery(normalized)
    elif run_degraded or degraded_nodes:
        _apply_degradation(normalized, degraded_nodes, failed_nodes)
    elif all_nodes_strict and normalized.get("status") == "success":
        normalized["completion_mode"] = "full"
        normalized["quality_status"] = "full_success"

    normalized["quality_integrity"] = {
        "status": _integrity_status(
            degraded_nodes,
            failed_nodes,
            all_nodes_strict,
            run_degraded,
            invalid_degraded,
        ),
        "strict_success_statuses": sorted(STRICT_SUCCESS_STATUSES),
        "strict_node_ids": strict_nodes,
        "degraded_nodes": degraded_nodes,
        "failed_node_ids": failed_nodes,
        "audited_degraded_delivery": run_degraded,
        "invalid_degraded_success_rejected": invalid_degraded,
        "positive_work_coverage_required_for_degraded_success": True,
        "minimum_strict_content_nodes_for_degraded_success": 1,
        "failed_nodes_may_coexist_only_with_delivery_gate_pass": True,
        "full_success_allowed": all_nodes_strict and not degraded_nodes and not failed_nodes,
    }
    return normalized


def _rewrite_request_audit(root: Path, integrity_status: str) -> None:
    audit_path = root / "v5-request-audit.json"
    audit = load_json_or_default(audit_path, {})
    if not isinstance(audit, Mapping):
        return
    audit = dict(audit)
    requests = audit.get("requests", [])
    requests = requests if isinstance(requests, list) else []
    dynamic_allowance = any(
        isinstance(request, Mapping)
        and ("max_tokens" in request or "max_completion_tokens" in request)
        for request in requests
    )
    audit["bounded_output_allowance_sent"] = dynamic_allowance
    audit["dynamic_output_allowance_sent"] = dynamic_allowance
    audit["artificial_token_ceiling_sent"] = False
    audit["output_allowance_policy"] = (
        "current-request-derived-transport-reservation-not-task-admission-gate"
        if dynamic_allowance
        else "provider-default-no-explicit-allowance"
    )
    audit["quality_integrity_status"] = integrity_status
    audit["request_count"] = int(audit.get("request_count") or len(requests))

    graph = load_json_or_default(root / "v5-execution-graph.json", {})
    if not isinstance(graph, Mapping):
        graph = {}
    node_results = load_json_or_default(root / "v5-node-results.json", [])
    if not isinstance(node_results, list):
        node_results = []
    knob_audit = audit_runtime_knob_coverage(graph, requests, node_results)
    audit["runtime_knob_coverage"] = knob_audit
    audit["runtime_knob_coverage_status"] = knob_audit["status"]
    audit["computed_runtime_knob_but_unused_count"] = len(
        knob_audit["computed_but_unused"]
    )
    audit["dynamic_model_timeout_binding_status"] = knob_audit[
        "dynamic_timeout_binding_status"
    ]
    audit["attempts_with_dynamic_model_timeout_binding"] = knob_audit[
        "attempts_with_dynamic_timeout_binding"
    ]
    write_json(audit_path, audit)


def _rewrite_artifacts(root: Path, result: Mapping[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    write_json(
        root / "v5-execution-summary.json",
        {key: value for key, value in result.items() if key != "node_results"},
    )
    integrity = result.get("quality_integrity", {})
    status = integrity.get("status") if isinstance(integrity, Mapping) else "UNKNOWN"
    _rewrite_request_audit(root, str(status or "UNKNOWN"))


def _rewrite_failure_artifacts(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    summary = load_json_or_default(root / "v5-execution-summary.json", {})
    rows = load_json_or_default(root / "v5-node-results.json", [])
    if isinstance(summary, Mapping):
        summary = dict(summary)
        summary.setdefault("quality_status", "failed")
        summary["quality_integrity"] = {
            "status": "FAIL",
            "strict_success_statuses": sorted(STRICT_SUCCESS_STATUSES),
            "strict_node_ids": [],
            "degraded_nodes": [],
            "failed_node_ids": [
                str(row.get("node_id") or "")
                for row in rows
                if isinstance(row, Mapping)
                and str(row.get("status") or "") not in STRICT_SUCCESS_STATUSES
            ],
            "audited_degraded_delivery": False,
            "invalid_degraded_success_rejected": False,
            "full_success_allowed": False,
        }
        write_json(root / "v5-execution-summary.json", summary)
    _rewrite_request_audit(root, "FAIL")
