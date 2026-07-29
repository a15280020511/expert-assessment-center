#!/usr/bin/env python3
"""Render one authoritative final Issue status after report and artifact delivery."""
from __future__ import annotations

import argparse
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
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def _heading(status: str) -> str:
    return {
        "PASS": "EXECUTION_COMPLETED",
        "DEGRADED": "EXECUTION_DEGRADED",
        "FAIL": "EXECUTION_FAILED",
    }.get(status, "EXECUTION_FAILED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--ticket-upload-outcome", default="unknown")
    parser.add_argument("--state-upload-outcome", default="unknown")
    parser.add_argument("--audit-outcome", default="unknown")
    parser.add_argument("--manifest-outcome", default="unknown")
    parser.add_argument("--artifact-id", default="")
    parser.add_argument("--artifact-url", default="")
    parser.add_argument("--artifact-digest", default="")
    args = parser.parse_args()

    root = Path(args.output_dir)
    ticket = _load(root / "ticket-status.json", {})
    result = _load(root / "expert-team-result.json", {})
    audit = _load(root / "execution-diagnosis.json", _load(root / "execution-audit.json", {"status": "FAIL", "failures": ["execution audit is missing"]}))
    ledger = _load(root / "call-ledger.json", {})
    judge_diag = _load(root / "judge-response-diagnostics.json", {})
    summary = ledger.get("summary") if isinstance(ledger.get("summary"), Mapping) else {}

    failures = list(audit.get("failures") or [])
    degradations = list(audit.get("degradations") or [])
    status = str(audit.get("status") or "FAIL")
    if args.audit_outcome != "success":
        status = "FAIL"
        failures.append(f"execution audit step outcome is {args.audit_outcome}")
    if args.manifest_outcome != "success":
        status = "FAIL"
        failures.append(f"artifact manifest step outcome is {args.manifest_outcome}")
    if args.ticket_upload_outcome != "success":
        status = "FAIL"
        failures.append(f"ticket artifact upload outcome is {args.ticket_upload_outcome}")
    if args.state_upload_outcome != "success" and status != "FAIL":
        status = "DEGRADED"
        degradations.append(f"model performance state upload outcome is {args.state_upload_outcome}")

    primary = audit.get("primary_failure") if isinstance(audit.get("primary_failure"), Mapping) else {}
    checks = audit.get("checks") if isinstance(audit.get("checks"), Mapping) else {}
    judge = result.get("judge") if isinstance(result.get("judge"), Mapping) else {}
    judge_model = str(judge.get("model_id") or primary.get("model") or judge_diag.get("model") or "")
    actual_cost = summary.get("provider_actual_cost_usd", 0)
    conservative_cost = summary.get("conservative_cost_usd", 0)
    substantive_providers = summary.get("substantive_providers") if isinstance(summary.get("substantive_providers"), list) else []
    provider_text = ", ".join(str(item) for item in substantive_providers) or "unavailable"

    lines = [
        f"## {_heading(status)}",
        "",
        f"- Deterministic audit status: `{status}`",
        f"- Run: `{args.run_url or 'unknown'}`",
        f"- Task ID：`{ticket.get('task_id', '')}`",
        f"- TASK_FINGERPRINT: `{ticket.get('task_fingerprint', '')}`",
        f"- Model calls reconstructed: `{summary.get('call_count', 0)}` / approved `{ticket.get('calls', 0)}`",
        f"- Captured model requests: `{checks.get('captured_request_count', 0)}` / expected `{checks.get('expected_request_count', 0)}`",
        f"- Request audit: `{checks.get('request_audit_status', 'unknown')}`",
        f"- Expert replacement calls: `{summary.get('expert_replacement_calls', 0)}`",
        f"- Judge replacement calls: `{summary.get('judge_replacement_calls', 0)}`",
        "- Hard monetary ceiling: `none`",
        "- Estimate policy: `provider-max theoretical estimate; not a limit`",
        f"- Cost evidence: `{summary.get('cost_evidence_status', 'unknown')}`",
        f"- Provider-reported total cost: `${actual_cost}`",
        f"- Conservative accounted cost: `${conservative_cost}`",
        f"- Substantive Provider count: `{summary.get('substantive_provider_count', 0)}`",
        f"- Substantive Providers: `{provider_text}`",
        f"- Judge model: `{judge_model}`",
        f"- Judge status: `{result.get('judge_status', '') if isinstance(result, Mapping) else ''}`",
        f"- Artifact ID: `{args.artifact_id or 'unavailable'}`",
        f"- Artifact digest: `{args.artifact_digest or 'unavailable'}`",
    ]
    if args.artifact_url:
        lines.append(f"- Artifact: {args.artifact_url}")

    code = str(primary.get("code") or "NONE")
    message = str(primary.get("message") or "")
    if code != "NONE" or message:
        lines.extend(
            [
                "",
                "### Primary diagnosis",
                "",
                f"- Error code: `{code}`",
                f"- Stage: `{primary.get('stage', 'execution')}`",
                f"- Message: {message or 'No direct error message was recorded.'}",
                f"- Provider: `{primary.get('provider') or 'unknown'}`",
                f"- Finish reason: `{primary.get('finish_reason') or 'unknown'}`",
                f"- Completion tokens: `{primary.get('completion_tokens') if primary.get('completion_tokens') is not None else 'unknown'}`",
                f"- Reasoning tokens: `{primary.get('reasoning_tokens') if primary.get('reasoning_tokens') is not None else 'unknown'}`",
                f"- Retryable: `{str(bool(primary.get('retryable'))).lower()}`",
            ]
        )

    if failures:
        lines.extend(["", "### Failure reasons", ""] + [f"- {item}" for item in dict.fromkeys(failures)])
    if degradations:
        lines.extend(["", "### Degradation reasons", ""] + [f"- {item}" for item in dict.fromkeys(degradations)])

    if status == "PASS":
        lines.extend(
            [
                "",
                "完整裁判报告已发布并通过SHA-256核验；三名专家原文、全部模型请求、调用账本、费用与Provider证据、统一诊断和Manifest保存在Artifact。",
            ]
        )
    elif status == "DEGRADED":
        lines.extend(
            [
                "",
                "本次执行已交付，但发生部分输出、模型替换或证据降级；不得表述为完整正常PASS。Artifact保留全部诊断证据。",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "本次运行不得表述为成功。直接根因、模型请求、调用记录和已生成的中间产物保存在Artifact（若上传成功）。",
            ]
        )

    print("\n".join(lines) + "\n")
    _write_output("status", status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
