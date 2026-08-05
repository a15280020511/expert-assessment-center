#!/usr/bin/env python3
"""Independently recompute a governed-roster V6 artifact verdict."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

from artifact_manifest import sha256_file
from v5_model_company import canonical_model_company
from v5_no_tools_policy import forbidden_request_fields
from v5_provider_lock import canonical_provider_lock
from v5_task_constraints import compile_task_constraints, validate_answer_evidence
from v5_task_delivery_contract import apply_explicit_contract, validate_answer_contract


def _load(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def _mapping(root: Path, name: str) -> dict[str, Any]:
    try:
        value = _load(root / name)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(root: Path, name: str) -> list[dict[str, Any]]:
    try:
        value = _load(root / name)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _canonical_task(ticket: Mapping[str, Any]) -> str:
    task = ticket.get("task")
    if not isinstance(task, Mapping):
        return ""
    question = str(task.get("question") or "").strip()
    if not question:
        return ""
    sections = [question]
    requirements = task.get("requirements")
    if isinstance(requirements, list):
        rows = [str(value).strip() for value in requirements if str(value).strip()]
        if rows:
            sections.append("执行要求：\n" + "\n".join(f"- {row}" for row in rows))
    language = str(task.get("language") or "").strip()
    if language:
        sections.append(f"输出语言：{language}")
    return "\n\n".join(sections)


def _manifest_audit(root: Path, expected_sha: str, expected_run_id: str) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    manifest = _mapping(root, "artifact-manifest.json")
    execution_sha = str(manifest.get("execution_sha") or manifest.get("github_sha") or "")
    run_id = str(manifest.get("github_run_id") or "")
    if execution_sha != expected_sha:
        failures.append(f"manifest execution SHA mismatch: {execution_sha}/{expected_sha}")
    if run_id != str(expected_run_id):
        failures.append(f"manifest Run ID mismatch: {run_id}/{expected_run_id}")
    if manifest.get("execution_sha_policy") != "checked-out-git-head-is-authoritative":
        failures.append("manifest execution SHA policy is invalid")
    entries = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    if not entries:
        failures.append("manifest file list is empty")
    listed: set[str] = set()
    checked = 0
    for row in entries:
        if not isinstance(row, Mapping):
            failures.append("manifest contains a non-object entry")
            continue
        relative = str(row.get("path") or "")
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            failures.append(f"invalid manifest path: {relative}")
            continue
        if relative in listed:
            failures.append(f"duplicate manifest path: {relative}")
            continue
        listed.add(relative)
        path = root / relative
        if not path.is_file():
            failures.append(f"manifest file missing: {relative}")
            continue
        checked += 1
        if path.stat().st_size != int(row.get("size") or -1):
            failures.append(f"manifest size mismatch: {relative}")
        if sha256_file(path) != str(row.get("sha256") or ""):
            failures.append(f"manifest hash mismatch: {relative}")
    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.name != "artifact-manifest.json"
    }
    omitted = sorted(actual - listed)
    absent = sorted(listed - actual)
    if omitted:
        failures.append("artifact files omitted from manifest: " + ",".join(omitted[:32]))
    if absent:
        failures.append("manifest lists absent files: " + ",".join(absent[:32]))
    return {
        "execution_sha": execution_sha,
        "run_id": run_id,
        "manifest_file_count": len(entries),
        "manifest_files_checked": checked,
        "actual_file_count": len(actual),
    }, failures


def _attempts(nodes: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in nodes:
        for attempt in node.get("attempts", []):
            if isinstance(attempt, Mapping):
                rows.append({"node_id": str(node.get("node_id") or ""), **dict(attempt)})
    return rows


def recompute(
    root: Path,
    *,
    expected_sha: str,
    expected_run_id: str,
    maximum_calls: int,
    maximum_cost_usd: float,
    archive: Path | None = None,
    expected_artifact_digest: str | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    manifest, manifest_failures = _manifest_audit(root, expected_sha, expected_run_id)
    failures.extend(manifest_failures)
    result = _mapping(root, "expert-team-result.json")
    summary = _mapping(root, "v5-execution-summary.json")
    graph = _mapping(root, "v5-execution-graph.json")
    request_audit = _mapping(root, "request-audit.json")
    call_ledger = _mapping(root, "call-ledger.json")
    validation = _mapping(root, "v6-roster-validation.json")
    materialization = _mapping(root, "v6-networkx-materialization.json")
    evidence = _mapping(root, "evidence-integrity.json")
    constraints = _mapping(root, "task-constraints.json")
    ticket = _mapping(root, "ticket.json")
    nodes = _rows(root, "v5-node-results.json")
    report_path = root / "v5-final-report.md"
    report = report_path.read_text("utf-8") if report_path.is_file() else ""

    if result.get("runtime_version") != "v6-governed-roster-networkx-1":
        failures.append("artifact is not a V6 governed-roster result")
    for document, label in ((result, "result"), (summary, "summary")):
        if document.get("status") != "success":
            failures.append(f"{label} status is not success")
        if document.get("completion_mode") != "full":
            failures.append(f"{label} completion mode is not full")
        if document.get("quality_status") != "full_success":
            failures.append(f"{label} quality status is not full_success")
    if not report.strip():
        failures.append("final report is missing")
    if report.strip() != str(result.get("final_answer") or "").strip():
        failures.append("result final answer differs from final report")
    if report.strip() != str(summary.get("final_answer") or "").strip():
        failures.append("summary final answer differs from final report")
    if result.get("cross_task_history_used") is not False:
        failures.append("cross-task history is not disabled")
    if result.get("claude_mechanism_enabled") is not False:
        failures.append("Claude mechanism is not disabled")
    for field in ("claude_red_team_calls", "gpt_planning_calls", "gpt_synthesis_calls"):
        if int(result.get(field) or 0) != 0:
            failures.append(f"forbidden governance call count is nonzero: {field}")

    graph_nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    if not nodes or len(nodes) != len(graph_nodes):
        failures.append("node result count differs from graph")
    strict = {"success", "success_retried", "success_recovered"}
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        if str(node.get("status") or "") not in strict:
            failures.append(f"node is not strict success: {node_id}")
        contract = node.get("contract") if isinstance(node.get("contract"), Mapping) else {}
        if contract.get("required_fields_complete") is not True:
            failures.append(f"node contract is incomplete: {node_id}")

    metadata = graph.get("metadata") if isinstance(graph.get("metadata"), Mapping) else {}
    roster_sha = validation.get("governance_roster_sha256")
    if validation.get("status") != "PASS" or not roster_sha:
        failures.append("governance roster validation is missing or failed")
    if materialization.get("status") != "PASS":
        failures.append("NetworkX materialization is missing or failed")
    if metadata.get("governance_roster_sha256") != roster_sha:
        failures.append("execution graph is not bound to governance roster")
    if metadata.get("orchestration_library") != "networkx":
        failures.append("execution graph does not prove NetworkX orchestration")
    if metadata.get("orchestration_algorithm") != "topological_generations":
        failures.append("execution graph does not prove topological generations")

    attempts = _attempts(nodes)
    expert_calls = len(attempts)
    budget = summary.get("execution_budget") if isinstance(summary.get("execution_budget"), Mapping) else {}
    if expert_calls != int(budget.get("calls_reserved") or -1):
        failures.append("expert attempt count differs from runtime ledger")
    if not 2 <= expert_calls <= maximum_calls:
        failures.append(f"expert call count outside bound: {expert_calls}/{maximum_calls}")
    requests = request_audit.get("requests") if isinstance(request_audit.get("requests"), list) else []
    if request_audit.get("status") != "PASS" or len(requests) != expert_calls:
        failures.append("request audit does not cover all expert calls")
    if int(request_audit.get("governance_request_count") or 0) != 0:
        failures.append("request audit contains governance calls")
    if int(request_audit.get("expert_request_count") or -1) != expert_calls:
        failures.append("request audit expert count mismatch")
    if request_audit.get("external_tools_allowed") is not False:
        failures.append("external tools are not explicitly prohibited")
    if request_audit.get("provider_fallback_allowed") is not False:
        failures.append("provider fallback is not explicitly prohibited")
    for index, request in enumerate(requests, 1):
        if not isinstance(request, Mapping):
            failures.append(f"request {index} is not an object")
            continue
        if forbidden_request_fields(request):
            failures.append(f"request {index} contains forbidden fields")
        if not canonical_provider_lock(request):
            failures.append(f"request {index} lacks exact provider lock")

    company_nodes: dict[str, set[str]] = {}
    called_models: list[dict[str, str]] = []
    for row in attempts:
        model = str(row.get("response_model") or row.get("model") or "")
        company = canonical_model_company(model)
        node_id = str(row.get("node_id") or "")
        called_models.append({"node_id": node_id, "model": model, "company": company})
        company_nodes.setdefault(company, set()).add(node_id)
    duplicates = {
        company: sorted(node_ids)
        for company, node_ids in company_nodes.items()
        if len(node_ids) > 1
    }
    unresolved = [row for row in called_models if row["company"] in {"", "unknown"}]
    if duplicates:
        failures.append("actual called companies repeat across nodes")
    if unresolved:
        failures.append("actual called company identity is unresolved")

    expert_cost = round(sum(float(node.get("actual_cost_usd") or 0.0) for node in nodes), 8)
    if not math.isfinite(expert_cost) or expert_cost < 0:
        failures.append("expert actual cost is invalid")
    summary_cost = float(summary.get("actual_cost_usd") or 0.0)
    if abs(expert_cost - summary_cost) > 1e-8:
        failures.append(f"summary actual cost mismatch: {expert_cost}/{summary_cost}")
    ledger_summary = call_ledger.get("summary") if isinstance(call_ledger.get("summary"), Mapping) else {}
    ledger_cost = ledger_summary.get("provider_actual_cost_usd")
    if ledger_cost is None or abs(expert_cost - float(ledger_cost)) > 1e-8:
        failures.append("call ledger actual cost mismatch")

    task = _canonical_task(ticket)
    recomputed_constraints = compile_task_constraints(task).to_dict() if task else {}
    if constraints != recomputed_constraints:
        failures.append("task constraints differ from independent compilation")
    if constraints.get("external_tools_allowed") is not False:
        failures.append("task constraints permit external tools")
    if constraints.get("fail_closed") is not True:
        failures.append("task constraints are not fail-closed")
    evidence_violations = validate_answer_evidence(task, report, recomputed_constraints) if task else ["task-missing"]
    if evidence_violations:
        failures.append("independent evidence violations: " + ";".join(evidence_violations))
    if evidence.get("status") != "PASS" or evidence.get("violations"):
        failures.append("runtime evidence integrity is not PASS")
    task_contract = apply_explicit_contract(
        task,
        {"synthesis": 1.0},
        {
            "required_fields": [],
            "machine_readable_required": False,
            "must_separate_fact_assumption_inference": True,
        },
    )
    report_contract_violations = validate_answer_contract(report, task_contract)
    if report_contract_violations:
        failures.append("final report contract violations: " + ";".join(report_contract_violations))

    archive_sha256 = None
    if archive is not None:
        archive_sha256 = sha256_file(archive)
        expected = str(expected_artifact_digest or "").removeprefix("sha256:")
        if not expected or archive_sha256 != expected:
            failures.append("downloaded ZIP digest differs from platform artifact digest")

    return {
        "schema_version": "v6-independent-artifact-revalidation-1",
        "status": "PASS" if not failures else "FAIL",
        "expected_run_id": str(expected_run_id),
        "artifact_run_id": manifest["run_id"],
        "expected_execution_sha": expected_sha,
        "artifact_execution_sha": manifest["execution_sha"],
        "manifest_file_count": manifest["manifest_file_count"],
        "manifest_files_checked": manifest["manifest_files_checked"],
        "actual_artifact_file_count": manifest["actual_file_count"],
        "total_model_calls": expert_calls,
        "governance_model_calls": 0,
        "expert_model_calls": expert_calls,
        "actual_cost_usd": expert_cost,
        "governance_actual_cost_usd": 0.0,
        "expert_actual_cost_usd": expert_cost,
        "call_ledger_cost_usd": ledger_cost,
        "maximum_calls": maximum_calls,
        "cost_advisory_usd": maximum_cost_usd,
        "cost_advisory_exceeded": bool(expert_cost > maximum_cost_usd + 1e-12),
        "cost_threshold_can_invalidate_revalidation": False,
        "node_count": len(nodes),
        "governance_roster_sha256": roster_sha,
        "actual_called_models": called_models,
        "duplicate_called_companies_across_nodes": duplicates,
        "unresolved_called_companies": unresolved,
        "completion_mode": summary.get("completion_mode"),
        "quality_status": summary.get("quality_status"),
        "runtime_evidence_integrity_status": evidence.get("status"),
        "independently_recomputed_evidence_violations": evidence_violations,
        "final_report_contract_violations": report_contract_violations,
        "archive_sha256": archive_sha256,
        "expected_artifact_digest": expected_artifact_digest,
        "networkx_used": True,
        "claude_calls": 0,
        "recomputed_from_primitive_evidence": True,
        "failures": list(dict.fromkeys(failures)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--maximum-calls", required=True, type=int)
    parser.add_argument("--cost-advisory-usd", type=float, required=True)
    parser.add_argument("--archive")
    parser.add_argument("--expected-artifact-digest")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = recompute(
            Path(args.artifact_dir),
            expected_sha=args.expected_sha,
            expected_run_id=args.expected_run_id,
            maximum_calls=args.maximum_calls,
            maximum_cost_usd=args.cost_advisory_usd,
            archive=Path(args.archive) if args.archive else None,
            expected_artifact_digest=args.expected_artifact_digest,
        )
    except Exception as exc:  # noqa: BLE001
        result = {
            "schema_version": "v6-independent-artifact-revalidation-1",
            "status": "FAIL",
            "failures": [str(exc)],
        }
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
