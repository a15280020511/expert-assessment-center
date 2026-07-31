#!/usr/bin/env python3
"""Audit native V5 production execution with node-level quality integrity."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import v5_execution_auditor as base
from v5_quality_status_integrity import (
    DEGRADED_SUCCESS_STATUSES,
    STRICT_SUCCESS_STATUSES,
)

NATIVE_RUNTIME_VERSION = "v5-native-runtime-1"
NATIVE_EXECUTOR = "v5-native-execution-engine"
LEGACY_RUNTIME_FAILURE = "V5 production result envelope is missing"
LEGACY_EXECUTOR_FAILURE = "R8 fault-aware executor evidence is missing"
PLANNING_FAILURE_PREFIXES = (
    "BUDGET_INSUFFICIENT_",
    "CAPABILITY_",
    "CANDIDATE_GENERATION_",
    "PLANNING_",
)


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _planning_failure(root: Path) -> dict[str, Any] | None:
    error = _load(root / "expert-team-error.json", {})
    error = error if isinstance(error, Mapping) else {}
    report = _load(root / "v5-planning-infeasibility.json", {})
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


def _node_quality(root: Path) -> dict[str, Any]:
    rows = _load(root / "v5-node-results.json", [])
    rows = rows if isinstance(rows, list) else []
    strict: list[str] = []
    degraded: list[dict[str, Any]] = []
    failed: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        node_id = str(row.get("node_id") or "")
        status = str(row.get("status") or "")
        contract = row.get("contract", {})
        contract_complete = (
            isinstance(contract, Mapping)
            and contract.get("required_fields_complete") is True
        )
        if status in STRICT_SUCCESS_STATUSES and contract_complete:
            strict.append(node_id)
        elif status in DEGRADED_SUCCESS_STATUSES or status.startswith("success"):
            attempts = row.get("attempts", [])
            gate_failures = []
            if isinstance(attempts, list):
                for attempt in attempts:
                    if not isinstance(attempt, Mapping):
                        continue
                    reasons = attempt.get("gate_reasons", [])
                    reasons = [str(value) for value in reasons] if isinstance(reasons, list) else []
                    if str(attempt.get("status") or "") == "quality_gate_failed" or reasons:
                        gate_failures.append(
                            {
                                "attempt_index": int(attempt.get("attempt_index") or 0),
                                "status": str(attempt.get("status") or ""),
                                "gate_reasons": reasons,
                                "quality_score": float(attempt.get("quality_score") or 0.0),
                            }
                        )
            degraded.append(
                {
                    "node_id": node_id,
                    "status": status,
                    "quality_score": float(row.get("quality_score") or 0.0),
                    "gate_failures": gate_failures,
                    "contract_incomplete": not contract_complete,
                }
            )
        else:
            failed.append(node_id)
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


def _apply_native_contract(
    root: Path,
    result: dict[str, Any],
    planning_failure: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Replace obsolete R8-name checks with the formal native contract."""
    envelope = _load(root / "expert-team-result.json", {})
    envelope = envelope if isinstance(envelope, Mapping) else {}
    summary = _load(root / "v5-execution-summary.json", {})
    summary = summary if isinstance(summary, Mapping) else {}
    runtime = _load(root / "production-runtime.json", {})
    runtime = runtime if isinstance(runtime, Mapping) else {}

    runtime_versions = {
        str(envelope.get("runtime_version") or ""),
        str(runtime.get("runtime_version") or ""),
    }
    runtime_versions.discard("")
    executor = str(summary.get("executor") or envelope.get("executor") or "")
    failures = list(result.get("failures") or [])

    runtime_valid = runtime_versions == {NATIVE_RUNTIME_VERSION}
    if runtime_valid:
        failures = [reason for reason in failures if reason != LEGACY_RUNTIME_FAILURE]
    else:
        failures.append(
            "native runtime version evidence is missing or inconsistent: "
            + (", ".join(sorted(runtime_versions)) if runtime_versions else "missing")
        )

    executor_required = planning_failure is None
    executor_valid = executor == NATIVE_EXECUTOR
    if executor_valid:
        failures = [reason for reason in failures if reason != LEGACY_EXECUTOR_FAILURE]
    elif executor_required:
        failures.append(
            f"native executor evidence is missing or inconsistent: {executor or 'missing'}"
        )
    else:
        failures = [reason for reason in failures if reason != LEGACY_EXECUTOR_FAILURE]
        failures = [
            reason
            for reason in failures
            if not reason.startswith("native executor evidence is missing or inconsistent:")
        ]

    checks = dict(result.get("checks") or {})
    checks.update(
        {
            "native_runtime_version": NATIVE_RUNTIME_VERSION,
            "observed_runtime_versions": sorted(runtime_versions),
            "native_executor": NATIVE_EXECUTOR,
            "observed_executor": executor,
            "native_executor_required": executor_required,
            "native_contract_status": (
                "PASS"
                if runtime_valid and executor_valid
                else "PASS_PRE_EXECUTION"
                if runtime_valid and not executor_required
                else "FAIL"
            ),
        }
    )
    result["runtime_version"] = NATIVE_RUNTIME_VERSION
    result["checks"] = checks
    result["failures"] = list(dict.fromkeys(failures))
    return result


