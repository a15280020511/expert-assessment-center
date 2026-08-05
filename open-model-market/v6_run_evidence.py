"""Build V6 production evidence from the governed roster and expert runtime."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from artifact_manifest import write_manifest
from v5_json_io import load_json_or_default, write_json
from v5_model_company import canonical_model_company
from v5_no_tools_policy import forbidden_request_fields
from v5_provider_lock import canonical_provider_lock

RUNTIME_VERSION = "v6-governed-roster-networkx-1"


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _mapping(root: Path, name: str) -> dict[str, Any]:
    value = load_json_or_default(root / name, {})
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(root: Path, name: str) -> list[dict[str, Any]]:
    value = load_json_or_default(root / name, [])
    return [dict(row) for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _attempts(nodes: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        for attempt in node.get("attempts", []):
            if isinstance(attempt, Mapping):
                rows.append({"node_id": node_id, **dict(attempt)})
    return rows


def _actual_called_models(attempts: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "node_id": str(row.get("node_id") or ""),
            "model": str(row.get("response_model") or row.get("model") or ""),
            "company": canonical_model_company(
                str(row.get("response_model") or row.get("model") or "")
            ),
            "status": str(row.get("status") or ""),
            "attempt_kind": str(row.get("attempt_kind") or ""),
        }
        for row in attempts
    ]


def build_v6_evidence(
    root: Path,
    *,
    maximum_total_calls: int,
    maximum_recovery_calls: int,
    cost_anomaly_usd: float | None,
    require_report: bool,
) -> dict[str, Any]:
    graph = _mapping(root, "v5-execution-graph.json")
    summary = _mapping(root, "v5-execution-summary.json")
    runtime = _mapping(root, "v5-runtime-config.json")
    catalog = _mapping(root, "catalog-snapshot.json")
    endpoint_catalog = _mapping(root, "v6-exact-endpoint-catalog.json")
    roster_validation = _mapping(root, "v6-roster-validation.json")
    materialization = _mapping(root, "v6-networkx-materialization.json")
    ticket = _mapping(root, "ticket-status.json") or _mapping(root, "ticket.json")
    native_request_audit = _mapping(root, "v5-request-audit.json")
    nodes = _rows(root, "v5-node-results.json")
    report_path = root / "v5-final-report.md"
    report = report_path.read_text("utf-8") if report_path.is_file() else ""
    attempts = _attempts(nodes)
    expert_calls = len(attempts)
    governance_calls = 0
    total_calls = expert_calls
    if total_calls > maximum_total_calls:
        raise RuntimeError("V6 actual calls exceed approved total calls")
    if total_calls < 1 and require_report:
        raise RuntimeError("V6 produced no expert calls")
    budget = summary.get("execution_budget")
    budget = dict(budget) if isinstance(budget, Mapping) else {}
    if int(budget.get("calls_reserved") or 0) != expert_calls:
        raise RuntimeError("V6 expert attempt count and runtime ledger disagree")
    actual_cost = float(summary.get("actual_cost_usd") or 0.0)
    if not math.isfinite(actual_cost) or actual_cost < 0:
        raise RuntimeError("V6 actual cost is invalid")
    if require_report and (
        summary.get("status") != "success"
        or summary.get("completion_mode") != "full"
        or summary.get("quality_status") != "full_success"
        or not report.strip()
    ):
        raise RuntimeError("V6 did not produce an audited full report")

    requests = [
        dict(row)
        for row in native_request_audit.get("requests", [])
        if isinstance(row, Mapping)
    ]
    if len(requests) != expert_calls:
        raise RuntimeError("V6 complete request audit does not cover every expert call")
    for index, request in enumerate(requests, 1):
        if forbidden_request_fields(request):
            raise RuntimeError(f"V6 request {index} contains forbidden tool fields")
        if not canonical_provider_lock(request):
            raise RuntimeError(f"V6 request {index} lacks an exact provider lock")

    called_models = _actual_called_models(attempts)
    by_company: dict[str, set[str]] = {}
    for row in called_models:
        by_company.setdefault(row["company"], set()).add(row["node_id"])
    duplicate_companies = {
        company: sorted(node_ids)
        for company, node_ids in by_company.items()
        if len(node_ids) > 1
    }
    unresolved = [
        row for row in called_models if row["company"] in {"", "unknown"}
    ]
    if duplicate_companies or unresolved:
        raise RuntimeError("V6 actual called model companies are not globally unique")

    request_audit = {
        **native_request_audit,
        "schema_version": "v6-complete-request-audit-1",
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS",
        "request_count": total_calls,
        "governance_request_count": 0,
        "expert_request_count": expert_calls,
        "requests": requests,
        "external_tools_allowed": False,
        "provider_fallback_allowed": False,
        "provider_locks_valid": True,
    }
    governance_ledger = {
        "schema_version": "v6-governance-selection-ledger-1",
        "runtime_version": RUNTIME_VERSION,
        "actual_governance_calls": 0,
        "calls": [],
        "actual_cost_usd": 0.0,
        "selection_authority": "decision-system-governance-signed-roster",
        "model_selection_calls": 0,
        "claude_red_team_calls": 0,
        "gpt_planning_calls": 0,
        "gpt_synthesis_calls": 0,
        "model_loop_allowed": False,
        "secret_values_exposed": False,
    }
    call_ledger = {
        "version": 6,
        "runtime_version": RUNTIME_VERSION,
        "summary": {
            "call_count": total_calls,
            "governance_calls": 0,
            "expert_calls": expert_calls,
            "approved_total_call_ceiling": maximum_total_calls,
            "approved_recovery_call_ceiling": maximum_recovery_calls,
            "provider_actual_cost_usd": round(actual_cost, 8),
            "conservative_cost_usd": round(actual_cost, 8),
            "cost_anomaly_usd": cost_anomaly_usd,
            "cost_advisory_usd": cost_anomaly_usd,
            "cost_advisory_exceeded": bool(
                cost_anomaly_usd is not None
                and actual_cost > float(cost_anomaly_usd) + 1e-12
            ),
            "cost_threshold_can_invalidate_result": False,
            "replacement_calls": int(budget.get("replacements_reserved") or 0),
            "retry_calls": int(budget.get("retries_reserved") or 0),
            "recovery_calls": int(budget.get("recovery_calls_reserved") or 0),
        },
        "governance": governance_ledger,
        "node_results": nodes,
    }
    model_selection = {
        "version": 6,
        "runtime_version": RUNTIME_VERSION,
        "selection_authority": "governance-signed-lowest-task-cost-roster",
        "orchestration_authority": "networkx-deterministic-dag",
        "governance_roster_sha256": roster_validation.get("governance_roster_sha256"),
        "governance_commit_sha": roster_validation.get("governance_commit_sha"),
        "catalog_snapshot_id": catalog.get("catalog_snapshot_id"),
        "endpoint_catalog_sha256": _canonical_sha(endpoint_catalog),
        "primary_models": [
            row.get("model_id")
            for row in roster_validation.get("primary_members", [])
            if isinstance(row, Mapping)
        ],
        "recovery_models": [
            row.get("model_id")
            for row in roster_validation.get("recovery_members", [])
            if isinstance(row, Mapping)
        ],
        "actual_called_models": called_models,
        "duplicate_called_companies_across_nodes": duplicate_companies,
        "all_model_companies_unique": not duplicate_companies and not unresolved,
        "model_identity_substitution_allowed": False,
        "local_scoring_used": False,
        "optimizer_used": False,
        "cp_sat_used": False,
        "pareto_pruning_used": False,
        "cross_task_history_used": False,
        "claude_red_team_calls": 0,
        "gpt_planning_calls": 0,
        "gpt_synthesis_calls": 0,
    }
    routing = {
        "version": 6,
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS" if graph.get("nodes") else "FAIL",
        "mode": "governance-roster-networkx-topological-generations",
        "networkx_used": True,
        "model_loop_allowed": False,
        "business_center_direct_communication": False,
    }
    approved = {
        "maximum_total_calls": maximum_total_calls,
        "governance_calls_reserved": 0,
        "maximum_expert_calls": maximum_total_calls,
        "maximum_recovery_calls": maximum_recovery_calls,
        "maximum_expert_initial_calls": maximum_total_calls - maximum_recovery_calls,
        "cost_anomaly_usd": cost_anomaly_usd,
    }
    execution_summary = {
        **summary,
        "runtime_version": RUNTIME_VERSION,
        "approved_budget": approved,
        "catalog_snapshot_id": catalog.get("catalog_snapshot_id"),
        "expert_actual_cost_usd": round(actual_cost, 8),
        "governance_actual_cost_usd": 0.0,
        "actual_cost_usd": round(actual_cost, 8),
        "governance_roster_sha256": roster_validation.get("governance_roster_sha256"),
    }
    result = {
        "version": 6,
        "runtime_version": RUNTIME_VERSION,
        "status": summary.get("status"),
        "completion_mode": summary.get("completion_mode"),
        "quality_status": summary.get("quality_status"),
        "final_answer": summary.get("final_answer"),
        "actual_cost_usd": round(actual_cost, 8),
        "expert_actual_cost_usd": round(actual_cost, 8),
        "governance_actual_cost_usd": 0.0,
        "node_count": len(nodes),
        "call_count": total_calls,
        "governance_call_count": 0,
        "expert_call_count": expert_calls,
        "recovery_used": bool(summary.get("recovery_used")),
        "selection_authority": "decision-system-governance",
        "orchestration_library": "networkx==3.6.1",
        "claude_mechanism_enabled": False,
        "claude_red_team_calls": 0,
        "gpt_planning_calls": 0,
        "gpt_synthesis_calls": 0,
        "external_tools_allowed": False,
        "provider_fallback_allowed": False,
        "cross_task_history_used": False,
        "governance_roster_sha256": roster_validation.get("governance_roster_sha256"),
        "evidence_input_sha256": _canonical_sha(
            {
                "runtime": runtime,
                "catalog": catalog,
                "graph": graph,
                "nodes": nodes,
                "summary": summary,
                "ticket": ticket,
                "roster_validation": roster_validation,
                "materialization": materialization,
            }
        ),
    }
    production_runtime = {
        "runtime_version": RUNTIME_VERSION,
        "entrypoint": "v6_production_ticket.py",
        "architecture": (
            "governance-signed-lowest-task-cost-roster -> "
            "exact-zdr-endpoint-resolution -> networkx-dag -> expert-execution"
        ),
        "approved_budget": approved,
        "claude_mechanism_enabled": False,
        "governance_model_calls": 0,
        "local_planner_present": False,
        "optimizer_present": False,
        "model_loop_allowed": False,
        "fallback_policy": "preapproved-distinct-company-recovery-only",
        "cross_task_history_used": False,
    }
    documents = {
        "request-audit.json": request_audit,
        "call-ledger.json": call_ledger,
        "model-selection.json": model_selection,
        "task-routing.json": routing,
        "execution-summary.json": execution_summary,
        "expert-team-result.json": result,
        "production-runtime.json": production_runtime,
        "v5-governance-calls.json": governance_ledger,
        "v5-governance-result.json": {
            "schema_version": "v6-governance-roster-receipt-1",
            "status": "PASS",
            "governance_roster_sha256": roster_validation.get("governance_roster_sha256"),
            "governance_calls": 0,
            "claude_calls": 0,
        },
    }
    for name, document in documents.items():
        write_json(root / name, document)
    if report.strip():
        (root / "expert-team-report.md").write_text(report, "utf-8")
    bundle = {
        "schema_version": "v6-evidence-bundle-1",
        "runtime_version": RUNTIME_VERSION,
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
    write_json(root / "evidence-bundle.json", bundle)
    write_manifest(root)
    return result


__all__ = ["RUNTIME_VERSION", "build_v6_evidence"]
