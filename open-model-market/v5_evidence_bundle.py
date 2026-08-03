"""Post-upload final status and attestation helpers.

Pre-upload execution evidence is built only by :mod:`v5_run_evidence`. This
module intentionally contains no planning, optimization, model selection, or
legacy evidence-bundle builder.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from artifact_manifest import sha256_file
from v5_json_io import load_json_or_default

RUNTIME_VERSION = "v5-native-runtime-1"


@dataclass(frozen=True)
class FinalStatusInputs:
    ticket: Mapping[str, Any]
    result: Mapping[str, Any]
    graph: Mapping[str, Any]
    diagnosis: Mapping[str, Any]
    ledger: Mapping[str, Any]
    runtime: Mapping[str, Any]
    evidence_bundle: Mapping[str, Any]

    @classmethod
    def from_directory(cls, root: Path) -> "FinalStatusInputs":
        def mapping(name: str, default: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
            value = load_json_or_default(root / name, default or {})
            return dict(value) if isinstance(value, Mapping) else {}
        return cls(
            ticket=mapping("ticket-status.json"),
            result=mapping("expert-team-result.json"),
            graph=mapping("v5-execution-graph.json"),
            diagnosis=mapping(
                "execution-diagnosis.json",
                {"status": "FAIL", "failures": ["V5 audit is missing"]},
            ),
            ledger=mapping("call-ledger.json"),
            runtime=mapping("production-runtime.json"),
            evidence_bundle=mapping("evidence-bundle.json"),
        )


def _final_status_context(
    inputs: FinalStatusInputs,
    *,
    audit_outcome: str,
    manifest_outcome: str,
    ticket_upload_outcome: str,
    independent_revalidation: Mapping[str, Any] | None,
) -> tuple[
    Mapping[str, Any],
    list[Any],
    list[Any],
    str,
    dict[str, Any],
    str,
]:
    audit = inputs.diagnosis
    failures = list(audit.get("failures") or [])
    degradations = list(audit.get("degradations") or [])
    status = str(audit.get("status") or "FAIL")
    independent = (
        dict(independent_revalidation)
        if isinstance(independent_revalidation, Mapping)
        else {}
    )
    independent_status = str(independent.get("status") or "MISSING").upper()
    if audit_outcome != "success":
        status = "FAIL"
        failures.append(f"V5 audit step outcome is {audit_outcome}")
    if manifest_outcome != "success":
        status = "FAIL"
        failures.append(
            f"primary artifact manifest step outcome is {manifest_outcome}"
        )
    if ticket_upload_outcome != "success":
        status = "FAIL"
        failures.append(
            "primary ticket artifact upload outcome is "
            f"{ticket_upload_outcome}"
        )
    if status in {"PASS", "DEGRADED"} and independent_status != "PASS":
        status = "FAIL"
        failures.append(
            "independent artifact revalidation is not PASS: "
            f"{independent_status}"
        )
    return (
        audit,
        failures,
        degradations,
        status,
        independent,
        independent_status,
    )


def build_final_status_record(
    inputs: FinalStatusInputs,
    *,
    run_url: str,
    ticket_upload_outcome: str,
    audit_outcome: str,
    manifest_outcome: str,
    artifact_id: str,
    artifact_url: str,
    artifact_digest: str,
    independent_revalidation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    (
        audit,
        failures,
        degradations,
        status,
        independent,
        independent_status,
    ) = _final_status_context(
        inputs,
        audit_outcome=audit_outcome,
        manifest_outcome=manifest_outcome,
        ticket_upload_outcome=ticket_upload_outcome,
        independent_revalidation=independent_revalidation,
    )
    summary = inputs.ledger.get("summary")
    summary = dict(summary) if isinstance(summary, Mapping) else {}
    nodes = inputs.graph.get("nodes")
    nodes = nodes if isinstance(nodes, list) else []
    primary = audit.get("primary_failure")
    primary = dict(primary) if isinstance(primary, Mapping) else {}
    approved_total = int(summary.get("approved_total_call_ceiling") or inputs.ticket.get("calls") or 0)
    approved_recovery = int(summary.get("approved_recovery_call_ceiling") or inputs.ticket.get("maximum_recovery_calls") or 0)
    return {
        "schema_version": "v5-final-status-1",
        "status": status,
        "runtime_version": RUNTIME_VERSION,
        "run_url": run_url,
        "task_id": inputs.ticket.get("task_id"),
        "task_fingerprint": inputs.ticket.get("task_fingerprint"),
        "node_count": len(nodes),
        "approved_initial_calls": max(0, approved_total - approved_recovery),
        "call_count": int(summary.get("call_count") or 0),
        "approved_total_calls": approved_total,
        "recovery_calls": int(summary.get("replacement_calls") or 0) + int(summary.get("retry_calls") or 0),
        "approved_recovery_calls": approved_recovery,
        "completion_mode": inputs.result.get("completion_mode"),
        "quality_status": inputs.result.get("quality_status"),
        "provider_actual_cost_usd": summary.get("provider_actual_cost_usd", 0),
        "cost_anomaly_usd": summary.get("cost_anomaly_usd"),
        "substantive_providers": summary.get("substantive_providers", []),
        "substantive_provider_count": summary.get("substantive_provider_count", 0),
        "independent_artifact_revalidation_status": independent_status,
        "independent_artifact_revalidation_schema": independent.get("schema_version"),
        "independent_recomputed_from_primitive_evidence": bool(
            independent.get("recomputed_from_primitive_evidence")
        ),
        "legacy_runtime_present": inputs.runtime.get("legacy_runtime_present"),
        "primary_artifact": {
            "artifact_id": artifact_id,
            "artifact_url": artifact_url,
            "artifact_digest": artifact_digest,
        },
        "primary_failure": primary,
        "failures": list(dict.fromkeys(str(row) for row in failures)),
        "degradations": list(dict.fromkeys(str(row) for row in degradations)),
        "evidence_input_sha256": inputs.evidence_bundle.get("input_sha256"),
        "business_evidence_frozen": bool(inputs.evidence_bundle.get("business_evidence_frozen")),
    }


def _status_heading(status: str) -> str:
    return {
        "PASS": "EXECUTION_COMPLETED",
        "DEGRADED": "EXECUTION_DEGRADED",
        "FAIL": "EXECUTION_FAILED",
    }.get(status, "EXECUTION_FAILED")


def _base_status_lines(
    record: Mapping[str, Any],
    status: str,
    artifact: Mapping[str, Any],
    providers: list[Any],
) -> list[str]:
    anomaly = record.get("cost_anomaly_usd")
    anomaly_label = anomaly if anomaly is not None else "account/estimate guard only"
    return [
        f"## {_status_heading(status)}",
        "",
        "- Runtime: `V5 native production runtime`",
        f"- Deterministic audit status: `{status}`",
        f"- Run: `{record.get('run_url') or 'unknown'}`",
        f"- Task ID：`{record.get('task_id') or ''}`",
        f"- TASK_FINGERPRINT: `{record.get('task_fingerprint') or ''}`",
        f"- Dynamic graph nodes: `{record.get('node_count', 0)}` / approved initial capacity `{record.get('approved_initial_calls', 0)}`",
        f"- Model calls: `{record.get('call_count', 0)}` / approved total hard ceiling `{record.get('approved_total_calls', 0)}`",
        f"- Recovery calls: `{record.get('recovery_calls', 0)}` / approved reserve `{record.get('approved_recovery_calls', 0)}`",
        f"- Completion mode: `{record.get('completion_mode') or ''}`",
        f"- Quality status: `{record.get('quality_status') or ''}`",
        f"- Provider-reported/reconciled cost: `${record.get('provider_actual_cost_usd', 0)}`",
        f"- Cost anomaly stop: `{anomaly_label}`",
        f"- Provider count: `{record.get('substantive_provider_count', 0)}`",
        f"- Providers: `{', '.join(str(item) for item in providers) or 'unavailable'}`",
        "- External tools: `forbidden`",
        "- Alternate runtime fallback: `disabled`",
        f"- Legacy runtime present: `{str(record.get('legacy_runtime_present')).lower()}`",
        f"- Evidence input SHA256: `{record.get('evidence_input_sha256') or 'unavailable'}`",
        f"- Primary Artifact ID: `{artifact.get('artifact_id') or 'unavailable'}`",
        f"- Primary Artifact digest: `{artifact.get('artifact_digest') or 'unavailable'}`",
        "- Final evidence: `a separate post-upload final-attestation Artifact is required before the Job can pass`",
    ]


def _diagnosis_lines(record: Mapping[str, Any]) -> list[str]:
    primary = record.get("primary_failure")
    primary = primary if isinstance(primary, Mapping) else {}
    if not (primary.get("code") or primary.get("message")):
        return []
    return [
        "",
        "### Primary diagnosis",
        "",
        f"- Error code: `{primary.get('code') or 'NONE'}`",
        f"- Stage: `{primary.get('stage') or 'v5-production'}`",
        f"- Message: {primary.get('message') or 'No direct error message was recorded.'}",
        f"- Retryable: `{str(bool(primary.get('retryable'))).lower()}`",
    ]


def _reason_lines(record: Mapping[str, Any], key: str, heading: str) -> list[str]:
    rows = record.get(key)
    if not isinstance(rows, list) or not rows:
        return []
    return ["", heading, "", *[f"- {row}" for row in rows]]


def _terminal_status_line(status: str) -> str:
    return {
        "PASS": "V5 动态专家图已完成生产任务；业务证据在主 Artifact 上传前已冻结，上传后只注入 Artifact 身份并生成最终证明。",
        "DEGRADED": "V5 已交付，但发生受控降级。不得表述为完整正常 PASS；系统不会调用其他运行时。",
    }.get(status, "本次 V5 运行不得表述为成功。系统已失败关闭，不调用其他运行时。")


def render_final_status_markdown(record: Mapping[str, Any]) -> str:
    status = str(record.get("status") or "FAIL")
    artifact = record.get("primary_artifact")
    artifact = artifact if isinstance(artifact, Mapping) else {}
    providers = record.get("substantive_providers")
    providers = providers if isinstance(providers, list) else []
    lines = _base_status_lines(record, status, artifact, providers)
    if artifact.get("artifact_url"):
        lines.append(f"- Primary Artifact: {artifact['artifact_url']}")
    lines.extend(_diagnosis_lines(record))
    lines.extend(_reason_lines(record, "failures", "### Failure reasons"))
    lines.extend(_reason_lines(record, "degradations", "### Degradation reasons"))
    lines.extend(["", _terminal_status_line(status)])
    return "\n".join(lines) + "\n"


def _load_independent_attestation(
    path: Path | None,
) -> tuple[dict[str, Any], bool]:
    raw = load_json_or_default(path, {}) if path is not None else {}
    independent = dict(raw) if isinstance(raw, Mapping) else {}
    valid = (
        independent.get("schema_version")
        == "v5-independent-artifact-revalidation-3"
        and independent.get("status") == "PASS"
        and independent.get("recomputed_from_primitive_evidence") is True
        and independent.get("paid_acceptance_verdict_used_as_source") is False
    )
    return independent, valid


def _require_attestation_inputs(
    *,
    manifest: Path,
    bundle: Path,
    final_status_file: Path,
    report: Path,
    diagnosis_path: Path,
    report_required: bool,
    normalized_audit_status: str,
    primary_artifact_id: str,
    primary_artifact_digest: str,
) -> None:
    required_paths = (manifest, bundle, final_status_file)
    if not all(item.is_file() for item in required_paths):
        raise RuntimeError("manifest, evidence bundle, and final status must exist")
    if report_required and not report.is_file():
        raise RuntimeError("successful or degraded execution requires a report")
    if normalized_audit_status == "FAIL" and not diagnosis_path.is_file():
        raise RuntimeError("failed execution requires deterministic diagnosis evidence")
    if not primary_artifact_id or not primary_artifact_digest:
        raise RuntimeError("primary artifact identity is required")


def _attestation_status(
    *,
    normalized_audit_status: str,
    diagnosis_status: str,
    report_present: bool,
    evidence_frozen: bool,
    independent_valid: bool,
) -> str:
    valid = (
        normalized_audit_status in {"PASS", "DEGRADED"}
        and diagnosis_status == normalized_audit_status
        and report_present
        and evidence_frozen
        and independent_valid
    )
    return normalized_audit_status if valid else "FAIL"


def build_final_attestation_record(
    *,
    root: Path,
    primary_artifact_id: str,
    primary_artifact_digest: str,
    primary_artifact_url: str,
    audit_status: str,
    run_id: str,
    commit_sha: str,
    final_status_file: Path,
    independent_revalidation_file: Path | None = None,
) -> dict[str, Any]:
    report = root / "expert-team-report.md"
    manifest = root / "artifact-manifest.json"
    bundle = root / "evidence-bundle.json"
    diagnosis_path = root / "execution-diagnosis.json"
    normalized_audit_status = str(audit_status or "FAIL").upper()
    independent_path = independent_revalidation_file
    independent, independent_valid = _load_independent_attestation(independent_path)
    report_required = normalized_audit_status in {"PASS", "DEGRADED"}
    _require_attestation_inputs(
        manifest=manifest,
        bundle=bundle,
        final_status_file=final_status_file,
        report=report,
        diagnosis_path=diagnosis_path,
        report_required=report_required,
        normalized_audit_status=normalized_audit_status,
        primary_artifact_id=primary_artifact_id,
        primary_artifact_digest=primary_artifact_digest,
    )
    diagnosis = load_json_or_default(diagnosis_path, {})
    evidence = load_json_or_default(bundle, {})
    report_present = report.is_file()
    diagnosis_status = (
        str(diagnosis.get("status") or "FAIL").upper()
        if isinstance(diagnosis, Mapping)
        else "FAIL"
    )
    evidence_frozen = bool(
        evidence.get("business_evidence_frozen")
        if isinstance(evidence, Mapping)
        else False
    )
    attestation_status = _attestation_status(
        normalized_audit_status=normalized_audit_status,
        diagnosis_status=diagnosis_status,
        report_present=report_present,
        evidence_frozen=evidence_frozen,
        independent_valid=independent_valid,
    )
    return {
        "version": 2,
        "status": attestation_status,
        "runtime": RUNTIME_VERSION,
        "run_id": int(run_id),
        "commit_sha": commit_sha,
        "primary_artifact": {
            "artifact_id": int(primary_artifact_id),
            "artifact_digest": primary_artifact_digest,
            "artifact_url": primary_artifact_url,
        },
        "audit_status": normalized_audit_status,
        "diagnosis_status": diagnosis_status,
        "evidence_input_sha256": evidence.get("input_sha256") if isinstance(evidence, Mapping) else None,
        "business_evidence_frozen_before_upload": evidence_frozen,
        "independent_artifact_revalidation": dict(independent),
        "independent_artifact_revalidation_valid": independent_valid,
        "independent_artifact_revalidation_sha256": (
            sha256_file(independent_path)
            if independent_path is not None and independent_path.is_file()
            else None
        ),
        "report_required": report_required,
        "report_present": report_present,
        "report_sha256": sha256_file(report) if report_present else None,
        "manifest_sha256": sha256_file(manifest),
        "evidence_bundle_sha256": sha256_file(bundle),
        "final_status_sha256": sha256_file(final_status_file),
        "external_tools_allowed": False,
        "alternate_runtime_fallback": False,
        "evidence_chain": (
            "frozen-business-evidence -> primary-artifact -> "
            "independent-artifact-revalidation -> final-status -> "
            "final-attestation-artifact"
        ),
        "generator": "v5_evidence_bundle.build_final_attestation_record",
        "github_repository": os.getenv("GITHUB_REPOSITORY", ""),
    }
