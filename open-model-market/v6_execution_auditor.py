#!/usr/bin/env python3
"""Audit one governed-roster V6 execution before report publication."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from v5_json_io import load_json_or_default, write_json
from v5_model_company import canonical_model_company
from v5_no_tools_policy import forbidden_request_fields
from v5_provider_lock import canonical_provider_lock


def _mapping(root: Path, name: str) -> dict[str, Any]:
    value = load_json_or_default(root / name, {})
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(root: Path, name: str) -> list[dict[str, Any]]:
    value = load_json_or_default(root / name, [])
    return [dict(row) for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _attempts(nodes: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in nodes:
        for attempt in node.get("attempts", []):
            if isinstance(attempt, Mapping):
                rows.append({"node_id": str(node.get("node_id") or ""), **dict(attempt)})
    return rows


def audit(root: Path, *, execute_outcome: str, publish_outcome: str) -> dict[str, Any]:
    result = _mapping(root, "expert-team-result.json")
    summary = _mapping(root, "v5-execution-summary.json")
    graph = _mapping(root, "v5-execution-graph.json")
    request_audit = _mapping(root, "request-audit.json")
    selection = _mapping(root, "model-selection.json")
    validation = _mapping(root, "v6-roster-validation.json")
    materialization = _mapping(root, "v6-networkx-materialization.json")
    evidence = _mapping(root, "evidence-integrity.json")
    company = _mapping(root, "actual-model-company-audit.json")
    report_manifest = _mapping(root / "report-comments", "report-comments-manifest.json")
    nodes = _rows(root, "v5-node-results.json")
    report_path = root / "v5-final-report.md"
    report = report_path.read_text("utf-8") if report_path.is_file() else ""
    failures: list[str] = []

    if execute_outcome != "success":
        failures.append(f"execution outcome is {execute_outcome}")
    if publish_outcome != "success":
        failures.append(f"report preparation outcome is {publish_outcome}")
    if result.get("runtime_version") != "v6-governed-roster-networkx-1":
        failures.append("expert result is not V6 governed-roster runtime")
    for document, label in ((result, "result"), (summary, "summary")):
        if document.get("status") != "success":
            failures.append(f"{label} status is not success")
        if document.get("completion_mode") != "full":
            failures.append(f"{label} completion mode is not full")
        if document.get("quality_status") != "full_success":
            failures.append(f"{label} quality status is not full_success")
    if not report.strip() or report.strip() != str(result.get("final_answer") or "").strip():
        failures.append("final report is missing or differs from expert result")
    if report.strip() != str(summary.get("final_answer") or "").strip():
        failures.append("final report differs from runtime summary")

    if validation.get("status") != "PASS":
        failures.append("governance roster validation is missing or failed")
    if materialization.get("status") != "PASS":
        failures.append("NetworkX materialization is missing or failed")
    roster_sha = validation.get("governance_roster_sha256")
    if not roster_sha or selection.get("governance_roster_sha256") != roster_sha:
        failures.append("selection evidence is not bound to the governance roster")
    metadata = graph.get("metadata") if isinstance(graph.get("metadata"), Mapping) else {}
    if metadata.get("governance_roster_sha256") != roster_sha:
        failures.append("execution graph is not bound to the governance roster")
    if metadata.get("selection_authority") != "governance-signed-roster":
        failures.append("execution graph selection authority is invalid")
    if metadata.get("orchestration_library") != "networkx":
        failures.append("NetworkX orchestration evidence is missing")
    if metadata.get("orchestration_algorithm") != "topological_generations":
        failures.append("NetworkX topological generation evidence is missing")
    for field in ("claude_red_team_calls", "gpt_planning_calls", "gpt_synthesis_calls"):
        if int(metadata.get(field) or 0) != 0 or int(result.get(field) or 0) != 0:
            failures.append(f"forbidden governance model call detected: {field}")
    if result.get("claude_mechanism_enabled") is not False:
        failures.append("Claude mechanism is not explicitly disabled")
    if result.get("cross_task_history_used") is not False:
        failures.append("cross-task history is not explicitly disabled")

    graph_nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    if not nodes or len(nodes) != len(graph_nodes):
        failures.append("node result count does not match execution graph")
    strict = {"success", "success_retried", "success_recovered"}
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        if str(node.get("status") or "") not in strict:
            failures.append(f"node is not strict success: {node_id}")
        contract = node.get("contract") if isinstance(node.get("contract"), Mapping) else {}
        if contract.get("required_fields_complete") is not True:
            failures.append(f"node contract is incomplete: {node_id}")

    attempts = _attempts(nodes)
    budget = summary.get("execution_budget") if isinstance(summary.get("execution_budget"), Mapping) else {}
    if len(attempts) != int(budget.get("calls_reserved") or -1):
        failures.append("attempt count and runtime call ledger disagree")
    requests = request_audit.get("requests") if isinstance(request_audit.get("requests"), list) else []
    if request_audit.get("status") != "PASS" or len(requests) != len(attempts):
        failures.append("complete request audit is missing or incomplete")
    if int(request_audit.get("governance_request_count") or 0) != 0:
        failures.append("request audit contains governance model calls")
    if int(request_audit.get("expert_request_count") or -1) != len(attempts):
        failures.append("request audit expert call count mismatch")
    if request_audit.get("external_tools_allowed") is not False:
        failures.append("external tools are not explicitly prohibited")
    if request_audit.get("provider_fallback_allowed") is not False:
        failures.append("provider fallback is not explicitly prohibited")
    for index, request in enumerate(requests, 1):
        if not isinstance(request, Mapping):
            failures.append(f"request {index} is not an object")
            continue
        if forbidden_request_fields(request):
            failures.append(f"request {index} contains forbidden tool fields")
        if not canonical_provider_lock(request):
            failures.append(f"request {index} lacks exact provider lock")

    company_nodes: dict[str, set[str]] = {}
    unresolved: list[dict[str, str]] = []
    for row in attempts:
        model = str(row.get("response_model") or row.get("model") or "")
        company_name = canonical_model_company(model)
        node_id = str(row.get("node_id") or "")
        if company_name in {"", "unknown"}:
            unresolved.append({"node_id": node_id, "model": model})
        company_nodes.setdefault(company_name, set()).add(node_id)
    duplicates = {
        name: sorted(node_ids)
        for name, node_ids in company_nodes.items()
        if len(node_ids) > 1
    }
    if duplicates:
        failures.append("actual model companies repeat across different nodes")
    if unresolved:
        failures.append("actual called company identity is unresolved")
    if company.get("status") != "PASS":
        failures.append("runtime company audit is missing or failed")
    if evidence.get("status") != "PASS":
        failures.append("semantic evidence integrity is missing or failed")
    if evidence.get("fact_truth_not_inferred_from_structure") is not True:
        failures.append("evidence audit does not separate structure from truth")
    if report_manifest.get("report_comment_preparation_status") != "PASS":
        failures.append("report comment preparation is not PASS")
    if report_manifest.get("issue_context_required") is not False:
        failures.append("report preparation unexpectedly depends on live Issue context")

    result_document = {
        "schema_version": "v6-execution-audit-1",
        "status": "PASS" if not failures else "FAIL",
        "runtime_version": "v6-governed-roster-networkx-1",
        "governance_roster_sha256": roster_sha,
        "node_count": len(nodes),
        "model_call_count": len(attempts),
        "governance_model_calls": 0,
        "claude_calls": 0,
        "actual_cost_usd": result.get("actual_cost_usd"),
        "actual_model_companies": {
            name: sorted(node_ids) for name, node_ids in company_nodes.items()
        },
        "duplicate_model_companies": duplicates,
        "unresolved_model_companies": unresolved,
        "networkx_used": True,
        "external_tools_allowed": False,
        "provider_fallback_allowed": False,
        "failures": list(dict.fromkeys(failures)),
    }
    write_json(root / "v6-execution-audit.json", result_document)
    write_json(root / "audit-result.json", result_document)
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"status={result_document['status']}\n")
    return result_document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--execute-outcome", required=True)
    parser.add_argument("--publish-outcome", required=True)
    args = parser.parse_args()
    result = audit(
        Path(args.output_dir),
        execute_outcome=args.execute_outcome,
        publish_outcome=args.publish_outcome,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
