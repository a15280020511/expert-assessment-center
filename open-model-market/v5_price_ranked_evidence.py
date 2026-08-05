"""Normalize evidence for governance-selected expert execution."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from artifact_manifest import write_manifest
from v5_governance_selection import SELECTION_AUTHORITY
from v5_json_io import load_json_or_default, write_json
from v5_price_ranked_support import (
    canonical_json_sha,
    load_mapping,
    mapping_rows,
    models_from_graph,
    providers_from_requests,
    report_text,
)
from v5_provider_lock import canonical_provider_lock

RUNTIME_VERSION = "v5-price-ranked-runtime-1"


@dataclass(frozen=True)
class ApprovedContext:
    total_calls: int
    recovery_calls: int
    cost_anomaly_usd: float | None

    @classmethod
    def build(
        cls,
        total_calls: int,
        recovery_calls: int,
        cost_anomaly_usd: float | None,
    ) -> "ApprovedContext":
        total = int(total_calls)
        recovery = int(recovery_calls)
        if not 4 <= total <= 16:
            raise ValueError("approved total calls must be between 4 and 16")
        if not 0 <= recovery < total or total - recovery < 3:
            raise ValueError("approved recovery reserve is invalid")
        return cls(total, recovery, cost_anomaly_usd)

    @property
    def initial_calls(self) -> int:
        return self.total_calls - self.recovery_calls

    def to_dict(self) -> dict[str, Any]:
        return {
            "maximum_total_calls": self.total_calls,
            "governance_calls_reserved": 0,
            "maximum_expert_calls": self.total_calls,
            "maximum_recovery_calls": self.recovery_calls,
            "maximum_expert_initial_calls": self.initial_calls,
            "cost_anomaly_usd": self.cost_anomaly_usd,
        }


@dataclass(frozen=True)
class EvidenceSource:
    runtime: Mapping[str, Any]
    runtime_config: Mapping[str, Any]
    plan: Mapping[str, Any]
    plan_validation: Mapping[str, Any]
    catalog: Mapping[str, Any]
    graph: Mapping[str, Any]
    summary: Mapping[str, Any]
    selection: Mapping[str, Any]
    request_audit: Mapping[str, Any]
    governance: Mapping[str, Any]
    ticket: Mapping[str, Any]
    nodes: tuple[Mapping[str, Any], ...]
    requests: tuple[Mapping[str, Any], ...]
    graph_nodes: tuple[Mapping[str, Any], ...]
    report: str

    @classmethod
    def from_root(cls, root: Path) -> "EvidenceSource":
        graph = load_mapping(root, "v5-execution-graph.json")
        request_audit = load_mapping(root, "v5-request-audit.json")
        return cls(
            runtime=load_mapping(root, "production-runtime.json"),
            runtime_config=load_mapping(root, "v5-runtime-config.json"),
            plan=load_mapping(root, "governance-selection.json"),
            plan_validation=load_mapping(
                root, "governance-selection-validation.json"
            ),
            catalog=load_mapping(root, "catalog-snapshot.json"),
            graph=graph,
            summary=load_mapping(root, "v5-execution-summary.json"),
            selection=load_mapping(root, "v5-price-ranked-selection.json"),
            request_audit=request_audit,
            governance=load_mapping(root, "v5-governance-calls.json"),
            ticket=load_mapping(root, "ticket-status.json"),
            nodes=mapping_rows(
                load_json_or_default(root / "v5-node-results.json", [])
            ),
            requests=mapping_rows(request_audit.get("requests")),
            graph_nodes=mapping_rows(graph.get("nodes")),
            report=report_text(root),
        )


@dataclass(frozen=True)
class PreparedEvidence:
    budget: Mapping[str, Any]
    call_count: int
    actual_cost: float
    answer: str
    providers: tuple[str, ...]
    expert_models: tuple[str, ...]
    cost_exceeded: bool
    evidence_input_sha: str


def _validate_selection_boundary(source: EvidenceSource) -> None:
    if source.runtime.get("runtime_version") != RUNTIME_VERSION:
        raise RuntimeError("production runtime envelope is missing")
    for document, label in (
        (source.runtime, "production runtime"),
        (source.runtime_config, "runtime config"),
        (source.selection, "selection audit"),
        (source.plan_validation, "governance selection validation"),
    ):
        if document.get("selection_authority") != SELECTION_AUTHORITY:
            raise RuntimeError(f"{label} selection authority is not governance")
    if source.plan.get("selection_authority") != SELECTION_AUTHORITY:
        raise RuntimeError("governance plan authority is invalid")
    plan_sha = str(source.plan.get("plan_sha256") or "")
    if not plan_sha:
        raise RuntimeError("governance plan digest is missing")
    for document, field in (
        (source.runtime, "selection_plan_sha256"),
        (source.runtime_config, "selection_plan_sha256"),
        (source.selection, "selection_plan_sha256"),
        (source.plan_validation, "plan_sha256"),
    ):
        if str(document.get(field) or "") != plan_sha:
            raise RuntimeError("governance plan digest is inconsistent")
    if source.plan_validation.get("status") != "PASS":
        raise RuntimeError("governance selection validation did not pass")
    forbidden_true = (
        (source.runtime, "expert_center_selection_performed"),
        (source.runtime, "expert_center_catalog_fetch_performed"),
        (source.runtime_config, "expert_center_selection_present"),
        (source.runtime_config, "expert_center_catalog_fetch_present"),
        (source.selection, "expert_center_selection_performed"),
        (source.selection, "expert_center_catalog_fetch_performed"),
        (source.selection, "local_fallback_used"),
    )
    if any(document.get(field) is not False for document, field in forbidden_true):
        raise RuntimeError("expert center selection, catalog access or fallback is not disabled")
    if source.runtime.get("local_selection_fallback_allowed") is not False:
        raise RuntimeError("local selection fallback is not explicitly forbidden")
    if int(source.governance.get("actual_governance_calls") or 0) != 0:
        raise RuntimeError("governance inference calls must equal zero")
    if int(source.governance.get("claude_red_team_calls") or 0) != 0:
        raise RuntimeError("Claude calls must equal zero")


def _validate_execution(
    source: EvidenceSource,
    approved: ApprovedContext,
    require_report: bool,
) -> tuple[Mapping[str, Any], int, float, str]:
    expected_nodes = int(source.plan.get("selected_expert_count") or 0)
    if not 3 <= expected_nodes <= min(6, approved.initial_calls):
        raise RuntimeError("governance-selected node count violates approved bounds")
    if len(source.graph_nodes) != expected_nodes:
        raise RuntimeError("execution graph differs from governance-selected node count")
    if source.selection.get("status") != "PASS":
        raise RuntimeError("governance selection audit did not pass")
    if source.request_audit.get("status") != "PASS":
        raise RuntimeError("complete request audit did not pass")
    if any(not canonical_provider_lock(row) for row in source.requests):
        raise RuntimeError("one or more expert requests lacks an exact provider lock")
    raw_budget = source.summary.get("execution_budget")
    budget = dict(raw_budget) if isinstance(raw_budget, Mapping) else {}
    call_count = int(budget.get("calls_reserved") or len(source.requests))
    if len(source.requests) != call_count:
        raise RuntimeError("request audit and expert call ledger disagree")
    if call_count > approved.total_calls:
        raise RuntimeError("approved total model-call ceiling exceeded")
    actual_cost = float(source.summary.get("actual_cost_usd") or 0.0)
    if not math.isfinite(actual_cost) or actual_cost < 0:
        raise RuntimeError("actual execution cost is invalid")
    answer = str(source.summary.get("final_answer") or "").strip()
    if require_report and (not source.report.strip() or not answer):
        raise RuntimeError("governance-selected runtime did not produce a final report")
    return budget, call_count, actual_cost, answer


def _input_payload(
    source: EvidenceSource,
    approved: ApprovedContext,
) -> dict[str, Any]:
    return {
        "runtime": source.runtime,
        "runtime_config": source.runtime_config,
        "governance_selection": source.plan,
        "governance_selection_validation": source.plan_validation,
        "catalog": source.catalog,
        "graph": source.graph,
        "nodes": source.nodes,
        "summary": source.summary,
        "selection": source.selection,
        "request_audit": source.request_audit,
        "governance": source.governance,
        "ticket": source.ticket,
        "approved": approved.to_dict(),
        "report": source.report,
    }


def _prepare(
    source: EvidenceSource,
    approved: ApprovedContext,
    require_report: bool,
) -> PreparedEvidence:
    _validate_selection_boundary(source)
    budget, call_count, actual_cost, answer = _validate_execution(
        source, approved, require_report
    )
    cost_exceeded = bool(
        approved.cost_anomaly_usd is not None
        and actual_cost > float(approved.cost_anomaly_usd) + 1e-12
    )
    return PreparedEvidence(
        budget=budget,
        call_count=call_count,
        actual_cost=actual_cost,
        answer=answer,
        providers=providers_from_requests(source.requests),
        expert_models=models_from_graph(source.graph),
        cost_exceeded=cost_exceeded,
        evidence_input_sha=canonical_json_sha(_input_payload(source, approved)),
    )


def _documents(
    source: EvidenceSource,
    approved: ApprovedContext,
    prepared: PreparedEvidence,
) -> dict[str, dict[str, Any]]:
    plan_sha = source.plan["plan_sha256"]
    request_document = {
        **source.request_audit,
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS",
        "request_count": len(source.requests),
        "approved_total_call_ceiling": approved.total_calls,
        "governance_request_count": 0,
        "expert_request_count": len(source.requests),
        "provider_locks_valid": True,
        "external_tools_allowed": False,
        "provider_fallback_allowed": False,
        "selection_authority": SELECTION_AUTHORITY,
        "selection_plan_sha256": plan_sha,
        "expert_center_selection_performed": False,
        "expert_center_catalog_fetch_performed": False,
    }
    ledger_document = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "summary": {
            "call_count": prepared.call_count,
            "governance_calls": 0,
            "expert_calls": prepared.call_count,
            "approved_total_call_ceiling": approved.total_calls,
            "approved_recovery_call_ceiling": approved.recovery_calls,
            "provider_actual_cost_usd": round(prepared.actual_cost, 8),
            "conservative_cost_usd": round(prepared.actual_cost, 8),
            "cost_anomaly_usd": approved.cost_anomaly_usd,
            "cost_advisory_usd": approved.cost_anomaly_usd,
            "cost_advisory_exceeded": prepared.cost_exceeded,
            "cost_threshold_can_invalidate_result": False,
            "substantive_providers": list(prepared.providers),
            "substantive_provider_count": len(prepared.providers),
            "replacement_calls": int(
                prepared.budget.get("replacements_reserved") or 0
            ),
            "retry_calls": int(prepared.budget.get("retries_reserved") or 0),
            "recovery_calls": int(
                prepared.budget.get("recovery_calls_reserved") or 0
            ),
        },
        "governance": source.governance,
        "node_results": list(source.nodes),
    }
    selection_document = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "selection_authority": SELECTION_AUTHORITY,
        "selection_source_repository": source.plan["source_repository"],
        "selection_source_commit": source.plan.get("source_commit", ""),
        "selection_plan_sha256": plan_sha,
        "selection_policy": source.plan.get("selection_rule"),
        "expert_models": list(prepared.expert_models),
        "node_count": len(source.graph_nodes),
        "catalog_snapshot_id": source.catalog.get("catalog_snapshot_id"),
        "networkx_used": True,
        "expert_center_selection_performed": False,
        "expert_center_catalog_fetch_performed": False,
        "local_fallback_used": False,
        "optimizer_used": False,
        "agent_framework_used": False,
        "cross_task_history_used": False,
    }
    routing_document = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS",
        "mode": "governance-selected-networkx-dag",
        "topology": "governance-declared-dag -> expert-validation -> execution",
        "selection_authority": SELECTION_AUTHORITY,
        "selection_plan_sha256": plan_sha,
        "expert_center_selection_performed": False,
        "local_fallback_used": False,
        "claude_mechanism_enabled": False,
        "model_loop_allowed": False,
    }
    summary_document = {
        **source.summary,
        "runtime_version": RUNTIME_VERSION,
        "approved_budget": approved.to_dict(),
        "catalog_snapshot_id": source.catalog.get("catalog_snapshot_id"),
        "evidence_input_sha256": prepared.evidence_input_sha,
        "selection_authority": SELECTION_AUTHORITY,
        "selection_plan_sha256": plan_sha,
        "expert_center_selection_performed": False,
        "expert_center_catalog_fetch_performed": False,
        "local_fallback_used": False,
        "governance": {
            "actual_calls": 0,
            "reserved_calls": 0,
            "selection_authority": SELECTION_AUTHORITY,
            "selection_plan_sha256": plan_sha,
            "claude_mechanism_enabled": False,
        },
        "resource_governance": {
            "mode": "prompt-led-soft-governance",
            "cost_advisory_usd": approved.cost_anomaly_usd,
            "cost_advisory_exceeded": prepared.cost_exceeded,
            "cost_threshold_can_invalidate_result": False,
            "local_token_ceiling_enforced": False,
        },
    }
    result_document = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": str(source.summary.get("status") or "failed"),
        "completion_mode": str(source.summary.get("completion_mode") or "none"),
        "quality_status": str(source.summary.get("quality_status") or "failed"),
        "quality_integrity": source.summary.get("quality_integrity"),
        "final_answer": prepared.answer,
        "actual_cost_usd": round(prepared.actual_cost, 8),
        "executor": source.summary.get("executor"),
        "work_coverage": source.summary.get("work_coverage"),
        "degradation": source.summary.get("degradation"),
        "execution_budget": prepared.budget,
        "approved_budget": approved.to_dict(),
        "selection_authority": SELECTION_AUTHORITY,
        "selection_source_repository": source.plan["source_repository"],
        "selection_source_commit": source.plan.get("source_commit", ""),
        "selection_plan_sha256": plan_sha,
        "expert_center_selection_performed": False,
        "expert_center_catalog_fetch_performed": False,
        "local_fallback_used": False,
        "governance": {
            "actual_calls": 0,
            "reserved_calls": 0,
            "selection_authority": SELECTION_AUTHORITY,
            "selection_plan_sha256": plan_sha,
            "claude_mechanism_enabled": False,
        },
        "catalog_snapshot_id": source.catalog.get("catalog_snapshot_id"),
        "node_count": len(source.graph_nodes),
        "model_count": len(prepared.expert_models),
        "provider_count": len(prepared.providers),
        "production_entrypoint": True,
        "fallback_used": False,
        "legacy_runtime_present": False,
        "claude_mechanism_enabled": False,
        "cross_task_history_used": False,
        "ticket_task_id": source.ticket.get("task_id"),
        "evidence_input_sha256": prepared.evidence_input_sha,
    }
    return {
        "request-audit.json": request_document,
        "call-ledger.json": ledger_document,
        "model-selection.json": selection_document,
        "task-routing.json": routing_document,
        "execution-summary.json": summary_document,
        "expert-team-result.json": result_document,
    }


def _write_bundle(
    root: Path,
    source: EvidenceSource,
    approved: ApprovedContext,
    prepared: PreparedEvidence,
    documents: Mapping[str, Mapping[str, Any]],
) -> None:
    write_json(
        root / "evidence-bundle.json",
        {
            "schema_version": "v5-evidence-bundle-4",
            "runtime_version": RUNTIME_VERSION,
            "input_sha256": prepared.evidence_input_sha,
            "approved": approved.to_dict(),
            "catalog_snapshot_id": source.catalog.get("catalog_snapshot_id"),
            "selection_authority": SELECTION_AUTHORITY,
            "selection_plan_sha256": source.plan["plan_sha256"],
            "generated_documents": {
                name: canonical_json_sha(document)
                for name, document in sorted(documents.items())
            },
            "business_evidence_frozen": True,
            "governance_model_calls": 0,
            "expert_center_selection_performed": False,
            "expert_center_catalog_fetch_performed": False,
            "local_fallback_used": False,
            "claude_mechanism_enabled": False,
            "post_upload_fields_pending": [
                "primary_artifact_id",
                "primary_artifact_digest",
                "primary_artifact_url",
            ],
        },
    )


def normalize_price_ranked_evidence(
    root: Path,
    *,
    approved_total_calls: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
    require_report: bool,
) -> dict[str, Any]:
    approved = ApprovedContext.build(
        approved_total_calls, approved_recovery_calls, cost_anomaly_usd
    )
    source = EvidenceSource.from_root(root)
    prepared = _prepare(source, approved, require_report)
    documents = _documents(source, approved, prepared)
    for name, document in documents.items():
        write_json(root / name, document)
    if source.report.strip():
        (root / "expert-team-report.md").write_text(
            source.report, encoding="utf-8"
        )
    _write_bundle(root, source, approved, prepared, documents)
    write_manifest(root)
    return documents["expert-team-result.json"]


__all__ = ["RUNTIME_VERSION", "normalize_price_ranked_evidence"]
