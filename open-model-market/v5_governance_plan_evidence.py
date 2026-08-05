"""Normalize evidence for governance-selected expert execution."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

from artifact_manifest import write_manifest
from v5_governance_model_plan import validate_governance_model_plan
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

RUNTIME_VERSION = "v5-governance-plan-runtime-1"
AUTHORITY = "decision-system-governance"


def _source(root: Path) -> dict[str, Any]:
    graph = load_mapping(root, "v5-execution-graph.json")
    request_audit = load_mapping(root, "v5-request-audit.json")
    return {
        "runtime": load_mapping(root, "production-runtime.json"),
        "runtime_config": load_mapping(root, "v5-runtime-config.json"),
        "catalog": load_mapping(root, "catalog-snapshot.json"),
        "graph": graph,
        "summary": load_mapping(root, "v5-execution-summary.json"),
        "selection": load_mapping(root, "v5-price-ranked-selection.json"),
        "request_audit": request_audit,
        "governance": load_mapping(root, "v5-governance-calls.json"),
        "ticket_status": load_mapping(root, "ticket-status.json"),
        "ticket": load_mapping(root, "ticket.json"),
        "plan": load_mapping(root, "governance-model-plan.json"),
        "nodes": mapping_rows(
            load_json_or_default(root / "v5-node-results.json", [])
        ),
        "requests": mapping_rows(request_audit.get("requests")),
        "graph_nodes": mapping_rows(graph.get("nodes")),
        "report": report_text(root),
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _validate_authority(source: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    runtime = source["runtime"]
    config = source["runtime_config"]
    selection = source["selection"]
    _require(
        runtime.get("runtime_version") == RUNTIME_VERSION,
        "governance-plan production runtime envelope is missing",
    )
    _require(
        config.get("selection_authority") == AUTHORITY,
        "runtime config does not preserve governance selection authority",
    )
    _require(
        config.get("model_selection_performed_locally") is False,
        "expert runtime reports local model selection",
    )
    _require(
        config.get("model_reranking_performed_locally") is False,
        "expert runtime reports local model reranking",
    )
    _require(
        config.get("governance_model_plan_sha256") == plan["plan_sha256"],
        "runtime config model plan digest mismatch",
    )
    _require(
        selection.get("status") == "PASS",
        "governance plan materialization did not pass",
    )
    _require(
        selection.get("selection_authority") == AUTHORITY,
        "selection evidence authority mismatch",
    )
    _require(
        selection.get("model_selection_performed_locally") is False,
        "selection evidence reports local model selection",
    )
    _require(
        int(source["governance"].get("actual_governance_calls") or 0) == 0,
        "expert center governance calls must equal zero",
    )


def _validate_graph_and_requests(
    source: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    maximum_nodes: int,
) -> int:
    request_audit = source["request_audit"]
    requests = source["requests"]
    _require(request_audit.get("status") == "PASS", "complete request audit did not pass")
    _require(
        all(canonical_provider_lock(row) for row in requests),
        "one or more expert requests lacks an exact provider lock",
    )
    _require(
        3 <= len(source["graph_nodes"]) <= min(6, maximum_nodes),
        "expert graph size violates approved bounds",
    )
    graph_models = tuple(models_from_graph(source["graph"]))
    planned_models = tuple(
        str(row.get("model") or "") for row in plan["selected_models"]
    )
    _require(
        graph_models == planned_models,
        "executed graph models differ from governance model plan",
    )
    budget = source["summary"].get("execution_budget")
    budget = dict(budget) if isinstance(budget, Mapping) else {}
    call_count = int(budget.get("calls_reserved") or len(requests))
    _require(
        len(requests) == call_count,
        "request audit and expert call ledger disagree",
    )
    return call_count


def _validated_result(
    source: Mapping[str, Any],
    *,
    total: int,
    recovery: int,
    require_report: bool,
) -> tuple[dict[str, Any], int, float, str]:
    plan = validate_governance_model_plan(source["ticket"], source["plan"])
    _validate_authority(source, plan)
    call_count = _validate_graph_and_requests(
        source,
        plan,
        maximum_nodes=total - recovery,
    )
    _require(call_count <= total, "approved total model-call ceiling exceeded")
    actual_cost = float(source["summary"].get("actual_cost_usd") or 0.0)
    _require(
        math.isfinite(actual_cost) and actual_cost >= 0,
        "actual execution cost is invalid",
    )
    answer = str(source["summary"].get("final_answer") or "").strip()
    _require(
        not require_report or (bool(source["report"].strip()) and bool(answer)),
        "governance-plan runtime did not produce a final report",
    )
    return plan, call_count, actual_cost, answer


def _approved_budget(
    total: int, recovery: int, cost_anomaly_usd: float | None
) -> dict[str, Any]:
    return {
        "maximum_total_calls": total,
        "governance_calls_reserved": 0,
        "maximum_expert_calls": total,
        "maximum_recovery_calls": recovery,
        "maximum_expert_initial_calls": total - recovery,
        "cost_anomaly_usd": cost_anomaly_usd,
    }


def _evidence_sha(
    source: Mapping[str, Any],
    plan: Mapping[str, Any],
    approved: Mapping[str, Any],
) -> str:
    return canonical_json_sha(
        {
            "runtime": source["runtime"],
            "runtime_config": source["runtime_config"],
            "catalog": source["catalog"],
            "graph": source["graph"],
            "nodes": list(source["nodes"]),
            "summary": source["summary"],
            "selection": source["selection"],
            "request_audit": source["request_audit"],
            "governance": source["governance"],
            "ticket_status": source["ticket_status"],
            "plan": plan,
            "approved": approved,
            "report": source["report"],
        }
    )


def _request_document(
    source: Mapping[str, Any], total: int
) -> dict[str, Any]:
    requests = source["requests"]
    return {
        **source["request_audit"],
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS",
        "request_count": len(requests),
        "approved_total_call_ceiling": total,
        "governance_request_count": 0,
        "expert_request_count": len(requests),
        "provider_locks_valid": True,
        "external_tools_allowed": False,
        "provider_fallback_allowed": False,
        "selection_authority": AUTHORITY,
        "model_selection_performed_locally": False,
    }


def _ledger_document(
    source: Mapping[str, Any],
    *,
    call_count: int,
    total: int,
    recovery: int,
    actual_cost: float,
    cost_anomaly_usd: float | None,
    exceeded: bool,
) -> dict[str, Any]:
    providers = providers_from_requests(source["requests"])
    return {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "summary": {
            "call_count": call_count,
            "governance_calls_in_expert_center": 0,
            "expert_calls": call_count,
            "approved_total_call_ceiling": total,
            "approved_recovery_call_ceiling": recovery,
            "provider_actual_cost_usd": round(actual_cost, 8),
            "conservative_cost_usd": round(actual_cost, 8),
            "cost_anomaly_usd": cost_anomaly_usd,
            "cost_advisory_exceeded": exceeded,
            "substantive_providers": list(providers),
            "substantive_provider_count": len(providers),
        },
        "governance": source["governance"],
        "node_results": list(source["nodes"]),
    }


def _selection_document(
    source: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "selection_authority": AUTHORITY,
        "governance_model_plan_sha256": plan["plan_sha256"],
        "model_selection_performed_locally": False,
        "model_reranking_performed_locally": False,
        "model_substitution_allowed": False,
        "provider_resolution_only": True,
        "expert_models": list(models_from_graph(source["graph"])),
        "node_count": len(source["graph_nodes"]),
        "catalog_snapshot_id": source["catalog"].get("catalog_snapshot_id"),
        "networkx_used": True,
        "cross_task_history_used": False,
    }


def _summary_document(
    source: Mapping[str, Any],
    plan: Mapping[str, Any],
    approved: Mapping[str, Any],
    evidence_sha: str,
    cost_anomaly_usd: float | None,
    exceeded: bool,
) -> dict[str, Any]:
    return {
        **source["summary"],
        "runtime_version": RUNTIME_VERSION,
        "approved_budget": approved,
        "catalog_snapshot_id": source["catalog"].get("catalog_snapshot_id"),
        "evidence_input_sha256": evidence_sha,
        "selection_authority": AUTHORITY,
        "governance_model_plan_sha256": plan["plan_sha256"],
        "model_selection_performed_locally": False,
        "resource_governance": {
            "mode": "prompt-led-soft-governance",
            "cost_advisory_usd": cost_anomaly_usd,
            "cost_advisory_exceeded": exceeded,
            "cost_threshold_can_invalidate_result": False,
            "local_token_ceiling_enforced": False,
        },
    }


def _result_document(
    source: Mapping[str, Any],
    plan: Mapping[str, Any],
    approved: Mapping[str, Any],
    evidence_sha: str,
    answer: str,
    actual_cost: float,
) -> dict[str, Any]:
    summary = source["summary"]
    return {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": str(summary.get("status") or "failed"),
        "completion_mode": str(summary.get("completion_mode") or "none"),
        "quality_status": str(summary.get("quality_status") or "failed"),
        "quality_integrity": summary.get("quality_integrity"),
        "final_answer": answer,
        "actual_cost_usd": round(actual_cost, 8),
        "executor": summary.get("executor"),
        "work_coverage": summary.get("work_coverage"),
        "degradation": summary.get("degradation"),
        "execution_budget": summary.get("execution_budget"),
        "approved_budget": approved,
        "catalog_snapshot_id": source["catalog"].get("catalog_snapshot_id"),
        "node_count": len(source["graph_nodes"]),
        "model_count": len(models_from_graph(source["graph"])),
        "provider_count": len(providers_from_requests(source["requests"])),
        "selection_authority": AUTHORITY,
        "governance_model_plan_sha256": plan["plan_sha256"],
        "model_selection_performed_locally": False,
        "production_entrypoint": True,
        "fallback_used": False,
        "legacy_runtime_present": False,
        "cross_task_history_used": False,
        "ticket_task_id": source["ticket_status"].get("task_id"),
        "evidence_input_sha256": evidence_sha,
    }


def _documents(
    source: Mapping[str, Any],
    plan: Mapping[str, Any],
    approved: Mapping[str, Any],
    *,
    total: int,
    recovery: int,
    call_count: int,
    actual_cost: float,
    answer: str,
    cost_anomaly_usd: float | None,
    exceeded: bool,
    evidence_sha: str,
) -> dict[str, dict[str, Any]]:
    return {
        "request-audit.json": _request_document(source, total),
        "call-ledger.json": _ledger_document(
            source,
            call_count=call_count,
            total=total,
            recovery=recovery,
            actual_cost=actual_cost,
            cost_anomaly_usd=cost_anomaly_usd,
            exceeded=exceeded,
        ),
        "model-selection.json": _selection_document(source, plan),
        "task-routing.json": {
            "version": 5,
            "runtime_version": RUNTIME_VERSION,
            "status": "PASS",
            "mode": "governance-selected-networkx-dag",
            "topology": (
                "parallel-independent-analysis -> cross-review -> final-synthesis"
            ),
            "selection_authority": AUTHORITY,
            "model_loop_allowed": False,
        },
        "execution-summary.json": _summary_document(
            source,
            plan,
            approved,
            evidence_sha,
            cost_anomaly_usd,
            exceeded,
        ),
        "expert-team-result.json": _result_document(
            source,
            plan,
            approved,
            evidence_sha,
            answer,
            actual_cost,
        ),
    }


def _write_bundle(
    root: Path,
    source: Mapping[str, Any],
    plan: Mapping[str, Any],
    approved: Mapping[str, Any],
    evidence_sha: str,
    documents: Mapping[str, Mapping[str, Any]],
) -> None:
    for name, document in documents.items():
        write_json(root / name, document)
    if source["report"].strip():
        (root / "expert-team-report.md").write_text(
            source["report"], encoding="utf-8"
        )
    write_json(
        root / "evidence-bundle.json",
        {
            "schema_version": "v5-governance-plan-evidence-bundle-1",
            "runtime_version": RUNTIME_VERSION,
            "input_sha256": evidence_sha,
            "approved": approved,
            "catalog_snapshot_id": source["catalog"].get("catalog_snapshot_id"),
            "governance_model_plan_sha256": plan["plan_sha256"],
            "selection_authority": AUTHORITY,
            "model_selection_performed_locally": False,
            "generated_documents": {
                name: canonical_json_sha(document)
                for name, document in sorted(documents.items())
            },
            "business_evidence_frozen": True,
            "governance_model_calls_in_expert_center": 0,
            "post_upload_fields_pending": [
                "primary_artifact_id",
                "primary_artifact_digest",
                "primary_artifact_url",
            ],
        },
    )
    write_manifest(root)


def normalize_governance_plan_evidence(
    root: Path,
    *,
    approved_total_calls: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
    require_report: bool,
) -> dict[str, Any]:
    total = int(approved_total_calls)
    recovery = int(approved_recovery_calls)
    if not 4 <= total <= 16 or not 0 <= recovery < total:
        raise ValueError("approved call budget is invalid")
    source = _source(root)
    plan, call_count, actual_cost, answer = _validated_result(
        source,
        total=total,
        recovery=recovery,
        require_report=require_report,
    )
    approved = _approved_budget(total, recovery, cost_anomaly_usd)
    evidence_sha = _evidence_sha(source, plan, approved)
    exceeded = bool(
        cost_anomaly_usd is not None
        and actual_cost > float(cost_anomaly_usd) + 1e-12
    )
    documents = _documents(
        source,
        plan,
        approved,
        total=total,
        recovery=recovery,
        call_count=call_count,
        actual_cost=actual_cost,
        answer=answer,
        cost_anomaly_usd=cost_anomaly_usd,
        exceeded=exceeded,
        evidence_sha=evidence_sha,
    )
    _write_bundle(root, source, plan, approved, evidence_sha, documents)
    return documents["expert-team-result.json"]


__all__ = ["RUNTIME_VERSION", "normalize_governance_plan_evidence"]
