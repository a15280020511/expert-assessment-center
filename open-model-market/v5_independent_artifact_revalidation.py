#!/usr/bin/env python3
"""Independently recompute a V5 artifact verdict from primitive evidence."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

from artifact_manifest import sha256_file
from v5_model_company import canonical_model_company
from v5_task_constraints import (
    compile_task_constraints,
    validate_answer_evidence,
)
from v5_task_delivery_contract import (
    apply_explicit_contract,
    explicit_contract_kind,
    validate_answer_contract,
)

FORBIDDEN_REQUEST_FIELDS = {
    "tools",
    "tool_choice",
    "plugins",
    "web_search",
    "web_search_options",
    "file_search",
    "browser",
    "code_interpreter",
    "models",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_optional(path: Path, default: Any) -> Any:
    try:
        return _load(path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _manifest_identity(
    manifest: Mapping[str, Any],
    expected_sha: str,
    expected_run_id: str,
) -> tuple[list[str], str, str]:
    failures: list[str] = []
    execution_sha = str(
        manifest.get("execution_sha")
        or manifest.get("github_sha")
        or ""
    )
    run_id = str(manifest.get("github_run_id") or "")
    if execution_sha != expected_sha:
        failures.append(
            f"manifest execution SHA mismatch: {execution_sha}/{expected_sha}"
        )
    if run_id != str(expected_run_id):
        failures.append(f"manifest Run ID mismatch: {run_id}/{expected_run_id}")
    if (
        manifest.get("execution_sha_policy")
        != "checked-out-git-head-is-authoritative"
    ):
        failures.append("manifest execution SHA policy is invalid")
    return failures, execution_sha, run_id


def _manifest_entry_checks(
    root: Path,
    entries: list[Any],
) -> tuple[list[str], set[str], int]:
    failures: list[str] = []
    listed_paths: set[str] = set()
    checked = 0
    for row in entries:
        if not isinstance(row, Mapping):
            failures.append("manifest contains a non-object entry")
            continue
        relative = str(row.get("path") or "")
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            failures.append(f"invalid manifest path: {relative}")
            continue
        if relative in listed_paths:
            failures.append(f"duplicate manifest path: {relative}")
            continue
        listed_paths.add(relative)
        artifact_path = root / relative
        if not artifact_path.is_file():
            failures.append(f"manifest file missing: {relative}")
            continue
        checked += 1
        if artifact_path.stat().st_size != int(row.get("size") or -1):
            failures.append(f"manifest size mismatch: {relative}")
        if sha256_file(artifact_path) != str(row.get("sha256") or ""):
            failures.append(f"manifest hash mismatch: {relative}")
    return failures, listed_paths, checked


def _manifest_archive_sets(
    root: Path,
    listed_paths: set[str],
) -> tuple[set[str], list[str], list[str]]:
    actual_paths = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.name != "artifact-manifest.json"
    }
    return (
        actual_paths,
        sorted(actual_paths - listed_paths),
        sorted(listed_paths - actual_paths),
    )


def _manifest_checks(
    root: Path,
    expected_sha: str,
    expected_run_id: str,
) -> dict[str, Any]:
    manifest = _load(root / "artifact-manifest.json")
    failures, execution_sha, run_id = _manifest_identity(
        manifest,
        expected_sha,
        expected_run_id,
    )
    entries = manifest.get("files", [])
    if not isinstance(entries, list) or not entries:
        failures.append("manifest file list is empty")
        entries = []
    entry_failures, listed_paths, checked = _manifest_entry_checks(root, entries)
    failures.extend(entry_failures)
    actual_paths, missing_from_manifest, absent_from_archive = (
        _manifest_archive_sets(root, listed_paths)
    )
    if missing_from_manifest:
        failures.append(
            "artifact files omitted from manifest: "
            + ",".join(missing_from_manifest[:32])
        )
    if absent_from_archive:
        failures.append(
            "manifest lists absent artifact files: "
            + ",".join(absent_from_archive[:32])
        )
    return {
        "failures": failures,
        "execution_sha": execution_sha,
        "run_id": run_id,
        "manifest_file_count": len(entries),
        "manifest_files_checked": checked,
        "actual_file_count": len(actual_paths),
        "missing_from_manifest": missing_from_manifest,
        "absent_from_archive": absent_from_archive,
    }


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
        values = [str(item).strip() for item in requirements if str(item).strip()]
        if values:
            sections.append(
                "执行要求：\n" + "\n".join(f"- {item}" for item in values)
            )
    language = str(task.get("language") or "").strip()
    if language:
        sections.append(f"输出语言：{language}")
    return "\n\n".join(sections)


def _attempts(nodes: list[Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("node_id") or "")
        attempts = node.get("attempts", [])
        if not isinstance(attempts, list):
            continue
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                continue
            values.append({"node_id": node_id, **dict(attempt)})
    return values


def _actual_cost_from_nodes(nodes: list[Any]) -> float:
    total = 0.0
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        value = float(node.get("actual_cost_usd") or 0.0)
        if not math.isfinite(value) or value < 0:
            raise ValueError("node actual cost is invalid")
        total += value
    return round(total, 8)


_EXPLICIT_CONTRACT_KEYS = (
    "explicit_user_contract",
    "exact_top_level_fields",
    "forbid_extra_top_level_fields",
    "all_required_fields_nonempty",
    "nested_exact_fields",
    "nested_values_must_be_objects",
    "explicit_markdown_contract",
    "exact_markdown_headings",
    "markdown_heading_level",
    "markdown_headings_must_be_nonempty",
    "markdown_heading_order_required",
    "explicit_table_contract",
    "exact_table_columns",
    "table_columns_must_be_nonempty",
    "table_column_order_required",
    "required_fields",
    "machine_readable_required",
)


def _explicit_contract_projection(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: contract[key]
        for key in _EXPLICIT_CONTRACT_KEYS
        if key in contract
    }


def _recompiled_task_contract(task: str) -> dict[str, Any]:
    return apply_explicit_contract(
        task,
        {"synthesis": 1.0},
        {
            "required_fields": [],
            "machine_readable_required": False,
            "must_separate_fact_assumption_inference": True,
        },
    )


def _final_contract_violations(
    graph: Mapping[str, Any],
    report: str,
    task: str = "",
) -> list[str]:
    final_ids = {
        str(value)
        for value in graph.get("final_nodes", [])
        if str(value)
    }
    nodes = graph.get("nodes", [])
    if not isinstance(nodes, list) or not final_ids:
        return ["final graph contract evidence is missing"]

    violations: list[str] = []
    task_contract = _recompiled_task_contract(task)
    task_kind = explicit_contract_kind(task_contract)
    if task_kind != "generic":
        task_violations = validate_answer_contract(report, task_contract)
        violations.extend(
            f"task-recompiled-final-report-contract:{value}"
            for value in task_violations
        )

    matched = 0
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        if str(node.get("node_id") or "") not in final_ids:
            continue
        matched += 1
        contract = node.get("output_contract", {})
        parameters = node.get("parameter_profile", {})
        if not isinstance(contract, Mapping):
            violations.append("final node output contract is missing")
            continue

        graph_kind = explicit_contract_kind(contract)
        if task_kind != "generic":
            if graph_kind != task_kind:
                violations.append(
                    f"final-graph-contract-kind-mismatch:{task_kind}:{graph_kind}"
                )
            if _explicit_contract_projection(contract) != _explicit_contract_projection(
                task_contract
            ):
                violations.append(
                    "final-graph-contract-differs-from-task-recompilation"
                )

        node_violations = validate_answer_contract(
            report,
            contract,
            parameters if isinstance(parameters, Mapping) else {},
        )
        violations.extend(
            f"final-report-contract:{value}" for value in node_violations
        )
    if matched != len(final_ids):
        violations.append(
            f"final node definitions are incomplete: {matched}/{len(final_ids)}"
        )
    return list(dict.fromkeys(violations))


def _load_revalidation_inputs(root: Path) -> dict[str, Any]:
    ticket = _load(root / "ticket.json")
    return {
        "result": _load(root / "expert-team-result.json"),
        "summary": _load(root / "v5-execution-summary.json"),
        "graph": _load(root / "v5-execution-graph.json"),
        "nodes": _load(root / "v5-node-results.json"),
        "request_audit": _load(root / "v5-request-audit.json"),
        "constraints": _load(root / "task-constraints.json"),
        "runtime_evidence": _load_optional(root / "evidence-integrity.json", {}),
        "ticket": ticket,
        "call_ledger": _load_optional(root / "call-ledger.json", {}),
        "report": (root / "v5-final-report.md").read_text(encoding="utf-8"),
        "task": _canonical_task(ticket if isinstance(ticket, Mapping) else {}),
    }


def _execution_result_failures(
    result: Mapping[str, Any],
    summary: Mapping[str, Any],
    report: str,
) -> list[str]:
    failures: list[str] = []
    if result.get("status") != "success" or summary.get("status") != "success":
        failures.append("execution status is not success")
    if (
        result.get("completion_mode") != "full"
        or summary.get("completion_mode") != "full"
    ):
        failures.append("completion mode is not full")
    if (
        result.get("quality_status") != "full_success"
        or summary.get("quality_status") != "full_success"
    ):
        failures.append("quality status is not full_success")
    if str(result.get("final_answer") or "").strip() != report.strip():
        failures.append("v5-result final answer differs from final report")
    if str(summary.get("final_answer") or "").strip() != report.strip():
        failures.append("execution summary final answer differs from final report")
    return failures


def _normalized_node_evidence(
    raw_nodes: Any,
    graph: Mapping[str, Any],
) -> tuple[list[Any], list[str]]:
    failures: list[str] = []
    if not isinstance(raw_nodes, list) or not raw_nodes:
        failures.append("node evidence is empty")
        nodes: list[Any] = []
    else:
        nodes = raw_nodes
    graph_nodes = graph.get("nodes", [])
    if len(nodes) != len(graph_nodes):
        failures.append(
            f"node result count mismatch: {len(nodes)}/{len(graph_nodes)}"
        )
    for node in nodes:
        if not isinstance(node, Mapping):
            failures.append("node result contains a non-object")
            continue
        if not str(node.get("status") or "").startswith("success"):
            failures.append(f"node is not successful: {node.get('node_id')}")
        contract = node.get("contract", {})
        if (
            not isinstance(contract, Mapping)
            or contract.get("required_fields_complete") is not True
        ):
            failures.append(
                f"node contract is incomplete: {node.get('node_id')}"
            )
    return nodes, failures


def _call_budget_audit(
    nodes: list[Any],
    summary: Mapping[str, Any],
    maximum_calls: int,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    failures: list[str] = []
    attempts = _attempts(nodes)
    calls = len(attempts)
    budget = summary.get("execution_budget", {})
    reserved = (
        int(budget.get("calls_reserved") or 0)
        if isinstance(budget, Mapping)
        else 0
    )
    if calls != reserved:
        failures.append(
            f"attempt count differs from reserved calls: {calls}/{reserved}"
        )
    if not 1 <= calls <= maximum_calls:
        failures.append(
            f"model call count outside bound: {calls}/{maximum_calls}"
        )
    return attempts, calls, failures


def _request_audit_failures(
    request_audit: Mapping[str, Any],
    attempts: list[dict[str, Any]],
    calls: int,
) -> list[str]:
    failures: list[str] = []
    if int(request_audit.get("request_count") or 0) != calls:
        failures.append("request audit count differs from primitive attempts")
    if request_audit.get("external_tools_allowed") is not False:
        failures.append("external tools are not explicitly prohibited")
    for index, row in enumerate(attempts, 1):
        request = row.get("request", {})
        if not isinstance(request, Mapping):
            failures.append(f"request {index} is not an object")
            continue
        forbidden = sorted(FORBIDDEN_REQUEST_FIELDS.intersection(request))
        if forbidden:
            failures.append(
                f"request {index} contains forbidden fields: {forbidden}"
            )
        provider = request.get("provider")
        if not isinstance(provider, Mapping):
            failures.append(f"request {index} has no provider lock")
            continue
        only = provider.get("only")
        if not isinstance(only, list) or len(only) != 1:
            failures.append(f"request {index} provider.only is not singular")
        if provider.get("allow_fallbacks") is not False:
            failures.append(f"request {index} allows provider fallback")
    return failures


def _called_model_audit(
    attempts: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], dict[str, list[str]], list[dict[str, str]], list[str]]:
    company_nodes: dict[str, set[str]] = {}
    called_models: list[dict[str, str]] = []
    for row in attempts:
        model = str(row.get("response_model") or row.get("model") or "")
        company = canonical_model_company(model)
        node_id = str(row.get("node_id") or "")
        called_models.append(
            {
                "node_id": node_id,
                "model": model,
                "company": company,
                "status": str(row.get("status") or ""),
            }
        )
        company_nodes.setdefault(company, set()).add(node_id)
    duplicates = {
        company: sorted(node_ids)
        for company, node_ids in company_nodes.items()
        if len(node_ids) > 1
    }
    unresolved = [
        row
        for row in called_models
        if not row["company"] or row["company"] == "unknown"
    ]
    failures = []
    if duplicates:
        failures.append("actual called companies repeat across nodes")
    if unresolved:
        failures.append("actual called company identity is unresolved")
    return called_models, duplicates, unresolved, failures


def _cost_audit(
    nodes: list[Any],
    summary: Mapping[str, Any],
    call_ledger: Any,
    maximum_cost_usd: float,
) -> tuple[float, float | None, list[str]]:
    failures: list[str] = []
    node_cost = _actual_cost_from_nodes(nodes)
    summary_cost = float(summary.get("actual_cost_usd") or 0.0)
    if abs(node_cost - summary_cost) > 1e-8:
        failures.append(f"actual cost mismatch: {node_cost}/{summary_cost}")
    ledger_summary = (
        call_ledger.get("summary", {})
        if isinstance(call_ledger, Mapping)
        else {}
    )
    ledger_cost_value = (
        ledger_summary.get("provider_actual_cost_usd")
        if isinstance(ledger_summary, Mapping)
        else None
    )
    if ledger_cost_value is None:
        ledger_cost = None
        failures.append("call ledger actual provider cost is missing")
    else:
        ledger_cost = float(ledger_cost_value)
        if abs(node_cost - ledger_cost) > 1e-8:
            failures.append(
                f"call ledger cost mismatch: {node_cost}/{ledger_cost}"
            )
    if not 0.0 <= node_cost <= maximum_cost_usd:
        failures.append(
            f"actual cost outside bound: {node_cost}/{maximum_cost_usd}"
        )
    return node_cost, ledger_cost, failures


def _independent_evidence_audit(
    task: str,
    report: str,
    constraints: Mapping[str, Any],
    runtime_evidence: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    if not task:
        failures.append("canonical task cannot be reconstructed from ticket")
        recomputed_constraints: Mapping[str, Any] = {}
        recomputed_evidence: list[str] = ["task-missing"]
    else:
        recomputed_constraints = compile_task_constraints(task).to_dict()
        recomputed_evidence = validate_answer_evidence(
            task,
            report,
            recomputed_constraints,
        )
    if constraints != recomputed_constraints:
        failures.append("artifact task constraints differ from independent compilation")
    if constraints.get("schema_version") != "v5-task-constraints-1":
        failures.append("structured task constraints are missing")
    if constraints.get("external_tools_allowed") is not False:
        failures.append("task constraints permit external tools")
    if constraints.get("fail_closed") is not True:
        failures.append("task constraints are not fail-closed")
    if recomputed_evidence:
        failures.append(
            "independently recomputed evidence violations: "
            + ";".join(recomputed_evidence)
        )
    if (
        runtime_evidence.get("status") != "PASS"
        or runtime_evidence.get("violations")
    ):
        failures.append("runtime evidence integrity is not PASS")
    if runtime_evidence.get("fact_truth_not_inferred_from_structure") is not True:
        failures.append("fact truth remains conflated with structure")
    if len(report.strip()) < 160:
        failures.append("final report is missing or too short")
    return recomputed_evidence, failures


def _archive_digest_audit(
    archive: Path | None,
    expected_artifact_digest: str | None,
) -> tuple[str | None, list[str]]:
    if archive is None:
        return None, []
    archive_sha256 = sha256_file(archive)
    expected = str(expected_artifact_digest or "").removeprefix("sha256:")
    failures = []
    if not expected or archive_sha256 != expected:
        failures.append("downloaded ZIP digest differs from platform artifact digest")
    return archive_sha256, failures


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
    manifest = _manifest_checks(root, expected_sha, expected_run_id)
    failures.extend(manifest["failures"])
    data = _load_revalidation_inputs(root)
    result = data["result"]
    summary = data["summary"]
    graph = data["graph"]
    report = data["report"]
    task = data["task"]
    failures.extend(_execution_result_failures(result, summary, report))
    nodes, node_failures = _normalized_node_evidence(data["nodes"], graph)
    failures.extend(node_failures)
    report_contract_violations = _final_contract_violations(graph, report, task)
    failures.extend(report_contract_violations)
    attempts, calls, call_failures = _call_budget_audit(
        nodes,
        summary,
        maximum_calls,
    )
    failures.extend(call_failures)
    failures.extend(_request_audit_failures(data["request_audit"], attempts, calls))
    called_models, duplicates, unresolved, company_failures = _called_model_audit(
        attempts
    )
    failures.extend(company_failures)
    node_cost, ledger_cost, cost_failures = _cost_audit(
        nodes,
        summary,
        data["call_ledger"],
        maximum_cost_usd,
    )
    failures.extend(cost_failures)
    recomputed_evidence, evidence_failures = _independent_evidence_audit(
        task,
        report,
        data["constraints"],
        data["runtime_evidence"],
    )
    failures.extend(evidence_failures)
    archive_sha256, archive_failures = _archive_digest_audit(
        archive,
        expected_artifact_digest,
    )
    failures.extend(archive_failures)
    return {
        "schema_version": "v5-independent-artifact-revalidation-3",
        "status": "PASS" if not failures else "FAIL",
        "expected_run_id": str(expected_run_id),
        "artifact_run_id": manifest["run_id"],
        "expected_execution_sha": expected_sha,
        "artifact_execution_sha": manifest["execution_sha"],
        "manifest_file_count": manifest["manifest_file_count"],
        "manifest_files_checked": manifest["manifest_files_checked"],
        "actual_artifact_file_count": manifest["actual_file_count"],
        "model_calls": calls,
        "actual_cost_usd": node_cost,
        "call_ledger_cost_usd": ledger_cost,
        "maximum_calls": maximum_calls,
        "maximum_cost_usd": maximum_cost_usd,
        "node_count": len(nodes),
        "actual_called_models": called_models,
        "duplicate_called_companies_across_nodes": duplicates,
        "unresolved_called_companies": unresolved,
        "completion_mode": summary.get("completion_mode"),
        "quality_status": summary.get("quality_status"),
        "runtime_evidence_integrity_status": data["runtime_evidence"].get("status"),
        "independently_recomputed_evidence_violations": recomputed_evidence,
        "final_report_contract_violations": report_contract_violations,
        "archive_sha256": archive_sha256,
        "expected_artifact_digest": expected_artifact_digest,
        "recomputed_from_primitive_evidence": True,
        "paid_acceptance_verdict_used_as_source": False,
        "failures": list(dict.fromkeys(failures)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--maximum-calls", required=True, type=int)
    parser.add_argument("--maximum-cost-usd", required=True, type=float)
    parser.add_argument("--archive")
    parser.add_argument("--expected-artifact-digest")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    archive = Path(args.archive) if args.archive else None
    try:
        result = recompute(
            Path(args.artifact_dir),
            expected_sha=args.expected_sha,
            expected_run_id=args.expected_run_id,
            maximum_calls=args.maximum_calls,
            maximum_cost_usd=args.maximum_cost_usd,
            archive=archive,
            expected_artifact_digest=args.expected_artifact_digest,
        )
    except Exception as exc:
        archive_sha256 = sha256_file(archive) if archive is not None and archive.is_file() else None
        result = {
            "schema_version": "v5-independent-artifact-revalidation-3",
            "status": "FAIL",
            "expected_run_id": str(args.expected_run_id),
            "artifact_run_id": None,
            "expected_execution_sha": args.expected_sha,
            "artifact_execution_sha": None,
            "manifest_file_count": None,
            "manifest_files_checked": 0,
            "actual_artifact_file_count": sum(
                1 for path in Path(args.artifact_dir).rglob("*") if path.is_file()
            ),
            "model_calls": None,
            "actual_cost_usd": None,
            "call_ledger_cost_usd": None,
            "maximum_calls": args.maximum_calls,
            "maximum_cost_usd": args.maximum_cost_usd,
            "node_count": None,
            "actual_called_models": [],
            "duplicate_called_companies_across_nodes": {},
            "unresolved_called_companies": [],
            "completion_mode": None,
            "quality_status": None,
            "runtime_evidence_integrity_status": None,
            "independently_recomputed_evidence_violations": [],
            "final_report_contract_violations": [],
            "archive_sha256": archive_sha256,
            "expected_artifact_digest": args.expected_artifact_digest,
            "recomputed_from_primitive_evidence": False,
            "paid_acceptance_verdict_used_as_source": False,
            "revalidation_exception": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
            "failures": [
                f"independent artifact revalidation could not be completed: "
                f"{type(exc).__name__}: {exc}"
            ],
        }
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
