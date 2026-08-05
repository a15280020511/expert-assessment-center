"""Normalize zero-governance price-ranked execution evidence.

This module is intentionally independent from the retired GPT/Claude governance
ledger. It accepts only expert requests and proves that governance model calls
are exactly zero.
"""
from __future__ import annotations

import json
import math
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from v5_json_io import load_json_or_default, write_json
from v5_price_ranked_artifact_manifest import write_manifest
from v5_provider_lock import canonical_provider_lock

RUNTIME_VERSION = "v5-price-ranked-runtime-1"


def _mapping(root: Path, name: str) -> dict[str, Any]:
    value = load_json_or_default(root / name, {})
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _canonical_sha(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(rendered.encode("utf-8")).hexdigest()


def _providers(requests: list[Mapping[str, Any]]) -> list[str]:
    result: set[str] = set()
    for request in requests:
        provider = request.get("provider")
        if not isinstance(provider, Mapping):
            continue
        only = provider.get("only")
        if isinstance(only, list) and only:
            result.add(str(only[0]))
    return sorted(item for item in result if item)


def _expert_models(graph: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            str(row.get("model") or "")
            for row in _rows(graph.get("nodes"))
            if str(row.get("model") or "")
        }
    )


def normalize_price_ranked_evidence(
    root: Path,
    *,
    approved_total_calls: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
    require_report: bool,
) -> dict[str, Any]:
    runtime = _mapping(root, "production-runtime.json")
    runtime_config = _mapping(root, "v5-runtime-config.json")
    catalog = _mapping(root, "catalog-snapshot.json")
    graph = _mapping(root, "v5-execution-graph.json")
    summary = _mapping(root, "v5-execution-summary.json")
    selection = _mapping(root, "v5-price-ranked-selection.json")
    request_audit = _mapping(root, "v5-request-audit.json")
    governance = _mapping(root, "v5-governance-calls.json")
    ticket = _mapping(root, "ticket-status.json")
    nodes = _rows(load_json_or_default(root / "v5-node-results.json", []))
    requests = _rows(request_audit.get("requests"))
    graph_nodes = _rows(graph.get("nodes"))

    if runtime.get("runtime_version") != RUNTIME_VERSION:
        raise RuntimeError("price-ranked production runtime envelope is missing")
    if runtime_config.get("claude_mechanism_enabled") is not False:
        raise RuntimeError("Claude mechanism is not explicitly disabled")
    if int(governance.get("actual_governance_calls") or 0) != 0:
        raise RuntimeError("governance model calls must equal zero")
    if int(governance.get("claude_red_team_calls") or 0) != 0:
        raise RuntimeError("Claude calls must equal zero")
    if not 4 <= int(approved_total_calls) <= 16:
        raise ValueError("approved total calls must be between 4 and 16")
    if not 0 <= int(approved_recovery_calls) < int(approved_total_calls):
        raise ValueError("approved recovery reserve is invalid")
    initial_limit = int(approved_total_calls) - int(approved_recovery_calls)
    if not 3 <= len(graph_nodes) <= min(6, initial_limit):
        raise RuntimeError("expert graph size violates approved price-ranked bounds")
    if selection.get("status") != "PASS":
        raise RuntimeError("price-ranked selection audit did not pass")
    if selection.get("claude_calls") not in {0, None}:
        raise RuntimeError("selection evidence reports a Claude call")
    if request_audit.get("status") != "PASS":
        raise RuntimeError("complete request audit did not pass")
    if any(not canonical_provider_lock(row) for row in requests):
        raise RuntimeError("one or more expert requests lacks an exact provider lock")

    budget = summary.get("execution_budget")
    budget = dict(budget) if isinstance(budget, Mapping) else {}
    call_count = int(budget.get("calls_reserved") or len(requests))
    if len(requests) != call_count:
        raise RuntimeError("request audit and expert call ledger disagree")
    if call_count > int(approved_total_calls):
        raise RuntimeError("approved total model-call ceiling exceeded")
    actual_cost = float(summary.get("actual_cost_usd") or 0.0)
    if not math.isfinite(actual_cost) or actual_cost < 0:
        raise RuntimeError("actual execution cost is invalid")

    report_path = root / "v5-final-report.md"
    report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    answer = str(summary.get("final_answer") or "").strip()
    if require_report and (not report.strip() or not answer):
        raise RuntimeError("price-ranked runtime did not produce a final report")

    providers = _providers(requests)
    expert_models = _expert_models(graph)
    approved = {
        "maximum_total_calls": int(approved_total_calls),
        "governance_calls_reserved": 0,
        "maximum_expert_calls": int(approved_total_calls),
        "maximum_recovery_calls": int(approved_recovery_calls),
        "maximum_expert_initial_calls": initial_limit,
        "cost_anomaly_usd": cost_anomaly_usd,
    }
    input_payload = {
        "runtime": runtime,
        "runtime_config": runtime_config,
        "catalog": catalog,
        "graph": graph,
        "nodes": nodes,
        "summary": summary,
        "selection": selection,
        "request_audit": request_audit,
        "governance": governance,
        "ticket": ticket,
        "approved": approved,
        "report": report,
    }
    evidence_input_sha = _canonical_sha(input_payload)
    cost_exceeded = bool(
        cost_anomaly_usd is not None
        and actual_cost > float(cost_anomaly_usd) + 1e-12
    )

    normalized_request = {
        **request_audit,
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS",
        "request_count": len(requests),
        "approved_total_call_ceiling": int(approved_total_calls),
        "governance_request_count": 0,
        "expert_request_count": len(requests),
        "provider_locks_valid": True,
        "external_tools_allowed": False,
        "provider_fallback_allowed": False,
        "claude_mechanism_enabled": False,
    }
    ledger = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "summary": {
            "call_count": call_count,
            "governance_calls": 0,
            "expert_calls": call_count,
            "approved_total_call_ceiling": int(approved_total_calls),
            "approved_recovery_call_ceiling": int(approved_recovery_calls),
            "provider_actual_cost_usd": round(actual_cost, 8),
            "conservative_cost_usd": round(actual_cost, 8),
            "cost_anomaly_usd": cost_anomaly_usd,
            "cost_advisory_usd": cost_anomaly_usd,
            "cost_advisory_exceeded": cost_exceeded,
            "cost_threshold_can_invalidate_result": False,
            "substantive_providers": providers,
            "substantive_provider_count": len(providers),
            "replacement_calls": int(budget.get("replacements_reserved") or 0),
            "retry_calls": int(budget.get("retries_reserved") or 0),
            "recovery_calls": int(budget.get("recovery_calls_reserved") or 0),
        },
        "governance": governance,
        "node_results": nodes,
    }
    model_selection = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "selection_authority": "python-price-ranked-orchestrator",
        "selection_policy": "estimated-task-cost-ascending-distinct-companies",
        "claude_mechanism_enabled": False,
        "claude_calls": 0,
        "gpt_selection_calls": 0,
        "expert_models": expert_models,
        "node_count": len(graph_nodes),
        "catalog_snapshot_id": catalog.get("catalog_snapshot_id"),
        "networkx_used": True,
        "optimizer_used": False,
        "agent_framework_used": False,
        "cross_task_history_used": False,
    }
    routing = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS",
        "mode": "price-ranked-networkx-dag",
        "topology": "parallel-independent-analysis -> cross-review -> final-synthesis",
        "claude_mechanism_enabled": False,
        "model_loop_allowed": False,
    }
    normalized_summary = {
        **summary,
        "runtime_version": RUNTIME_VERSION,
        "approved_budget": approved,
        "catalog_snapshot_id": catalog.get("catalog_snapshot_id"),
        "evidence_input_sha256": evidence_input_sha,
        "governance": {
            "actual_calls": 0,
            "reserved_calls": 0,
            "claude_mechanism_enabled": False,
        },
        "resource_governance": {
            "mode": "prompt-led-soft-governance",
            "cost_advisory_usd": cost_anomaly_usd,
            "cost_advisory_exceeded": cost_exceeded,
            "cost_threshold_can_invalidate_result": False,
            "local_token_ceiling_enforced": False,
        },
    }
    result = {
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
        "execution_budget": budget,
        "approved_budget": approved,
        "governance": normalized_summary["governance"],
        "catalog_snapshot_id": catalog.get("catalog_snapshot_id"),
        "node_count": len(graph_nodes),
        "model_count": len(expert_models),
        "provider_count": len(providers),
        "selection_authority": "python-price-ranked-orchestrator",
        "production_entrypoint": True,
        "fallback_used": False,
        "legacy_runtime_present": False,
        "claude_mechanism_enabled": False,
        "cross_task_history_used": False,
        "ticket_task_id": ticket.get("task_id"),
        "evidence_input_sha256": evidence_input_sha,
    }
    documents = {
        "request-audit.json": normalized_request,
        "call-ledger.json": ledger,
        "model-selection.json": model_selection,
        "task-routing.json": routing,
        "execution-summary.json": normalized_summary,
        "expert-team-result.json": result,
    }
    for name, document in documents.items():
        write_json(root / name, document)
    if report.strip():
        (root / "expert-team-report.md").write_text(report, encoding="utf-8")

    bundle = {
        "schema_version": "v5-evidence-bundle-3",
        "runtime_version": RUNTIME_VERSION,
        "input_sha256": evidence_input_sha,
        "approved": approved,
        "catalog_snapshot_id": catalog.get("catalog_snapshot_id"),
        "generated_documents": {
            name: _canonical_sha(document)
            for name, document in sorted(documents.items())
        },
        "business_evidence_frozen": True,
        "governance_model_calls": 0,
        "claude_mechanism_enabled": False,
        "post_upload_fields_pending": [
            "primary_artifact_id",
            "primary_artifact_digest",
            "primary_artifact_url",
        ],
    }
    write_json(root / "evidence-bundle.json", bundle)
    write_manifest(root)
    return result


__all__ = ["RUNTIME_VERSION", "normalize_price_ranked_evidence"]
