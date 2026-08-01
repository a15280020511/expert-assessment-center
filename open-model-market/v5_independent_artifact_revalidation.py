#!/usr/bin/env python3
"""Independently recompute a V5 artifact verdict from primitive evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from v5_model_company import canonical_model_company

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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_checks(root: Path, expected_sha: str) -> dict[str, Any]:
    manifest = _load(root / "artifact-manifest.json")
    failures: list[str] = []
    execution_sha = str(
        manifest.get("execution_sha")
        or manifest.get("github_sha")
        or ""
    )
    if execution_sha != expected_sha:
        failures.append(
            f"manifest execution SHA mismatch: {execution_sha}/{expected_sha}"
        )
    if manifest.get("execution_sha_policy") not in {
        "checked-out-git-head-is-authoritative",
        None,
    }:
        failures.append("manifest execution SHA policy is invalid")
    entries = manifest.get("files", [])
    if not isinstance(entries, list) or not entries:
        failures.append("manifest file list is empty")
        entries = []
    checked = 0
    for row in entries:
        if not isinstance(row, Mapping):
            failures.append("manifest contains a non-object entry")
            continue
        relative = str(row.get("path") or "")
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            failures.append(f"invalid manifest path: {relative}")
            continue
        path = root / relative
        if not path.is_file():
            failures.append(f"manifest file missing: {relative}")
            continue
        checked += 1
        if path.stat().st_size != int(row.get("size") or -1):
            failures.append(f"manifest size mismatch: {relative}")
        if _sha256(path) != str(row.get("sha256") or ""):
            failures.append(f"manifest hash mismatch: {relative}")
    return {
        "failures": failures,
        "execution_sha": execution_sha,
        "manifest_file_count": len(entries),
        "manifest_files_checked": checked,
    }


def _attempts(nodes: list[Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("node_id") or "")
        for attempt in node.get("attempts", []):
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
    manifest = _manifest_checks(root, expected_sha)
    failures.extend(manifest["failures"])

    result = _load(root / "v5-result.json")
    summary = _load(root / "v5-execution-summary.json")
    graph = _load(root / "v5-execution-graph.json")
    nodes = _load(root / "v5-node-results.json")
    request_audit = _load(root / "v5-request-audit.json")
    constraints = _load(root / "task-constraints.json")
    evidence = _load(root / "evidence-integrity.json")
    report = (root / "v5-final-report.md").read_text(encoding="utf-8")

    if result.get("status") != "success" or summary.get("status") != "success":
        failures.append("execution status is not success")
    if result.get("completion_mode") != "full" or summary.get("completion_mode") != "full":
        failures.append("completion mode is not full")
    if result.get("quality_status") != "full_success" or summary.get("quality_status") != "full_success":
        failures.append("quality status is not full_success")
    if not isinstance(nodes, list) or not nodes:
        failures.append("node evidence is empty")
        nodes = []
    graph_nodes = graph.get("nodes", []) if isinstance(graph, Mapping) else []
    if len(nodes) != len(graph_nodes):
        failures.append(f"node result count mismatch: {len(nodes)}/{len(graph_nodes)}")
    for node in nodes:
        if not isinstance(node, Mapping):
            failures.append("node result contains a non-object")
            continue
        if not str(node.get("status") or "").startswith("success"):
            failures.append(f"node is not successful: {node.get('node_id')}")
        contract = node.get("contract", {})
        if not isinstance(contract, Mapping) or contract.get("required_fields_complete") is not True:
            failures.append(f"node contract is incomplete: {node.get('node_id')}")

    attempts = _attempts(nodes)
    calls = len(attempts)
    budget = summary.get("execution_budget", {})
    reserved = int(budget.get("calls_reserved") or 0) if isinstance(budget, Mapping) else 0
    if calls != reserved:
        failures.append(f"attempt count differs from reserved calls: {calls}/{reserved}")
    if not 1 <= calls <= maximum_calls:
        failures.append(f"model call count outside bound: {calls}/{maximum_calls}")

    requests = [row.get("request", {}) for row in attempts]
    if int(request_audit.get("request_count") or 0) != calls:
        failures.append("request audit count differs from primitive attempts")
    if request_audit.get("external_tools_allowed") is not False:
        failures.append("external tools are not explicitly prohibited")
    for index, request in enumerate(requests, 1):
        if not isinstance(request, Mapping):
            failures.append(f"request {index} is not an object")
            continue
        forbidden = sorted(FORBIDDEN_REQUEST_FIELDS.intersection(request))
        if forbidden:
            failures.append(f"request {index} contains forbidden fields: {forbidden}")
        provider = request.get("provider")
        if not isinstance(provider, Mapping):
            failures.append(f"request {index} has no provider lock")
            continue
        only = provider.get("only")
        if not isinstance(only, list) or len(only) != 1:
            failures.append(f"request {index} provider.only is not singular")
        if provider.get("allow_fallbacks") is not False:
            failures.append(f"request {index} allows provider fallback")

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
    unresolved = [row for row in called_models if not row["company"] or row["company"] == "unknown"]
    if duplicates:
        failures.append("actual called companies repeat across nodes")
    if unresolved:
        failures.append("actual called company identity is unresolved")

    node_cost = _actual_cost_from_nodes(nodes)
    summary_cost = float(summary.get("actual_cost_usd") or 0.0)
    if abs(node_cost - summary_cost) > 1e-8:
        failures.append(f"actual cost mismatch: {node_cost}/{summary_cost}")
    if not 0.0 <= node_cost <= maximum_cost_usd:
        failures.append(f"actual cost outside bound: {node_cost}/{maximum_cost_usd}")

    if constraints.get("schema_version") != "v5-task-constraints-1":
        failures.append("structured task constraints are missing")
    if constraints.get("external_tools_allowed") is not False:
        failures.append("task constraints permit external tools")
    if constraints.get("fail_closed") is not True:
        failures.append("task constraints are not fail-closed")
    if evidence.get("status") != "PASS" or evidence.get("violations"):
        failures.append("evidence integrity is not PASS")
    if evidence.get("fact_truth_not_inferred_from_structure") is not True:
        failures.append("fact truth remains conflated with structure")
    if len(report.strip()) < 160:
        failures.append("final report is missing or too short")

    archive_sha256 = None
    if archive is not None:
        archive_sha256 = _sha256(archive)
        expected = str(expected_artifact_digest or "").removeprefix("sha256:")
        if not expected or archive_sha256 != expected:
            failures.append("downloaded ZIP digest differs from platform artifact digest")

    return {
        "schema_version": "v5-independent-artifact-revalidation-1",
        "status": "PASS" if not failures else "FAIL",
        "expected_run_id": str(expected_run_id),
        "expected_execution_sha": expected_sha,
        "artifact_execution_sha": manifest["execution_sha"],
        "manifest_file_count": manifest["manifest_file_count"],
        "manifest_files_checked": manifest["manifest_files_checked"],
        "model_calls": calls,
        "actual_cost_usd": node_cost,
        "maximum_calls": maximum_calls,
        "maximum_cost_usd": maximum_cost_usd,
        "node_count": len(nodes),
        "actual_called_models": called_models,
        "duplicate_called_companies_across_nodes": duplicates,
        "unresolved_called_companies": unresolved,
        "completion_mode": summary.get("completion_mode"),
        "quality_status": summary.get("quality_status"),
        "evidence_integrity_status": evidence.get("status"),
        "archive_sha256": archive_sha256,
        "expected_artifact_digest": expected_artifact_digest,
        "recomputed_from_primitive_evidence": True,
        "paid_acceptance_verdict_used_as_source": False,
        "failures": failures,
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
    result = recompute(
        Path(args.artifact_dir),
        expected_sha=args.expected_sha,
        expected_run_id=args.expected_run_id,
        maximum_calls=args.maximum_calls,
        maximum_cost_usd=args.maximum_cost_usd,
        archive=Path(args.archive) if args.archive else None,
        expected_artifact_digest=args.expected_artifact_digest,
    )
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
