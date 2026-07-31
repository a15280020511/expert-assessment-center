"""Build the V5 evidence chain from one immutable in-memory input snapshot.

The builder has two deterministic phases:
1. pre-upload: normalize runtime evidence and create the primary manifest;
2. post-upload: inject the immutable Artifact identity into final status and
   final attestation without reinterpreting task, model, cost, or quality data.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

from artifact_manifest import sha256_file, write_manifest

RUNTIME_VERSION = "v5-native-runtime-1"


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _canonical_sha(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(rendered.encode("utf-8")).hexdigest()


def _provider_slug(endpoint: str) -> str:
    return endpoint.rsplit("@", 1)[-1] if "@" in endpoint else endpoint


def _request_provider(request: Mapping[str, Any]) -> str | None:
    provider = request.get("provider")
    if not isinstance(provider, Mapping):
        return None
    values = provider.get("only") or provider.get("order")
    if isinstance(values, list) and values:
        return str(values[0])
    return None


def _attempt_rows(node_results: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    for node in node_results:
        attempts = node.get("attempts", [])
        if isinstance(attempts, list):
            rows.extend(attempt for attempt in attempts if isinstance(attempt, Mapping))
    return tuple(rows)


def _cost_from_attempts(attempts: Sequence[Mapping[str, Any]]) -> float:
    total = 0.0
    for attempt in attempts:
        usage = attempt.get("usage") if isinstance(attempt.get("usage"), Mapping) else {}
        for key in ("cost", "total_cost"):
            try:
                if usage.get(key) is not None:
                    total += max(0.0, float(usage[key]))
                    break
            except (TypeError, ValueError):
                continue
    return round(total, 8)


def _providers(
    attempts: Sequence[Mapping[str, Any]],
    requests: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    attempted = {
        provider
        for request in requests
        for provider in [_request_provider(request)]
        if provider
    }
    substantive: set[str] = set()
    for attempt in attempts:
        provider = str(attempt.get("response_provider") or "").strip()
        endpoint = str(attempt.get("provider_endpoint") or "").strip()
        usage = attempt.get("usage") if isinstance(attempt.get("usage"), Mapping) else {}
        if provider:
            substantive.add(provider)
        elif usage and endpoint:
            substantive.add(_provider_slug(endpoint))
        if endpoint:
            attempted.add(_provider_slug(endpoint))
    return sorted(attempted), sorted(substantive)


@dataclass(frozen=True)
class EvidenceInputs:
    runtime_config: Mapping[str, Any]
    catalog_snapshot: Mapping[str, Any]
    execution_graph: Mapping[str, Any]
    node_results: tuple[Mapping[str, Any], ...]
    call_attempts: tuple[Mapping[str, Any], ...]
    final_report: str
    execution_summary: Mapping[str, Any]
    optimization: Mapping[str, Any]
    ticket: Mapping[str, Any]
    source_request_audit: Mapping[str, Any]

    @classmethod
    def from_directory(cls, root: Path) -> "EvidenceInputs":
        summary = _load(root / "v5-execution-summary.json", {})
        graph = _load(root / "v5-execution-graph.json", {})
        raw_nodes = _load(root / "v5-node-results.json", [])
        nodes = tuple(row for row in raw_nodes if isinstance(row, Mapping)) if isinstance(raw_nodes, list) else ()
        request_audit = _load(root / "v5-request-audit.json", {})
        optimization = _load(root / "v5-optimization.json", {})
        ticket = _load(root / "ticket-status.json", {})
        runtime = _load(root / "v5-runtime-config.json", {})
        snapshot = _load(root / "catalog-snapshot.json", {})
        report_path = root / "v5-final-report.md"
        report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        return cls(
            runtime_config=dict(runtime) if isinstance(runtime, Mapping) else {},
            catalog_snapshot=dict(snapshot) if isinstance(snapshot, Mapping) else {},
            execution_graph=dict(graph) if isinstance(graph, Mapping) else {},
            node_results=nodes,
            call_attempts=_attempt_rows(nodes),
            final_report=report,
            execution_summary=dict(summary) if isinstance(summary, Mapping) else {},
            optimization=dict(optimization) if isinstance(optimization, Mapping) else {},
            ticket=dict(ticket) if isinstance(ticket, Mapping) else {},
            source_request_audit=(
                dict(request_audit) if isinstance(request_audit, Mapping) else {}
            ),
        )


@dataclass(frozen=True)
class ApprovedRun:
    total_calls: int
    recovery_calls: int
    cost_anomaly_usd: float | None

    @property
    def initial_calls(self) -> int:
        return self.total_calls - self.recovery_calls

    def to_dict(self) -> dict[str, Any]:
        return {
            "maximum_total_calls": self.total_calls,
            "maximum_recovery_calls": self.recovery_calls,
            "maximum_initial_calls": self.initial_calls,
            "cost_anomaly_usd": self.cost_anomaly_usd,
        }


class EvidenceBundleBuilder:
    """Create every pre-upload evidence document from one EvidenceInputs object."""

    def __init__(self, inputs: EvidenceInputs, approved: ApprovedRun) -> None:
        if not 1 <= approved.total_calls <= 16:
            raise ValueError("approved total calls must be between 1 and 16")
        if not 0 <= approved.recovery_calls < approved.total_calls:
            raise ValueError("approved recovery calls must leave an initial call")
        self.inputs = inputs
        self.approved = approved

    def build(self, *, require_report: bool) -> dict[str, Any]:
        inputs = self.inputs
        source_audit = inputs.source_request_audit
        requests_raw = source_audit.get("requests")
        requests = [row for row in requests_raw if isinstance(row, Mapping)] if isinstance(requests_raw, list) else []
        request_count = int(source_audit.get("request_count") or len(requests))
        budget = inputs.execution_summary.get("execution_budget")
        budget = dict(budget) if isinstance(budget, Mapping) else {}
        calls_reserved = int(budget.get("calls_reserved") or request_count)
        actual_cost = float(
            inputs.execution_summary.get("actual_cost_usd")
            or budget.get("actual_cost_usd")
            or _cost_from_attempts(inputs.call_attempts)
        )
        if not math.isfinite(actual_cost) or actual_cost < 0:
            actual_cost = _cost_from_attempts(inputs.call_attempts)
        if calls_reserved > self.approved.total_calls or request_count > self.approved.total_calls:
            raise RuntimeError(
                "V5 exceeded approved total paid-call ceiling: "
                f"reserved={calls_reserved}, captured={request_count}, "
                f"approved={self.approved.total_calls}"
            )

        nodes_raw = inputs.execution_graph.get("nodes")
        nodes = [row for row in nodes_raw if isinstance(row, Mapping)] if isinstance(nodes_raw, list) else []
        if len(nodes) > self.approved.initial_calls:
            raise RuntimeError(
                "V5 planned more initial nodes than the approved total leaves after recovery reserve"
            )
        endpoints = sorted({
            str(row.get("provider_endpoint"))
            for row in nodes
            if row.get("provider_endpoint")
        })
        models = sorted({str(row.get("model")) for row in nodes if row.get("model")})
        attempted_providers, substantive_providers = _providers(inputs.call_attempts, requests)

        source_status = str(source_audit.get("status") or "missing")
        request_status = (
            "PASS"
            if source_status == "PASS"
            and request_count == len(requests)
            and request_count == calls_reserved
            else source_status
        )
        request_audit = {
            "version": 5,
            "runtime_version": RUNTIME_VERSION,
            "status": request_status,
            "approved_total_call_ceiling": self.approved.total_calls,
            "approved_recovery_call_ceiling": self.approved.recovery_calls,
            "expected_request_count": calls_reserved,
            "captured_request_count": request_count,
            "requests": requests,
            "external_tools_allowed": False,
            "dynamic_output_allowance_sent": bool(source_audit.get("dynamic_output_allowance_sent")),
            "bounded_output_allowance_sent": bool(source_audit.get("bounded_output_allowance_sent")),
            "artificial_token_ceiling_sent": bool(source_audit.get("artificial_token_ceiling_sent", False)),
            "quality_integrity_status": source_audit.get("quality_integrity_status"),
            "source": "v5-request-audit.json",
        }
        ledger = {
            "version": 5,
            "runtime_version": RUNTIME_VERSION,
            "summary": {
                "call_count": calls_reserved,
                "approved_total_call_ceiling": self.approved.total_calls,
                "approved_recovery_call_ceiling": self.approved.recovery_calls,
                "provider_actual_cost_usd": round(actual_cost, 8),
                "conservative_cost_usd": round(actual_cost, 8),
                "cost_evidence_status": (
                    "provider_actual_or_runtime_reconciled"
                    if actual_cost > 0
                    else "request_attempt_recorded_no_provider_usage"
                ),
                "cost_anomaly_usd": self.approved.cost_anomaly_usd,
                "attempted_providers": attempted_providers,
                "attempted_provider_count": len(attempted_providers),
                "substantive_providers": substantive_providers,
                "substantive_provider_count": len(substantive_providers),
                "all_providers": sorted(set(attempted_providers) | set(substantive_providers)),
                "replacement_calls": int(budget.get("replacements_reserved") or 0),
                "retry_calls": int(budget.get("retries_reserved") or 0),
                "recovery_calls": int(budget.get("recovery_calls_reserved") or 0),
            },
            "node_results": list(inputs.node_results),
        }
        selection = {
            "version": 5,
            "runtime_version": RUNTIME_VERSION,
            "models": models,
            "provider_endpoints": endpoints,
            "node_count": len(nodes),
            "selected_interpretation": inputs.optimization.get("selected_interpretation"),
            "catalog_snapshot_id": inputs.catalog_snapshot.get("catalog_snapshot_id"),
            "cross_task_history_used": False,
        }
        routing = {
            "version": 5,
            "runtime_version": RUNTIME_VERSION,
            "status": "PASS" if inputs.execution_graph else "FAIL",
            "mode": "dynamic-v5-dag",
            "call_consumed": False,
        }
        execution_summary = {
            **dict(inputs.execution_summary),
            "runtime_version": RUNTIME_VERSION,
            "approved_budget": self.approved.to_dict(),
            "catalog_snapshot_id": inputs.catalog_snapshot.get("catalog_snapshot_id"),
            "evidence_input_sha256": self.input_sha256(),
        }
        answer = str(inputs.execution_summary.get("final_answer") or "").strip()
        if require_report and (not inputs.final_report.strip() or not answer):
            raise RuntimeError("V5 did not produce a final report")
        result = {
            "version": 5,
            "runtime_version": RUNTIME_VERSION,
            "status": str(inputs.execution_summary.get("status") or "failed"),
            "completion_mode": str(inputs.execution_summary.get("completion_mode") or "none"),
            "quality_status": str(inputs.execution_summary.get("quality_status") or "failed"),
            "quality_integrity": inputs.execution_summary.get("quality_integrity"),
            "final_answer": answer,
            "actual_cost_usd": round(actual_cost, 8),
            "executor": inputs.execution_summary.get("executor"),
            "work_coverage": inputs.execution_summary.get("work_coverage"),
            "degradation": inputs.execution_summary.get("degradation"),
            "delivery_policy": inputs.execution_summary.get("delivery_policy"),
            "execution_budget": budget,
            "approved_budget": self.approved.to_dict(),
            "catalog_snapshot_id": inputs.catalog_snapshot.get("catalog_snapshot_id"),
            "node_count": len(nodes),
            "model_count": len(models),
            "provider_count": len(substantive_providers),
            "attempted_provider_count": len(attempted_providers),
            "production_entrypoint": True,
            "fallback_used": False,
            "legacy_runtime_present": False,
            "ticket_task_id": inputs.ticket.get("task_id"),
            "evidence_input_sha256": self.input_sha256(),
        }
        runtime = {
            "runtime_version": RUNTIME_VERSION,
            "entrypoint": "v5_production_ticket.py",
            "runtime_constructor": "v5_runtime.ProductionRuntime",
            "global_monkey_patching": False,
            **self.approved.to_dict(),
            "fallback_policy": "fail-closed-no-alternate-runtime",
            "legacy_runtime_present": False,
            "cross_task_history_used": False,
            "catalog_snapshot_id": inputs.catalog_snapshot.get("catalog_snapshot_id"),
        }
        return {
            "request-audit.json": request_audit,
            "call-ledger.json": ledger,
            "model-selection.json": selection,
            "task-routing.json": routing,
            "execution-summary.json": execution_summary,
            "expert-team-result.json": result,
            "production-runtime.json": runtime,
        }

    def input_sha256(self) -> str:
        return _canonical_sha({
            "runtime_config": self.inputs.runtime_config,
            "catalog_snapshot": self.inputs.catalog_snapshot,
            "execution_graph": self.inputs.execution_graph,
            "node_results": self.inputs.node_results,
            "call_attempts": self.inputs.call_attempts,
            "final_report": self.inputs.final_report,
            "execution_summary": self.inputs.execution_summary,
            "optimization": self.inputs.optimization,
            "ticket": self.inputs.ticket,
            "source_request_audit": self.inputs.source_request_audit,
            "approved": self.approved.to_dict(),
        })

    def write(self, root: Path, *, require_report: bool) -> dict[str, Any]:
        root.mkdir(parents=True, exist_ok=True)
        documents = self.build(require_report=require_report)
        for name, document in documents.items():
            _write(root / name, document)
        if require_report:
            (root / "expert-team-report.md").write_text(
                self.inputs.final_report,
                encoding="utf-8",
            )
        snapshot = {
            "schema_version": "v5-evidence-bundle-1",
            "runtime_version": RUNTIME_VERSION,
            "input_sha256": self.input_sha256(),
            "approved": self.approved.to_dict(),
            "catalog_snapshot_id": self.inputs.catalog_snapshot.get("catalog_snapshot_id"),
            "generated_documents": {
                name: _canonical_sha(document)
                for name, document in sorted(documents.items())
            },
            "business_evidence_frozen": True,
            "post_upload_fields_pending": [
                "primary_artifact_id",
                "primary_artifact_digest",
                "primary_artifact_url",
            ],
        }
        _write(root / "evidence-bundle.json", snapshot)
        write_manifest(root)
        return documents["expert-team-result.json"]


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
            value = _load(root / name, default or {})
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
) -> dict[str, Any]:
    audit = inputs.diagnosis
    failures = list(audit.get("failures") or [])
    degradations = list(audit.get("degradations") or [])
    status = str(audit.get("status") or "FAIL")
    if audit_outcome != "success":
        status = "FAIL"
        failures.append(f"V5 audit step outcome is {audit_outcome}")
    if manifest_outcome != "success":
        status = "FAIL"
        failures.append(f"primary artifact manifest step outcome is {manifest_outcome}")
    if ticket_upload_outcome != "success":
        status = "FAIL"
        failures.append(f"primary ticket artifact upload outcome is {ticket_upload_outcome}")
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


def render_final_status_markdown(record: Mapping[str, Any]) -> str:
    status = str(record.get("status") or "FAIL")
    heading = {
        "PASS": "EXECUTION_COMPLETED",
        "DEGRADED": "EXECUTION_DEGRADED",
        "FAIL": "EXECUTION_FAILED",
    }.get(status, "EXECUTION_FAILED")
    artifact = record.get("primary_artifact")
    artifact = artifact if isinstance(artifact, Mapping) else {}
    providers = record.get("substantive_providers")
    providers = providers if isinstance(providers, list) else []
    lines = [
        f"## {heading}",
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
        f"- Cost anomaly stop: `{record.get('cost_anomaly_usd') if record.get('cost_anomaly_usd') is not None else 'account/estimate guard only'}`",
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
    if artifact.get("artifact_url"):
        lines.append(f"- Primary Artifact: {artifact['artifact_url']}")
    primary = record.get("primary_failure")
    primary = primary if isinstance(primary, Mapping) else {}
    if primary.get("code") or primary.get("message"):
        lines.extend([
            "",
            "### Primary diagnosis",
            "",
            f"- Error code: `{primary.get('code') or 'NONE'}`",
            f"- Stage: `{primary.get('stage') or 'v5-production'}`",
            f"- Message: {primary.get('message') or 'No direct error message was recorded.'}",
            f"- Retryable: `{str(bool(primary.get('retryable'))).lower()}`",
        ])
    failures = record.get("failures")
    if isinstance(failures, list) and failures:
        lines.extend(["", "### Failure reasons", ""] + [f"- {row}" for row in failures])
    degradations = record.get("degradations")
    if isinstance(degradations, list) and degradations:
        lines.extend(["", "### Degradation reasons", ""] + [f"- {row}" for row in degradations])
    if status == "PASS":
        lines.extend(["", "V5 动态专家图已完成生产任务；业务证据在主 Artifact 上传前已冻结，上传后只注入 Artifact 身份并生成最终证明。"])
    elif status == "DEGRADED":
        lines.extend(["", "V5 已交付，但发生受控降级。不得表述为完整正常 PASS；系统不会调用其他运行时。"])
    else:
        lines.extend(["", "本次 V5 运行不得表述为成功。系统已失败关闭，不调用其他运行时。"])
    return "\n".join(lines) + "\n"


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
) -> dict[str, Any]:
    report = root / "expert-team-report.md"
    manifest = root / "artifact-manifest.json"
    bundle = root / "evidence-bundle.json"
    diagnosis_path = root / "execution-diagnosis.json"
    normalized_audit_status = str(audit_status or "FAIL").upper()
    report_required = normalized_audit_status in {"PASS", "DEGRADED"}
    required_paths = (manifest, bundle, final_status_file)
    if not all(item.is_file() for item in required_paths):
        raise RuntimeError("manifest, evidence bundle, and final status must exist")
    if report_required and not report.is_file():
        raise RuntimeError("successful or degraded execution requires a report")
    if normalized_audit_status == "FAIL" and not diagnosis_path.is_file():
        raise RuntimeError("failed execution requires deterministic diagnosis evidence")
    if not primary_artifact_id or not primary_artifact_digest:
        raise RuntimeError("primary artifact identity is required")
    diagnosis = _load(diagnosis_path, {})
    evidence = _load(bundle, {})
    report_present = report.is_file()
    return {
        "version": 2,
        "runtime": RUNTIME_VERSION,
        "run_id": int(run_id),
        "commit_sha": commit_sha,
        "primary_artifact": {
            "artifact_id": int(primary_artifact_id),
            "artifact_digest": primary_artifact_digest,
            "artifact_url": primary_artifact_url,
        },
        "audit_status": normalized_audit_status,
        "diagnosis_status": diagnosis.get("status") if isinstance(diagnosis, Mapping) else None,
        "evidence_input_sha256": evidence.get("input_sha256") if isinstance(evidence, Mapping) else None,
        "business_evidence_frozen_before_upload": bool(
            evidence.get("business_evidence_frozen") if isinstance(evidence, Mapping) else False
        ),
        "report_required": report_required,
        "report_present": report_present,
        "report_sha256": sha256_file(report) if report_present else None,
        "manifest_sha256": sha256_file(manifest),
        "evidence_bundle_sha256": sha256_file(bundle),
        "final_status_sha256": sha256_file(final_status_file),
        "external_tools_allowed": False,
        "alternate_runtime_fallback": False,
        "evidence_chain": "frozen-business-evidence -> primary-artifact -> final-status -> final-attestation-artifact",
        "generator": "v5_evidence_bundle.build_final_attestation_record",
        "github_repository": os.getenv("GITHUB_REPOSITORY", ""),
    }