def _normalize_planning_failure(
    root: Path,
    result: dict[str, Any],
    planning: Mapping[str, Any],
) -> dict[str, Any]:
    """Collapse expected downstream absences into one truthful root failure."""
    manifest = _load(
        root / "report-comments" / "report-comments-manifest.json", {}
    )
    manifest = manifest if isinstance(manifest, Mapping) else {}
    publication_status = str(manifest.get("publication_status") or "missing")
    planning_report_present = bool(planning.get("infeasibility_report_present"))
    calls = int(planning.get("model_calls_performed") or 0)
    fallback_used = bool(planning.get("fallback_used"))
    evidence_valid = (
        planning_report_present
        and calls == 0
        and not fallback_used
        and publication_status == "skipped_failed_execution"
    )
    code = str(planning.get("code") or "PLANNING_FAILED")
    message = str(planning.get("message") or "planning failed")
    root_failure = f"planning failed before model calls: {code}: {message}"

    checks = dict(result.get("checks") or {})
    checks.update(
        {
            "planning_failure": dict(planning),
            "planning_failure_evidence_valid": evidence_valid,
            "downstream_execution_stages_applicable": False,
            "report_publication_status": publication_status,
            "model_calls": calls,
        }
    )
    stage_status = dict(result.get("stage_status") or {})
    stage_status.update(
        {
            "runtime": "FAIL_CLOSED_PRE_EXECUTION",
            "requests": "NOT_APPLICABLE",
            "graph": "INFEASIBLE",
            "report": (
                "SKIPPED_FAILED_EXECUTION"
                if publication_status == "skipped_failed_execution"
                else "FAIL"
            ),
        }
    )
    failures = [root_failure]
    if not evidence_valid:
        failures.append("planning failure evidence chain is incomplete or inconsistent")
    result["checks"] = checks
    result["stage_status"] = stage_status
    result["failures"] = failures
    result["degradations"] = []
    result["status"] = "FAIL"
    result["primary_failure"] = {
        "code": code,
        "stage": str(planning.get("stage") or "planning"),
        "message": message,
        "retryable": bool(planning.get("retryable")),
    }
    return result


def audit(root: Path, *, execute_outcome: str, publish_outcome: str) -> dict[str, Any]:
    planning = _planning_failure(root)
    result = base.audit(
        root,
        execute_outcome=execute_outcome,
        publish_outcome=publish_outcome,
    )
    result = _apply_native_contract(root, result, planning)
    if planning is not None:
        return _normalize_planning_failure(root, result, planning)

    summary = _load(root / "v5-execution-summary.json", {})
    summary = summary if isinstance(summary, Mapping) else {}
    evidence = _node_quality(root)
    failures = list(result.get("failures") or [])
    degradations = list(result.get("degradations") or [])
    completion_mode = str(summary.get("completion_mode") or "")
    quality_status = str(summary.get("quality_status") or "")
    integrity = summary.get("quality_integrity", {})
    integrity = integrity if isinstance(integrity, Mapping) else {}

    if evidence["contract_incomplete_node_ids"]:
        degradations.append(
            "one or more usable nodes did not satisfy the deterministic output contract"
        )

    if evidence["failed_node_ids"]:
        failures.append(
            "node-level execution failures are present: "
            + ", ".join(evidence["failed_node_ids"])
        )

    if evidence["degraded_nodes"]:
        if completion_mode != "degraded" or quality_status != "degraded_success":
            failures.append(
                "degraded node output was incorrectly represented as full success"
            )
        else:
            degradations.append(
                "one or more nodes delivered usable output after failing a quality gate"
            )
        if integrity.get("status") != "DEGRADED":
            failures.append("run-level quality integrity evidence is missing or inconsistent")
    elif evidence["all_nodes_strict"]:
        if completion_mode == "full" and quality_status != "full_success":
            failures.append("strict full completion is missing full_success quality status")
        if completion_mode == "full" and integrity.get("status") not in {"PASS", None}:
            failures.append("strict node success conflicts with run-level quality integrity")

    if quality_status == "full_success" and evidence["degraded_nodes"]:
        failures.append("full_success is forbidden when a node is success_degraded")

    checks = dict(result.get("checks") or {})
    checks.update(
        {
            "quality_status": quality_status,
            "quality_integrity_status": integrity.get("status"),
            "strict_node_count": len(evidence["strict_node_ids"]),
            "degraded_node_count": len(evidence["degraded_nodes"]),
            "failed_node_count": len(evidence["failed_node_ids"]),
            "contract_incomplete_node_count": len(evidence["contract_incomplete_node_ids"]),
            "node_quality_evidence": evidence,
        }
    )
    result["checks"] = checks
    result["failures"] = list(dict.fromkeys(failures))
    result["degradations"] = list(dict.fromkeys(degradations))
    result["status"] = (
        "FAIL"
        if result["failures"]
        else "DEGRADED"
        if result["degradations"]
        else "PASS"
    )
    if result["status"] == "PASS":
        result["primary_failure"] = {
            "code": "NONE",
            "stage": "completed",
            "message": "",
            "retryable": False,
        }
    elif result["status"] == "DEGRADED":
        result["primary_failure"] = {
            "code": "DEGRADED_SUCCESS",
            "stage": "quality-integrity",
            "message": result["degradations"][0] if result["degradations"] else "bounded degradation",
            "retryable": False,
        }
    elif result["failures"]:
        primary = result.get("primary_failure")
        primary = dict(primary) if isinstance(primary, Mapping) else {}
        if primary.get("message") in {LEGACY_RUNTIME_FAILURE, LEGACY_EXECUTOR_FAILURE, ""}:
            primary = {
                "code": "V5_PRODUCTION_AUDIT_FAILED",
                "stage": "v5-production-audit",
                "message": result["failures"][0],
                "retryable": False,
            }
        result["primary_failure"] = primary
    return result


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
