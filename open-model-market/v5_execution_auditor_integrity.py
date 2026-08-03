#!/usr/bin/env python3
"""Audit native V5 execution with semantic, evidence, and company integrity."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import v5_execution_auditor as base
from v5_json_io import load_json_or_default
from v5_provider_lock import canonical_provider_lock
from v5_quality_status_integrity import (
    DEGRADED_SUCCESS_STATUSES,
    STRICT_SUCCESS_STATUSES,
)
PLANNING_FAILURE_PREFIXES = (
    "BUDGET_INSUFFICIENT_",
    "CAPABILITY_",
    "CANDIDATE_GENERATION_",
    "PLANNING_",
)
_OBSOLETE_COMPANY_FAILURE_PREFIXES = (
    "actual successful model-company audit is missing or failed",
    "actual model-company audit did not recompute from resolved models",
    "actual successful model-company evidence does not cover every node:",
    "actual successful model companies are not globally unique",
    "actual successful model company identity is unresolved",
)


def _planning_failure(root: Path) -> dict[str, Any] | None:
    error = load_json_or_default(root / "expert-team-error.json", {})
    error = error if isinstance(error, Mapping) else {}
    report = load_json_or_default(root / "v5-planning-infeasibility.json", {})
    report = report if isinstance(report, Mapping) else {}
    code = str(error.get("error_code") or report.get("code") or "")
    message = str(error.get("message") or report.get("message") or "")
    if not code.startswith(PLANNING_FAILURE_PREFIXES):
        return None
    return {
        "code": code,
        "stage": str(error.get("stage") or "planning"),
        "message": message,
        "retryable": bool(error.get("retryable")),
        "infeasibility_report_present": bool(report),
        "model_calls_performed": int(report.get("model_calls_performed") or 0),
        "fallback_used": bool(report.get("fallback_used")),
    }


def _gate_failures(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    attempts = row.get("attempts", [])
    if not isinstance(attempts, list):
        return []
    failures: list[dict[str, Any]] = []
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            continue
        raw_reasons = attempt.get("gate_reasons", [])
        reasons = [str(value) for value in raw_reasons] if isinstance(raw_reasons, list) else []
        status = str(attempt.get("status") or "")
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


def _classify_quality_row(
    row: Mapping[str, Any],
) -> tuple[str, str | dict[str, Any]]:
    node_id = str(row.get("node_id") or "")
    status = str(row.get("status") or "")
    contract = row.get("contract", {})
    contract_complete = (
        isinstance(contract, Mapping)
        and contract.get("required_fields_complete") is True
    )
    if status in STRICT_SUCCESS_STATUSES and contract_complete:
        return "strict", node_id
    if status in DEGRADED_SUCCESS_STATUSES or status.startswith("success"):
        return "degraded", {
            "node_id": node_id,
            "status": status,
            "quality_score": float(row.get("quality_score") or 0.0),
            "gate_failures": _gate_failures(row),
            "contract_incomplete": not contract_complete,
        }
    return "failed", node_id


def _node_quality(root: Path) -> dict[str, Any]:
    raw_rows = load_json_or_default(root / "v5-node-results.json", [])
    rows = raw_rows if isinstance(raw_rows, list) else []
    strict: list[str] = []
    degraded: list[dict[str, Any]] = []
    failed: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        kind, value = _classify_quality_row(row)
        if kind == "strict":
            strict.append(str(value))
        elif kind == "degraded":
            degraded.append(dict(value))
        else:
            failed.append(str(value))
    return {
        "node_result_count": len(rows),
        "strict_node_ids": strict,
        "degraded_nodes": degraded,
        "failed_node_ids": failed,
        "contract_incomplete_node_ids": [
            row["node_id"] for row in degraded if row.get("contract_incomplete")
        ],
        "all_nodes_strict": bool(rows) and len(strict) == len(rows),
    }


def _normalize_planning_failure(
    root: Path,
    result: dict[str, Any],
    planning: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = load_json_or_default(root / "report-comments" / "report-comments-manifest.json", {})
    manifest = manifest if isinstance(manifest, Mapping) else {}
    publication_status = str(manifest.get("publication_status") or "missing")
    evidence_valid = (
        bool(planning.get("infeasibility_report_present"))
        and int(planning.get("model_calls_performed") or 0) == 0
        and not bool(planning.get("fallback_used"))
        and publication_status == "skipped_failed_execution"
    )
    code = str(planning.get("code") or "PLANNING_FAILED")
    message = str(planning.get("message") or "planning failed")
    checks = dict(result.get("checks") or {})
    checks.update(
        {
            "planning_failure": dict(planning),
            "planning_failure_evidence_valid": evidence_valid,
            "downstream_execution_stages_applicable": False,
            "report_publication_status": publication_status,
            "model_calls": int(planning.get("model_calls_performed") or 0),
        }
    )
    result["checks"] = checks
    result["stage_status"] = {
        **dict(result.get("stage_status") or {}),
        "runtime": "FAIL_CLOSED_PRE_EXECUTION",
        "requests": "NOT_APPLICABLE",
        "graph": "INFEASIBLE",
        "report": (
            "SKIPPED_FAILED_EXECUTION"
            if publication_status == "skipped_failed_execution"
            else "FAIL"
        ),
    }
    result["failures"] = [
        f"planning failed before model calls: {code}: {message}"
    ] + ([] if evidence_valid else ["planning failure evidence chain is incomplete or inconsistent"])
    result["degradations"] = []
    result["status"] = "FAIL"
    result["primary_failure"] = {
        "code": code,
        "stage": str(planning.get("stage") or "planning"),
        "message": message,
        "retryable": bool(planning.get("retryable")),
    }
    return result


def _constitutional_evidence(root: Path) -> dict[str, Any]:
    constraints = load_json_or_default(root / "task-constraints.json", {})
    constraints = constraints if isinstance(constraints, Mapping) else {}
    evidence = load_json_or_default(root / "evidence-integrity.json", {})
    evidence = evidence if isinstance(evidence, Mapping) else {}
    company = load_json_or_default(root / "actual-model-company-audit.json", {})
    company = company if isinstance(company, Mapping) else {}

    called = company.get("all_called_models", [])
    called = called if isinstance(called, list) else []
    duplicate_called = company.get("duplicate_called_companies_across_nodes", {})
    duplicate_called = duplicate_called if isinstance(duplicate_called, Mapping) else {}
    unresolved = company.get("unresolved_called_companies", [])
    unresolved = unresolved if isinstance(unresolved, list) else []

    failures: list[str] = []
    if constraints.get("schema_version") != "v5-task-constraints-1":
        failures.append("structured task constraints are missing")
    if constraints.get("fail_closed") is not True:
        failures.append("task constraints are not fail-closed")
    if constraints.get("external_tools_allowed") is not False:
        failures.append("expert external-tool prohibition is not explicit")
    if evidence.get("status") != "PASS":
        failures.append("semantic evidence integrity gate is missing or failed")
    if evidence.get("fact_truth_not_inferred_from_structure") is not True:
        failures.append("fact truth is still inferred from structural completeness")
    if company.get("status") != "PASS":
        failures.append("all-call model-company audit is missing or failed")
    if company.get("policy") != "recompute-from-all-actual-called-models":
        failures.append("company audit does not include every actual call")
    if company.get("failed_calls_are_included") is not True:
        failures.append("failed model calls are excluded from company uniqueness")
    if not called:
        failures.append("all-called-model evidence is empty")
    if duplicate_called:
        failures.append("a model company was reused across different nodes")
    if unresolved:
        failures.append("one or more actual called companies are unresolved")

    return {
        "failures": failures,
        "checks": {
            "task_constraints": dict(constraints),
            "evidence_integrity_status": evidence.get("status"),
            "evidence_violations": evidence.get("violations", []),
            "actual_model_company_audit_status": company.get("status"),
            "actual_model_company_audit_policy": company.get("policy"),
            "actual_called_model_count": len(called),
            "duplicate_called_companies_across_nodes": dict(duplicate_called),
            "unresolved_called_companies": unresolved,
            "failed_calls_are_included": company.get("failed_calls_are_included"),
        },
    }



def _history_isolation_evidence(
    result: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    disabled = result.get("cross_task_history_used") is False
    failures = [] if disabled else [
        "expert-team-result does not prove cross_task_history_used=false"
    ]
    return disabled, failures


def _final_delivery_contract_evidence(
    graph: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    final_ids = {
        str(value)
        for value in graph.get("final_nodes", [])
        if str(value)
    }
    raw_nodes = graph.get("nodes", [])
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            failures.append("execution graph contains a non-object node")
            continue
        node_id = str(node.get("node_id") or "")
        expected = node_id in final_ids
        contract = node.get("output_contract")
        observed = (
            contract.get("final_delivery_node")
            if isinstance(contract, Mapping)
            else None
        )
        rows.append(
            {
                "node_id": node_id,
                "expected_final_delivery_node": expected,
                "observed_final_delivery_node": observed,
            }
        )
        if observed is not expected:
            failures.append(
                "graph output_contract final_delivery_node mismatch: "
                + (node_id or "missing-node-id")
            )
    return rows, failures


def _provider_lock_evidence(
    request_audit: Mapping[str, Any],
) -> tuple[list[bool], list[str]]:
    raw_requests = request_audit.get("requests", [])
    requests = raw_requests if isinstance(raw_requests, list) else []
    rows: list[bool] = []
    failures: list[str] = []
    for index, request in enumerate(requests, 1):
        valid = isinstance(request, Mapping) and canonical_provider_lock(request)
        rows.append(valid)
        if not valid:
            failures.append(
                f"request {index} provider.only/order lock is missing or inconsistent"
            )
    return rows, failures


def _report_preparation_evidence(
    report_manifest: Mapping[str, Any],
) -> tuple[str, str, Any, list[str]]:
    status = str(report_manifest.get("report_comment_preparation_status") or "")
    mode = str(report_manifest.get("report_comment_preparation_mode") or "")
    issue_required = report_manifest.get("issue_context_required")
    valid = (
        status == "PASS"
        and mode == "deterministic-files"
        and issue_required is False
    )
    failures = [] if valid else [
        "deterministic report-comment preparation receipt is missing"
    ]
    return status, mode, issue_required, failures


def _native_phase_contract_evidence(root: Path) -> dict[str, Any]:
    result = load_json_or_default(root / "expert-team-result.json", {})
    result = result if isinstance(result, Mapping) else {}
    graph = load_json_or_default(root / "v5-execution-graph.json", {})
    graph = graph if isinstance(graph, Mapping) else {}
    request_audit = load_json_or_default(root / "request-audit.json", {})
    request_audit = request_audit if isinstance(request_audit, Mapping) else {}
    report_manifest = load_json_or_default(
        root / "report-comments" / "report-comments-manifest.json",
        {},
    )
    report_manifest = report_manifest if isinstance(report_manifest, Mapping) else {}

    history_disabled, history_failures = _history_isolation_evidence(result)
    node_rows, node_failures = _final_delivery_contract_evidence(graph)
    request_rows, request_failures = _provider_lock_evidence(request_audit)
    report_status, report_mode, issue_required, report_failures = (
        _report_preparation_evidence(report_manifest)
    )
    return {
        "failures": [
            *history_failures,
            *node_failures,
            *request_failures,
            *report_failures,
        ],
        "checks": {
            "cross_task_history_used": result.get("cross_task_history_used"),
            "cross_task_history_disabled": history_disabled,
            "final_delivery_node_contracts": node_rows,
            "provider_lock_contract": "provider.only/order-exact-single-endpoint",
            "provider_lock_rows_valid": request_rows,
            "legacy_provider_order_required": False,
            "report_comment_preparation_status": report_status,
            "report_comment_preparation_mode": report_mode,
            "issue_context_required": issue_required,
            "independent_artifact_revalidation_status": "PENDING_POST_UPLOAD",
        },
    }


def _quality_evidence_updates(
    node_evidence: Mapping[str, Any],
    completion_mode: str,
    quality_status: str,
    integrity: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    degradations: list[str] = []
    if node_evidence["contract_incomplete_node_ids"]:
        degradations.append(
            "one or more usable nodes did not satisfy the deterministic output contract"
        )
    if node_evidence["failed_node_ids"]:
        failures.append(
            "node-level execution failures are present: "
            + ", ".join(node_evidence["failed_node_ids"])
        )
    if node_evidence["degraded_nodes"]:
        if completion_mode != "degraded" or quality_status != "degraded_success":
            failures.append("degraded node output was incorrectly represented as full success")
        else:
            degradations.append(
                "one or more nodes delivered usable output after failing a quality gate"
            )
        if integrity.get("status") != "DEGRADED":
            failures.append("run-level quality integrity evidence is missing or inconsistent")
    elif node_evidence["all_nodes_strict"]:
        if completion_mode == "full" and quality_status != "full_success":
            failures.append("strict full completion is missing full_success quality status")
        if completion_mode == "full" and integrity.get("status") not in {"PASS", None}:
            failures.append("strict node success conflicts with run-level quality integrity")
    if quality_status == "full_success" and node_evidence["degraded_nodes"]:
        failures.append("full_success is forbidden when a node is success_degraded")
    return failures, degradations


def _quality_checks(
    quality_status: str,
    integrity: Mapping[str, Any],
    node_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "quality_status": quality_status,
        "quality_integrity_status": integrity.get("status"),
        "strict_node_count": len(node_evidence["strict_node_ids"]),
        "degraded_node_count": len(node_evidence["degraded_nodes"]),
        "failed_node_count": len(node_evidence["failed_node_ids"]),
        "contract_incomplete_node_count": len(
            node_evidence["contract_incomplete_node_ids"]
        ),
        "node_quality_evidence": node_evidence,
    }


def _finalize_audit_result(
    result: dict[str, Any],
    failures: list[str],
    degradations: list[str],
) -> dict[str, Any]:
    result["failures"] = list(dict.fromkeys(failures))
    result["degradations"] = list(dict.fromkeys(degradations))
    if result["failures"]:
        result["status"] = "FAIL"
        result["primary_failure"] = {
            "code": "V5_PRODUCTION_AUDIT_FAILED",
            "stage": "v5-production-audit",
            "message": result["failures"][0],
            "retryable": False,
        }
    elif result["degradations"]:
        result["status"] = "DEGRADED"
        result["primary_failure"] = {
            "code": "DEGRADED_SUCCESS",
            "stage": "quality-integrity",
            "message": result["degradations"][0],
            "retryable": False,
        }
    else:
        result["status"] = "PASS"
        result["primary_failure"] = {
            "code": "NONE",
            "stage": "completed",
            "message": "",
            "retryable": False,
        }
    return result


def audit(
    root: Path,
    *,
    execute_outcome: str,
    publish_outcome: str,
) -> dict[str, Any]:
    planning = _planning_failure(root)
    result = base.audit(
        root,
        execute_outcome=execute_outcome,
        publish_outcome=publish_outcome,
    )
    if planning is not None:
        return _normalize_planning_failure(root, result, planning)

    summary = load_json_or_default(root / "v5-execution-summary.json", {})
    summary = summary if isinstance(summary, Mapping) else {}
    node_evidence = _node_quality(root)
    constitutional = _constitutional_evidence(root)
    phase_contract = _native_phase_contract_evidence(root)
    failures = [
        reason
        for reason in list(result.get("failures") or [])
        if not reason.startswith(_OBSOLETE_COMPANY_FAILURE_PREFIXES)
    ]
    failures.extend(constitutional["failures"])
    failures.extend(phase_contract["failures"])
    degradations = list(result.get("degradations") or [])
    completion_mode = str(summary.get("completion_mode") or "")
    quality_status = str(summary.get("quality_status") or "")
    integrity = summary.get("quality_integrity", {})
    integrity = integrity if isinstance(integrity, Mapping) else {}

    quality_failures, quality_degradations = _quality_evidence_updates(
        node_evidence, completion_mode, quality_status, integrity
    )
    failures.extend(quality_failures)
    degradations.extend(quality_degradations)

    checks = dict(result.get("checks") or {})
    checks.update(constitutional["checks"])
    checks.update(phase_contract["checks"])
    checks.update(_quality_checks(quality_status, integrity, node_evidence))
    result["checks"] = checks
    stage_status = dict(result.get("stage_status") or {})
    stage_status["independent_artifact_revalidation"] = "PENDING_POST_UPLOAD"
    result["stage_status"] = stage_status
    return _finalize_audit_result(result, failures, degradations)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--execute-outcome", default="unknown")
    parser.add_argument("--publish-outcome", default="unknown")
    args = parser.parse_args()
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    result = audit(
        root,
        execute_outcome=args.execute_outcome,
        publish_outcome=args.publish_outcome,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    (root / "execution-audit.json").write_text(serialized, encoding="utf-8")
    (root / "execution-diagnosis.json").write_text(serialized, encoding="utf-8")
    base._write_output("status", result["status"])
    base._write_output(
        "reason",
        "; ".join(result["failures"] or result["degradations"]),
    )
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
