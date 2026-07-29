#!/usr/bin/env python3
"""Deterministically classify an execution and generate one diagnostic summary."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_output(name: str, value: Any) -> None:
    target = os.getenv("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={str(value).replace(chr(10), ' ').replace(chr(13), ' ')}\n")


def _error_code(message: str, judge: Mapping[str, Any]) -> str:
    text = message.casefold()
    if "truncated and too short" in text:
        return "JUDGE_OUTPUT_TOO_SHORT"
    if "fixed 3+1 execution requires" in text:
        return "EXPERT_QUORUM_FAILED"
    if "timeout" in text or "timed out" in text:
        return "MODEL_TIMEOUT"
    if "empty" in text or "no final answer" in text:
        return "MODEL_EMPTY_ANSWER"
    if "budget" in text or "cost limit" in text:
        return "LEGACY_COST_GUARD_TRIGGERED"
    if judge.get("finish_reason") == "length":
        return "JUDGE_OUTPUT_TRUNCATED"
    return "EXECUTION_ERROR" if message else "NONE"


def _expert_results(output_dir: Path, result: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
    rows = result.get("expert_results") if isinstance(result.get("expert_results"), list) else None
    if rows is not None:
        return [dict(item) for item in rows if isinstance(item, Mapping)], "expert-team-result.json"
    fallback = _load(output_dir / "expert-responses.json", [])
    if isinstance(fallback, list):
        return [dict(item) for item in fallback if isinstance(item, Mapping)], "expert-responses.json"
    return [], "missing"


def audit(
    output_dir: Path,
    *,
    execute_outcome: str,
    publish_outcome: str,
    require_diagnostics: bool = False,
) -> dict[str, Any]:
    failures: list[str] = []
    degradations: list[str] = []
    checks: dict[str, Any] = {}

    ticket = _load(output_dir / "ticket-status.json", {})
    result = _load(output_dir / "expert-team-result.json", {})
    ledger = _load(output_dir / "call-ledger.json", {})
    request_audit = _load(output_dir / "request-audit.json", {})
    routing = _load(output_dir / "task-routing.json", {})
    report_manifest = _load(output_dir / "report-comments" / "report-comments-manifest.json", {})
    error_artifact = _load(output_dir / "expert-team-error.json", {})
    judge_diag = _load(output_dir / "judge-response-diagnostics.json", {})
    diagnostic_summary = _load(output_dir / "diagnostic-summary.json", {})

    checks["execute_outcome"] = execute_outcome
    checks["publish_outcome"] = publish_outcome
    if execute_outcome != "success":
        failures.append(f"expert execution outcome is {execute_outcome}")
    if publish_outcome != "success":
        failures.append(f"report publication preparation outcome is {publish_outcome}")
    if ticket.get("private_output") is True:
        failures.append("private_output was accepted despite no private delivery channel")
    checks["diagnostics_required"] = require_diagnostics
    if require_diagnostics and (not isinstance(diagnostic_summary, Mapping) or not diagnostic_summary):
        failures.append("structured diagnostic summary is missing")

    expert_results, expert_source = _expert_results(output_dir, result if isinstance(result, Mapping) else {})
    usable = sum(item.get("status") in {"success_complete", "success_partial"} for item in expert_results)
    complete = sum(item.get("status") == "success_complete" for item in expert_results)
    checks["expert_result_source"] = expert_source
    checks["experts_usable"] = usable
    checks["experts_complete"] = complete
    if usable != 3:
        failures.append(f"usable expert answers are {usable}/3")
    if complete != 3:
        degradations.append(f"only {complete}/3 expert answers are complete")

    judge_status = str(result.get("judge_status") or "") if isinstance(result, Mapping) else ""
    checks["judge_status"] = judge_status
    if judge_status not in {"success_complete", "success_partial"}:
        failures.append(f"judge status is {judge_status or 'missing'}")
    elif judge_status == "success_partial":
        degradations.append("judge report is partial")

    summary = ledger.get("summary") if isinstance(ledger.get("summary"), Mapping) else {}
    call_count = int(summary.get("call_count") or 0)
    approved_calls = int(ticket.get("calls") or 0)
    checks["model_call_count"] = call_count
    checks["approved_model_calls"] = approved_calls
    if call_count < 4:
        failures.append(f"call ledger contains only {call_count} paid calls")
    if approved_calls and call_count > approved_calls:
        failures.append(f"call ledger exceeds approved call ceiling: {call_count}>{approved_calls}")

    request_status = str(request_audit.get("status") or "missing") if isinstance(request_audit, Mapping) else "missing"
    captured_requests = int(request_audit.get("captured_request_count") or 0) if isinstance(request_audit, Mapping) else 0
    expected_requests = int(request_audit.get("expected_request_count") or 0) if isinstance(request_audit, Mapping) else 0
    checks["request_audit_status"] = request_status
    checks["captured_request_count"] = captured_requests
    checks["expected_request_count"] = expected_requests
    checks["routing_request_captured"] = bool(
        not routing.get("call_consumed") or isinstance(routing.get("request_payload"), Mapping)
    ) if isinstance(routing, Mapping) else False
    if request_status != "PASS":
        failures.append(f"model request audit status is {request_status}")
    if expected_requests != call_count:
        failures.append(f"request audit count does not match paid call ledger: {expected_requests}!={call_count}")
    if captured_requests != expected_requests:
        failures.append(f"only {captured_requests}/{expected_requests} model request payloads were captured")

    cost_status = str(summary.get("cost_evidence_status") or "unknown")
    conservative_cost = float(summary.get("conservative_cost_usd") or 0.0)
    actual_cost = float(summary.get("provider_actual_cost_usd") or 0.0)
    checks["cost_policy"] = "no-hard-monetary-ceiling"
    checks["estimated_cost_policy"] = "provider-max-theoretical-not-a-limit"
    checks["cost_evidence_status"] = cost_status
    checks["provider_actual_cost_usd"] = actual_cost
    checks["conservative_cost_usd"] = conservative_cost
    if cost_status == "unknown":
        degradations.append("one or more model calls have unknown cost")
    elif cost_status == "estimated_only":
        degradations.append("one or more model calls use conservative estimated cost")

    substantive_providers = summary.get("substantive_providers") if isinstance(summary.get("substantive_providers"), list) else []
    substantive_provider_count = int(summary.get("substantive_provider_count") or 0)
    checks["substantive_providers"] = substantive_providers
    checks["substantive_provider_count"] = substantive_provider_count
    checks["all_providers"] = summary.get("all_providers") if isinstance(summary.get("all_providers"), list) else []
    if call_count >= 4 and substantive_provider_count == 0:
        failures.append("actual substantive Provider evidence is missing")
    elif substantive_provider_count < 3:
        degradations.append(f"only {substantive_provider_count} distinct substantive Providers were recorded")

    replacements = int(summary.get("replacement_calls") or 0)
    judge_replacements = int(summary.get("judge_replacement_calls") or 0)
    checks["replacement_calls"] = replacements
    checks["judge_replacement_calls"] = judge_replacements
    if replacements:
        degradations.append(f"execution recovered through {replacements} replacement call(s)")

    report_path = output_dir / "expert-team-report.md"
    files = report_manifest.get("files") if isinstance(report_manifest.get("files"), list) else []
    checks["report_comment_count"] = len(files)
    if not report_path.exists() or not report_manifest:
        failures.append("report or report-comment manifest is missing")
    else:
        report = report_path.read_text(encoding="utf-8")
        digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
        checks["report_sha256"] = digest
        if digest != report_manifest.get("report_sha256"):
            failures.append("report-comment manifest SHA256 does not match the report")
        for filename in files:
            if not (output_dir / "report-comments" / str(filename)).is_file():
                failures.append(f"report comment file is missing: {filename}")

    error_message = str(error_artifact.get("message") or error_artifact.get("error") or "") if isinstance(error_artifact, Mapping) else ""
    error_code = str(error_artifact.get("error_code") or "") if isinstance(error_artifact, Mapping) else ""
    if not error_code:
        error_code = _error_code(error_message, judge_diag if isinstance(judge_diag, Mapping) else {})
    primary_failure = {
        "code": error_code,
        "stage": str(error_artifact.get("stage") or ("judge" if error_code.startswith("JUDGE_") else "execution")) if isinstance(error_artifact, Mapping) else "execution",
        "message": error_message,
        "model": error_artifact.get("model") if isinstance(error_artifact, Mapping) else None,
        "provider": (judge_diag.get("provider") if isinstance(judge_diag, Mapping) else None),
        "finish_reason": (judge_diag.get("finish_reason") if isinstance(judge_diag, Mapping) else None),
        "completion_tokens": (judge_diag.get("completion_tokens") if isinstance(judge_diag, Mapping) else None),
        "reasoning_tokens": (judge_diag.get("reasoning_tokens") if isinstance(judge_diag, Mapping) else None),
        "retryable": error_code in {"JUDGE_OUTPUT_TOO_SHORT", "JUDGE_OUTPUT_TRUNCATED", "MODEL_TIMEOUT", "MODEL_EMPTY_ANSWER"},
    }

    status = "FAIL" if failures else "DEGRADED" if degradations else "PASS"
    stage_status = {
        "ticket": "PASS" if ticket.get("accepted") else "FAIL",
        "routing": "PASS" if (output_dir / "task-routing.json").exists() else "MISSING",
        "requests": "PASS" if request_status == "PASS" else "FAIL",
        "diagnostics": "PASS" if diagnostic_summary else "MISSING",
        "experts": "PASS" if complete == 3 else "DEGRADED" if usable == 3 else "FAIL",
        "judge": "PASS" if judge_status == "success_complete" else "DEGRADED" if judge_status == "success_partial" else "FAIL",
        "report": "PASS" if report_path.exists() and report_manifest else "FAIL",
        "artifact_manifest": "PASS" if (output_dir / "artifact-manifest.json").exists() else "PENDING",
    }
    return {
        "version": 3,
        "status": status,
        "primary_failure": primary_failure,
        "stage_status": stage_status,
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
    result = audit(
        root,
        execute_outcome=args.execute_outcome,
        publish_outcome=args.publish_outcome,
        require_diagnostics=os.getenv("GITHUB_ACTIONS") == "true",
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    (root / "execution-audit.json").write_text(serialized, encoding="utf-8")
    (root / "execution-diagnosis.json").write_text(serialized, encoding="utf-8")
    _write_output("status", result["status"])
    _write_output("reason", "; ".join(result["failures"] or result["degradations"]))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
