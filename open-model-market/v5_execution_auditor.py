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
MAX_MODEL_CALLS = 16
MAX_NODES = 16


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

    checks.update({
        "runtime_version": result.get("runtime_version") if isinstance(result, Mapping) else None,
        "execute_outcome": execute_outcome,
        "publish_outcome": publish_outcome,
    })
    if execute_outcome != "success":
        failures.append(f"V5 execution outcome is {execute_outcome}")
    if publish_outcome != "success":
        failures.append(f"report publication outcome is {publish_outcome}")
    if not isinstance(ticket, Mapping) or ticket.get("accepted") is not True:
        failures.append("production ticket was not accepted")
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
    if not nodes or len(nodes) > MAX_NODES:
        failures.append(f"V5 graph node count is invalid: {len(nodes)}")
    if not final_nodes:
        failures.append("V5 graph has no final node")

    budget = summary.get("execution_budget") if isinstance(summary, Mapping) and isinstance(summary.get("execution_budget"), Mapping) else {}
    calls = int(budget.get("calls_reserved") or 0)
    actual_cost = float(summary.get("actual_cost_usd") or budget.get("actual_cost_usd") or 0.0) if isinstance(summary, Mapping) else 0.0
    checks.update({"model_calls": calls, "maximum_model_calls": MAX_MODEL_CALLS, "actual_cost_usd": actual_cost})
    if calls <= 0 or calls > MAX_MODEL_CALLS:
        failures.append(f"V5 model calls are outside the production bound: {calls}")
    if not math.isfinite(actual_cost) or actual_cost < 0:
        failures.append("V5 actual cost is invalid")

    request_status = str(request_audit.get("status") or "missing") if isinstance(request_audit, Mapping) else "missing"
    expected = int(request_audit.get("expected_request_count") or 0) if isinstance(request_audit, Mapping) else 0
    captured = int(request_audit.get("captured_request_count") or 0) if isinstance(request_audit, Mapping) else 0
    tools_allowed = request_audit.get("external_tools_allowed") if isinstance(request_audit, Mapping) else None
    checks.update({
        "request_audit_status": request_status,
        "expected_request_count": expected,
        "captured_request_count": captured,
        "external_tools_allowed": tools_allowed,
    })
    if request_status != "PASS":
        failures.append(f"V5 request audit status is {request_status}")
    if expected != calls or captured != expected:
        failures.append(f"V5 request evidence is incomplete: captured={captured}, expected={expected}, calls={calls}")
    if tools_allowed is not False:
        failures.append("external-tool prohibition evidence is missing")

    ledger_summary = ledger.get("summary") if isinstance(ledger, Mapping) and isinstance(ledger.get("summary"), Mapping) else {}
    if int(ledger_summary.get("call_count") or 0) != calls:
        failures.append("V5 call ledger does not match execution budget")
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
        for filename in files:
            if not (root / "report-comments" / str(filename)).is_file():
                failures.append(f"report comment file is missing: {filename}")

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
            "ticket": "PASS" if isinstance(ticket, Mapping) and ticket.get("accepted") else "FAIL",
            "runtime": "PASS" if status == "success" else "FAIL",
            "requests": "PASS" if request_status == "PASS" and captured == expected == calls else "FAIL",
            "graph": "PASS" if nodes and len(nodes) <= MAX_NODES and final_nodes else "FAIL",
            "report": "PASS" if report_path.is_file() and report_manifest else "FAIL",
            "artifact_manifest": "PENDING",
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
