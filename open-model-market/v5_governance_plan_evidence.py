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


def _validate(
    source: Mapping[str, Any],
    *,
    approved_total_calls: int,
    approved_recovery_calls: int,
    require_report: bool,
) -> tuple[int, float, str]:
    runtime = source["runtime"]
    config = source["runtime_config"]
    selection = source["selection"]
    request_audit = source["request_audit"]
    governance = source["governance"]
    summary = source["summary"]
    ticket = source["ticket"]
    plan = validate_governance_model_plan(ticket, source["plan"])

    if runtime.get("runtime_version") != RUNTIME_VERSION:
        raise RuntimeError("governance-plan production runtime envelope is missing")
    if config.get("selection_authority") != "decision-system-governance":
        raise RuntimeError("runtime config does not preserve governance selection authority")
    if config.get("model_selection_performed_locally") is not False:
        raise RuntimeError("expert runtime reports local model selection")
    if config.get("model_reranking_performed_locally") is not False:
        raise RuntimeError("expert runtime reports local model reranking")
    if config.get("governance_model_plan_sha256") != plan["plan_sha256"]:
        raise RuntimeError("runtime config model plan digest mismatch")
    if selection.get("status") != "PASS":
        raise RuntimeError("governance plan materialization did not pass")
    if selection.get("selection_authority") != "decision-system-governance":
        raise RuntimeError("selection evidence authority mismatch")
    if selection.get("model_selection_performed_locally") is not False:
        raise RuntimeError("selection evidence reports local model selection")
    if int(governance.get("actual_governance_calls") or 0) != 0:
        raise RuntimeError("expert center governance calls must equal zero")
    if request_audit.get("status") != "PASS":
        raise RuntimeError("complete request audit did not pass")
    if any(not canonical_provider_lock(row) for row in source["requests"]):
        raise RuntimeError("one or more expert requests lacks an exact provider lock")
    if not 3 <= len(source["graph_nodes"]) <= min(
        6, approved_total_calls - approved_recovery_calls
    ):
        raise RuntimeError("expert graph size violates approved bounds")
    graph_models = models_from_graph(source["graph"])
    planned_models = tuple(
        str(row.get("model") or "") for row in plan["selected_models"]
    )
    if tuple(graph_models) != planned_models:
        raise RuntimeError("executed graph models differ from governance model plan")

    budget = summary.get("execution_budget")
    budget = dict(budget) if isinstance(budget, Mapping) else {}
    call_count = int(budget.get("calls_reserved") or len(source["requests"]))
    if len(source["requests"]) != call_count:
        raise RuntimeError("request audit and expert call ledger disagree")
    if call_count > approved_total_calls:
        raise RuntimeError("approved total model-call ceiling exceeded")
    actual_cost = float(summary.get("actual_cost_usd") or 0.0)
    if not math.isfinite(actual_cost) or actual_cost < 0:
        raise RuntimeError("actual execution cost is invalid")
    answer = str(summary.get("final_answer") or "").strip()
    if require_report and (not source["report"].strip() or not answer):
        raise RuntimeError("governance-plan runtime did not produce a final report")
    return call_count, actual_cost, answer


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
    call_count, actual_cost, answer = _validate(
        source,
        approved_total_calls=total,
        approved_recovery_calls=recovery,
        require_report=require_report,
    )
    plan = source["plan"]
    providers = providers_from_requests(source["requests"])
    expert_models = models_from_graph(source["graph"])
    exceeded = bool(
        cost_anomaly_usd is not None
        and actual_cost > float(cost_anomaly_usd) + 1e-12
    )
    approved = {
        "maximum_total_calls": total,
        "governance_calls_reserved": 0,
        "maximum_expert_calls": total,
        "maximum_recovery_calls": recovery,
        "maximum_expert_initial_calls": total - recovery,
        "cost_anomaly_usd": cost_anomaly_usd,
    }
    evidence_input = {
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
    evidence_sha = canonical_json_sha(evidence_input)

    request_document = {
        **source["request_audit"],
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS",
        "request_count": len(source["requests"]),
        "approved_total_call_ceiling": total,
        "governance_request_count": 0,
        "expert_request_count": len(source["requests"]),
        "provider_locks_valid": True,
        "external_tools_allowed": False,
        "provider_fallback_allowed": False,
        "selection_authority": "decision-system-governance",
        "model_selection_performed_locally": False,
    }
    ledger_document = {
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
    selection_document = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "selection_authority": "decision-system-governance",
        "governance_model_plan_sha256": plan["plan_sha256"],
        "model_selection_performed_locally": False,
        "model_reranking_performed_locally": False,
        "model_substitution_allowed": False,
        "provider_resolution_only": True,
        "expert_models": list(expert_models),
        "node_count": len(source["graph_nodes"]),
        "catalog_snapshot_id": source["catalog"].get("catalog_snapshot_id"),
        "networkx_used": True,
        "cross_task_history_used": False,
    }
    routing_document = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS",
        "mode": "governance-selected-networkx-dag",
        "topology": "parallel-independent-analysis -> cross-review -> final-synthesis",
        "selection_authority": "decision-system-governance",
        "model_loop_allowed": False,
    }
    summary_document = {
        **source["summary"],
        "runtime_version": RUNTIME_VERSION,
        "approved_budget": approved,
        "catalog_snapshot_id": source["catalog"].get("catalog_snapshot_id"),
        "evidence_input_sha256": evidence_sha,
        "selection_authority": "decision-system-governance",
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
    result_document = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": str(source["summary"].get("status") or "failed"),
        "completion_mode": str(
            source["summary"].get("completion_mode") or "none"
        ),
        "quality_status": str(
            source["summary"].get("quality_status") or "failed"
        ),
        "quality_integrity": source["summary"].get("quality_integrity"),
        "final_answer": answer,
        "actual_cost_usd": round(actual_cost, 8),
        "executor": source["summary"].get("executor"),
        "work_coverage": source["summary"].get("work_coverage"),
        "degradation": source["summary"].get("degradation"),
        "execution_budget": source["summary"].get("execution_budget"),
        "approved_budget": approved,
        "catalog_snapshot_id": source["catalog"].get("catalog_snapshot_id"),
        "node_count": len(source["graph_nodes"]),
        "model_count": len(expert_models),
        "provider_count": len(providers),
        "selection_authority": "decision-system-governance",
        "governance_model_plan_sha256": plan["plan_sha256"],
        "model_selection_performed_locally": False,
        "production_entrypoint": True,
        "fallback_used": False,
        "legacy_runtime_present": False,
        "cross_task_history_used": False,
        "ticket_task_id": source["ticket_status"].get("task_id"),
        "evidence_input_sha256": evidence_sha,
    }
    documents = {
        "request-audit.json": request_document,
        "call-ledger.json": ledger_document,
        "model-selection.json": selection_document,
        "task-routing.json": routing_document,
        "execution-summary.json": summary_document,
        "expert-team-result.json": result_document,
    }
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
            "selection_authority": "decision-system-governance",
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
    return result_document


__all__ = ["RUNTIME_VERSION", "normalize_governance_plan_evidence"]
