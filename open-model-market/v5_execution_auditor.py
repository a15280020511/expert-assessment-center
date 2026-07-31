#!/usr/bin/env python3
"""Deterministically audit a V5 R8 production ticket execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

RUNTIME_VERSION = "v5-r8"
ABSOLUTE_MAX_MODEL_CALLS = 16
ABSOLUTE_MAX_NODES = 16


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_output(name: str, value: Any) -> None:
    target = os.getenv("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={str(value).replace(chr(10), ' ').replace(chr(13), ' ')}\n")


def _positive_optional(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def audit(root: Path, *, execute_outcome: str, publish_outcome: str) -> dict[str, Any]:
    failures: list[str] = []
    degradations: list[str] = []
    checks: dict[str, Any] = {}

    ticket = _load(root / "ticket-status.json", {})
    result = _load(root / "expert-team-result.json", {})
    summary = _load(root / "v5-execution-summary.json", {})
    graph = _load(root / "v5-execution-graph.json", {})
    request_audit = _load(root / "request-audit.json", {})
    ledger = _load(root / "call-ledger.json", {})
    runtime = _load(root / "production-runtime.json", {})
    report_manifest = _load(root / "report-comments" / "report-comments-manifest.json", {})
    error = _load(root / "expert-team-error.json", {})

    approved_total = int(ticket.get("calls") or 0) if isinstance(ticket, Mapping) else 0
    approved_recovery = int(ticket.get("maximum_recovery_calls") or 0) if isinstance(ticket, Mapping) else 0
    approved_initial = int(ticket.get("maximum_initial_calls") or 0) if isinstance(ticket, Mapping) else 0
    anomaly_budget = _positive_optional(ticket.get("cost_anomaly_usd")) if isinstance(ticket, Mapping) else None
    budget_contract_valid = (
        4 <= approved_total <= ABSOLUTE_MAX_MODEL_CALLS
        and 0 <= approved_recovery < approved_total
        and approved_initial == approved_total - approved_recovery
        and ticket.get("cost_policy") == "unbounded_with_anomaly_guard"
    ) if isinstance(ticket, Mapping) else False
    checks.update({
        "runtime_version": result.get("runtime_version") if isinstance(result, Mapping) else None,
        "execute_outcome": execute_outcome,
        "publish_outcome": publish_outcome,
        "approved_total_calls": approved_total,
        "approved_recovery_calls": approved_recovery,
        "approved_initial_calls": approved_initial,
        "cost_anomaly_usd": anomaly_budget,
        "budget_contract_valid": budget_contract_valid,
    })
    if execute_outcome != "success":
        failures.append(f"V5 execution outcome is {execute_outcome}")
    if publish_outcome != "success":
        failures.append(f"report publication outcome is {publish_outcome}")
    if not isinstance(ticket, Mapping) or ticket.get("accepted") is not True:
        failures.append("production ticket was not accepted")
    if not budget_contract_valid:
        failures.append("approved V5 budget contract is missing, invalid, or internally inconsistent")
    if not isinstance(result, Mapping) or result.get("runtime_version") != RUNTIME_VERSION:
        failures.append("V5 production result envelope is missing")
    if not isinstance(runtime, Mapping) or runtime.get("fallback_policy") != "fail-closed-no-alternate-runtime":
        failures.append("fail-closed V5 runtime evidence is missing")
    if isinstance(result, Mapping) and result.get("fallback_used") is not False:
        failures.append("an alternate runtime fallback was used or not explicitly disabled")
    if not isinstance(runtime, Mapping) or runtime.get("legacy_runtime_present") is not False:
        failures.append("legacy runtime absence was not proven")
    if isinstance(result, Mapping) and result.get("legacy_runtime_present") is not False:
        failures.append("result envelope does not prove legacy runtime absence")

    status = str(summary.get("status") or result.get("status") or "") if isinstance(summary, Mapping) else ""
    completion_mode = str(summary.get("completion_mode") or result.get("completion_mode") or "") if isinstance(summary, Mapping) else ""
    executor = str(summary.get("executor") or result.get("executor") or "") if isinstance(summary, Mapping) else ""
    answer = str(summary.get("final_answer") or result.get("final_answer") or "") if isinstance(summary, Mapping) else ""
    checks.update({
        "v5_status": status,
        "completion_mode": completion_mode,
        "executor": executor,
        "final_answer_chars": len(answer.strip()),
    })
    if status != "success":
        failures.append(f"V5 delivery status is {status or 'missing'}")
    if executor != "v5-r8-fault-aware":
        failures.append("R8 fault-aware executor evidence is missing")
    if len(answer.strip()) < 160:
        failures.append("V5 final answer is missing or too short")
    if completion_mode == "degraded":
        degradations.append("V5 delivered through bounded degradation")

    nodes = graph.get("nodes") if isinstance(graph, Mapping) and isinstance(graph.get("nodes"), list) else []
    final_nodes = graph.get("final_nodes") if isinstance(graph, Mapping) and isinstance(graph.get("final_nodes"), list) else []
    checks["node_count"] = len(nodes)
    checks["final_node_count"] = len(final_nodes)
    node_limit = min(ABSOLUTE_MAX_NODES, approved_initial) if approved_initial > 0 else 0
    checks["approved_node_limit"] = node_limit
    if not nodes or node_limit <= 0 or len(nodes) > node_limit:
        failures.append(f"V5 graph node count exceeds the approved initial-call capacity: {len(nodes)} > {node_limit}")
    if not final_nodes:
        failures.append("V5 graph has no final node")

    budget = summary.get("execution_budget") if isinstance(summary, Mapping) and isinstance(summary.get("execution_budget"), Mapping) else {}
    calls = int(budget.get("calls_reserved") or 0)
    ledger_total = int(budget.get("maximum_total_calls") or 0)
    ledger_initial = int(budget.get("maximum_initial_calls") or 0)
    actual_cost = float(summary.get("actual_cost_usd") or budget.get("actual_cost_usd") or 0.0) if isinstance(summary, Mapping) else 0.0
    checks.update({
        "model_calls": calls,
        "runtime_total_call_ceiling": ledger_total,
        "runtime_initial_call_ceiling": ledger_initial,
        "absolute_maximum_model_calls": ABSOLUTE_MAX_MODEL_CALLS,
        "actual_cost_usd": actual_cost,
    })
    if calls <= 0 or calls > approved_total:
        failures.append(f"V5 model calls exceed or violate the approved ticket bound: {calls}/{approved_total}")
    if ledger_total != approved_total:
        failures.append(f"runtime total-call ceiling differs from approved ticket: {ledger_total}/{approved_total}")
    if ledger_initial != approved_initial:
        failures.append(f"runtime initial-call ceiling differs from approved ticket: {ledger_initial}/{approved_initial}")
    if not math.isfinite(actual_cost) or actual_cost < 0:
        failures.append("V5 actual cost is invalid")
    if anomaly_budget is not None and actual_cost > anomaly_budget + 1e-12:
        failures.append(f"V5 actual cost exceeded the approved anomaly stop: {actual_cost}/{anomaly_budget}")

    request_status = str(request_audit.get("status") or "missing") if isinstance(request_audit, Mapping) else "missing"
    expected = int(request_audit.get("expected_request_count") or 0) if isinstance(request_audit, Mapping) else 0
    captured = int(request_audit.get("captured_request_count") or 0) if isinstance(request_audit, Mapping) else 0
    captured_ceiling = int(request_audit.get("approved_total_call_ceiling") or 0) if isinstance(request_audit, Mapping) else 0
    tools_allowed = request_audit.get("external_tools_allowed") if isinstance(request_audit, Mapping) else None
    checks.update({
        "request_audit_status": request_status,
        "expected_request_count": expected,
        "captured_request_count": captured,
        "request_approved_total_call_ceiling": captured_ceiling,
        "external_tools_allowed": tools_allowed,
    })
    if request_status != "PASS":
        failures.append(f"V5 request audit status is {request_status}")
    if expected != calls or captured != expected:
        failures.append(f"V5 request evidence is incomplete: captured={captured}, expected={expected}, calls={calls}")
    if captured > approved_total or captured_ceiling != approved_total:
        failures.append("request audit does not prove compliance with the approved total-call ceiling")
    if tools_allowed is not False:
        failures.append("external-tool prohibition evidence is missing")

    ledger_summary = ledger.get("summary") if isinstance(ledger, Mapping) and isinstance(ledger.get("summary"), Mapping) else {}
    if int(ledger_summary.get("call_count") or 0) != calls:
        failures.append("V5 call ledger does not match execution budget")
    if int(ledger_summary.get("approved_total_call_ceiling") or 0) != approved_total:
        failures.append("V5 call ledger does not preserve the approved total-call ceiling")
    if int(ledger_summary.get("approved_recovery_call_ceiling") or 0) != approved_recovery:
        failures.append("V5 call ledger does not preserve the approved recovery-call ceiling")
    provider_count = int(ledger_summary.get("substantive_provider_count") or 0)
    checks["substantive_provider_count"] = provider_count
    checks["substantive_providers"] = ledger_summary.get("substantive_providers") or []
    if provider_count <= 0:
        failures.append("V5 Provider evidence is missing")

    report_path = root / "expert-team-report.md"
    files = report_manifest.get("files") if isinstance(report_manifest, Mapping) and isinstance(report_manifest.get("files"), list) else []
    checks["report_comment_count"] = len(files)
    if not report_path.is_file() or not report_manifest:
        failures.append("V5 report or publication manifest is missing")
    else:
        digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
        checks["report_sha256"] = digest
        if digest != report_manifest.get("report_sha256"):
            failures.append("published report SHA256 does not match V5 report")
        run_url = str(report_manifest.get("run_url") or "").strip().rstrip("/")
        run_id = str(report_manifest.get("run_id") or "").strip()
        expected_suffix = f"/actions/runs/{run_id}" if run_id else ""
        run_evidence_valid = (
            bool(run_url)
            and run_id.isdigit()
            and run_url.endswith(expected_suffix)
        )
        checks["report_run_url"] = run_url
        checks["report_run_id"] = run_id
        checks["report_run_evidence_valid"] = run_evidence_valid
        if not run_evidence_valid:
            failures.append("published report run identity is missing or invalid")
        for index, filename in enumerate(files, 1):
            comment_path = root / "report-comments" / str(filename)
            if not comment_path.is_file():
                failures.append(f"report comment file is missing: {filename}")
                continue
            comment = comment_path.read_text(encoding="utf-8")
            marker = f"expert-team-report-run:{run_id}:part:{index:03d}"
            if run_evidence_valid and (marker not in comment or f"- Run: `{run_url}`" not in comment):
                failures.append(f"report comment run identity is inconsistent: {filename}")

    primary = {
        "code": str(error.get("error_code") or ("NONE" if not failures else "V5_PRODUCTION_AUDIT_FAILED")) if isinstance(error, Mapping) else "V5_PRODUCTION_AUDIT_FAILED",
        "stage": str(error.get("stage") or "v5-production-audit") if isinstance(error, Mapping) else "v5-production-audit",
        "message": str(error.get("message") or (failures[0] if failures else "")) if isinstance(error, Mapping) else (failures[0] if failures else ""),
        "retryable": bool(error.get("retryable")) if isinstance(error, Mapping) else False,
    }
    audit_status = "FAIL" if failures else "DEGRADED" if degradations else "PASS"
    return {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": audit_status,
        "primary_failure": primary,
        "stage_status": {
            "ticket": "PASS" if isinstance(ticket, Mapping) and ticket.get("accepted") and budget_contract_valid else "FAIL",
            "runtime": "PASS" if status == "success" else "FAIL",
            "requests": "PASS" if request_status == "PASS" and captured == expected == calls and captured <= approved_total else "FAIL",
            "graph": "PASS" if nodes and len(nodes) <= node_limit and final_nodes else "FAIL",
            "report": "PASS" if report_path.is_file() and report_manifest else "FAIL",
            "primary_artifact_manifest": "PENDING_UPLOAD",
            "final_attestation": "PENDING_POST_UPLOAD",
        },
        "checks": checks,
        "failures": failures,
        "degradations": degradations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--execute-outcome", default="unknown")
    parser.add_argument("--publish-outcome", default="unknown")
    args = parser.parse_args()
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    result = audit(root, execute_outcome=args.execute_outcome, publish_outcome=args.publish_outcome)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    (root / "execution-audit.json").write_text(serialized, encoding="utf-8")
    (root / "execution-diagnosis.json").write_text(serialized, encoding="utf-8")
    _write_output("status", result["status"])
    _write_output("reason", "; ".join(result["failures"] or result["degradations"]))
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
