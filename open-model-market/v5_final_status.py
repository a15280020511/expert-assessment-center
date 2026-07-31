#!/usr/bin/env python3
"""Render the authoritative final Issue status for a V5 R8 production run."""
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
    parser.add_argument("--audit-outcome", default="unknown")
    parser.add_argument("--manifest-outcome", default="unknown")
    parser.add_argument("--artifact-id", default="")
    parser.add_argument("--artifact-url", default="")
    parser.add_argument("--artifact-digest", default="")
    args = parser.parse_args()

    root = Path(args.output_dir)
    ticket = _load(root / "ticket-status.json", {})
    result = _load(root / "expert-team-result.json", {})
    graph = _load(root / "v5-execution-graph.json", {})
    audit = _load(root / "execution-diagnosis.json", {"status": "FAIL", "failures": ["V5 audit is missing"]})
    ledger = _load(root / "call-ledger.json", {})
    runtime = _load(root / "production-runtime.json", {})

    failures = list(audit.get("failures") or []) if isinstance(audit, Mapping) else ["V5 audit is invalid"]
    degradations = list(audit.get("degradations") or []) if isinstance(audit, Mapping) else []
    status = str(audit.get("status") or "FAIL") if isinstance(audit, Mapping) else "FAIL"
    if args.audit_outcome != "success":
        status = "FAIL"
        failures.append(f"V5 audit step outcome is {args.audit_outcome}")
    if args.manifest_outcome != "success":
        status = "FAIL"
        failures.append(f"primary artifact manifest step outcome is {args.manifest_outcome}")
    if args.ticket_upload_outcome != "success":
        status = "FAIL"
        failures.append(f"primary ticket artifact upload outcome is {args.ticket_upload_outcome}")

    summary = ledger.get("summary") if isinstance(ledger, Mapping) and isinstance(ledger.get("summary"), Mapping) else {}
    nodes = graph.get("nodes") if isinstance(graph, Mapping) and isinstance(graph.get("nodes"), list) else []
    primary = audit.get("primary_failure") if isinstance(audit, Mapping) and isinstance(audit.get("primary_failure"), Mapping) else {}
    providers = summary.get("substantive_providers") if isinstance(summary.get("substantive_providers"), list) else []
    approved_total = int(summary.get("approved_total_call_ceiling") or ticket.get("calls") or 0) if isinstance(ticket, Mapping) else 0
    approved_recovery = int(summary.get("approved_recovery_call_ceiling") or ticket.get("maximum_recovery_calls") or 0) if isinstance(ticket, Mapping) else 0
    approved_initial = max(0, approved_total - approved_recovery)

    lines = [
        f"## {_heading(status)}",
        "",
        "- Runtime: `V5 R8 production`",
        f"- Deterministic audit status: `{status}`",
        f"- Run: `{args.run_url or 'unknown'}`",
        f"- Task ID：`{ticket.get('task_id', '') if isinstance(ticket, Mapping) else ''}`",
        f"- TASK_FINGERPRINT: `{ticket.get('task_fingerprint', '') if isinstance(ticket, Mapping) else ''}`",
        f"- Dynamic graph nodes: `{len(nodes)}` / approved initial capacity `{approved_initial}`",
        f"- Model calls: `{summary.get('call_count', 0)}` / approved total hard ceiling `{approved_total}`",
        f"- Recovery calls: `{int(summary.get('replacement_calls') or 0) + int(summary.get('retry_calls') or 0)}` / approved reserve `{approved_recovery}`",
        f"- Completion mode: `{result.get('completion_mode', '') if isinstance(result, Mapping) else ''}`",
        f"- Quality status: `{result.get('quality_status', '') if isinstance(result, Mapping) else ''}`",
        f"- Provider-reported/reconciled cost: `${summary.get('provider_actual_cost_usd', 0)}`",
        f"- Cost anomaly stop: `{summary.get('cost_anomaly_usd') if summary.get('cost_anomaly_usd') is not None else 'account/estimate guard only'}`",
        f"- Provider count: `{summary.get('substantive_provider_count', 0)}`",
        f"- Providers: `{', '.join(str(item) for item in providers) or 'unavailable'}`",
        "- External tools: `forbidden`",
        "- Alternate runtime fallback: `disabled`",
        f"- Legacy runtime present: `{str(runtime.get('legacy_runtime_present')).lower() if isinstance(runtime, Mapping) else 'unknown'}`",
        f"- Primary Artifact ID: `{args.artifact_id or 'unavailable'}`",
        f"- Primary Artifact digest: `{args.artifact_digest or 'unavailable'}`",
        "- Final evidence: `a separate post-upload final-attestation Artifact is required before the Job can pass`",
    ]
    if args.artifact_url:
        lines.append(f"- Primary Artifact: {args.artifact_url}")

    code = str(primary.get("code") or "NONE")
    message = str(primary.get("message") or "")
    if code != "NONE" or message:
        lines.extend([
            "",
            "### Primary diagnosis",
            "",
            f"- Error code: `{code}`",
            f"- Stage: `{primary.get('stage', 'v5-production')}`",
            f"- Message: {message or 'No direct error message was recorded.'}",
            f"- Retryable: `{str(bool(primary.get('retryable'))).lower()}`",
        ])
    if failures:
        lines.extend(["", "### Failure reasons", ""] + [f"- {item}" for item in dict.fromkeys(failures)])
    if degradations:
        lines.extend(["", "### Degradation reasons", ""] + [f"- {item}" for item in dict.fromkeys(degradations)])

    if status == "PASS":
        lines.extend([
            "",
            "V5 动态专家图已完成生产任务；完整最终报告已发布并通过 SHA-256 核验。动态规划、节点结果、全部模型请求、费用、Provider、审计和主 Manifest 保存在主 Artifact；主 Artifact 的 ID、digest 与本终态由随后上传的 final-attestation Artifact 封闭证明。",
        ])
    elif status == "DEGRADED":
        lines.extend([
            "",
            "V5 已交付，但发生受控降级。不得表述为完整正常 PASS；系统不会调用其他运行时。",
        ])
    else:
        lines.extend([
            "",
            "本次 V5 运行不得表述为成功。系统已失败关闭，不调用其他运行时；诊断和中间证据保存在主 Artifact（若上传成功）。",
        ])

    print("\n".join(lines) + "\n")
    _write_output("status", status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
