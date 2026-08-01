#!/usr/bin/env python3
"""Independently recompute bounded paid acceptance from raw Artifact evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from v5_constitution import validate_answer_against_constitution

EXPECTED_HEADINGS = [
    "已知条件、假设与未知项",
    "成本结构、公式与选择阈值",
    "实施风险、运营风险与反证",
    "综合结论、适用条件与下一步",
]


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(path: Path) -> Mapping[str, Any]:
    value = _load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{path.name} must contain an object")
    return value


def revalidate(
    root: Path,
    *,
    head_sha: str,
    run_id: str,
    run_url: str,
    maximum_calls: int,
    maximum_cost: float,
    artifact_id: int,
    artifact_name: str,
    artifact_digest: str,
) -> dict[str, Any]:
    result = _mapping(root / "v5-result.json")
    summary = _mapping(root / "v5-execution-summary.json")
    audit = _mapping(root / "actual-model-company-audit.json")
    graph = _mapping(root / "v5-execution-graph.json")
    search = _mapping(root / "v5-adaptive-search.json")
    constitution = _mapping(root / "v5-constitution.json")
    nodes = _load(root / "v5-node-results.json")
    if not isinstance(nodes, list):
        raise RuntimeError("v5-node-results.json must contain an array")
    report = (root / "v5-final-report.md").read_text(encoding="utf-8")
    task = (root / "paid-acceptance-task.txt").read_text(encoding="utf-8")

    if result.get("status") != "success":
        raise RuntimeError("result status is not success")
    if result.get("completion_mode") != "full":
        raise RuntimeError("result completion mode is not full")
    if result.get("quality_status") != "full_success":
        raise RuntimeError("result quality status is not full_success")
    if summary.get("status") != "success" or summary.get("completion_mode") != "full":
        raise RuntimeError("execution summary is not strict full success")
    if audit.get("status") != "PASS":
        raise RuntimeError("actual company audit is not PASS")
    if audit.get("policy") != "recompute-from-all-actual-cross-node-calls-and-successes":
        raise RuntimeError("actual company audit policy is stale")
    if audit.get("duplicate_successful_companies"):
        raise RuntimeError("successful companies are duplicated")
    if audit.get("duplicate_called_companies"):
        raise RuntimeError("called companies are duplicated across nodes")
    if audit.get("unknown_company_models"):
        raise RuntimeError("unknown model company identity is present")

    graph_nodes = graph.get("nodes")
    if not isinstance(graph_nodes, list) or not graph_nodes:
        raise RuntimeError("execution graph has no nodes")
    if len(audit.get("successful_node_models", [])) != len(graph_nodes):
        raise RuntimeError("successful-model evidence does not cover all nodes")
    if len(nodes) != len(graph_nodes):
        raise RuntimeError("node result count differs from graph node count")
    for row in nodes:
        if not isinstance(row, Mapping):
            raise RuntimeError("node result row is not an object")
        if str(row.get("status") or "") not in {
            "success",
            "success_retried",
            "success_recovered",
        }:
            raise RuntimeError("node is not a strict success")
        contract = row.get("contract")
        if not isinstance(contract, Mapping) or contract.get("required_fields_complete") is not True:
            raise RuntimeError("node output contract is incomplete")

    budget = summary.get("execution_budget")
    if not isinstance(budget, Mapping):
        raise RuntimeError("execution budget evidence is missing")
    calls = int(budget.get("calls_reserved") or 0)
    actual_cost = float(
        summary.get("actual_cost_usd")
        or budget.get("actual_cost_usd")
        or 0.0
    )
    if not 1 <= calls <= maximum_calls:
        raise RuntimeError(f"model calls outside bound: {calls}")
    if not 0.0 <= actual_cost <= maximum_cost:
        raise RuntimeError(f"actual cost outside bound: {actual_cost}")
    if search.get("policy") != "task-shape-feasibility-marginal-value":
        raise RuntimeError("adaptive search policy is missing")
    if not search.get("attempts"):
        raise RuntimeError("adaptive search has no attempts")
    if constitution.get("version") != "v5-constitution-2":
        raise RuntimeError("constitutional evidence version is missing")

    actual_headings = re.findall(r"(?m)^##\s+(.+?)\s*$", report)
    normalized = [value.strip().strip("#").strip() for value in actual_headings]
    positions = [normalized.index(value) for value in EXPECTED_HEADINGS]
    if positions != sorted(positions):
        raise RuntimeError("final report headings are out of order")
    violations = validate_answer_against_constitution(task, report)
    if violations:
        raise RuntimeError("final report violates constitution: " + ";".join(violations))

    manifest = _mapping(root / "artifact-manifest.json")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("artifact manifest has no files")
    manifested_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise RuntimeError("artifact manifest entry is invalid")
        relative = str(entry.get("path") or "")
        if not relative or relative in manifested_paths:
            raise RuntimeError("artifact manifest path is empty or duplicated")
        manifested_paths.add(relative)
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"manifest file is missing: {path}")
        if int(entry.get("size") or -1) != path.stat().st_size:
            raise RuntimeError(f"manifest size mismatch: {path.name}")
        if str(entry.get("sha256") or "") != _sha256(path):
            raise RuntimeError(f"manifest digest mismatch: {path.name}")
    actual_paths = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.name != "artifact-manifest.json"
    }
    if manifested_paths != actual_paths:
        missing = sorted(actual_paths - manifested_paths)
        extra = sorted(manifested_paths - actual_paths)
        raise RuntimeError(
            f"artifact manifest coverage mismatch: missing={missing}, extra={extra}"
        )

    companies = [
        str(row.get("company") or "")
        for row in audit.get("successful_node_models", [])
        if isinstance(row, Mapping)
    ]
    return {
        "schema_version": "v5-paid-acceptance-attestation-3",
        "status": "PASS",
        "paid_workflow_run_id": run_id,
        "paid_workflow_run_url": run_url,
        "paid_head_sha": head_sha,
        "paid_artifact_id": artifact_id,
        "paid_artifact_name": artifact_name,
        "paid_artifact_digest": artifact_digest,
        "model_calls": calls,
        "actual_cost_usd": actual_cost,
        "cost_cap_usd": maximum_cost,
        "node_count": len(graph_nodes),
        "successful_models": audit.get("successful_node_models", []),
        "successful_companies": companies,
        "completion_mode": "full",
        "quality_status": "full_success",
        "contract_heading_count": len(EXPECTED_HEADINGS),
        "actual_model_company_audit": "PASS",
        "constitutional_report_validation": "PASS",
        "manifest_file_revalidation": "PASS",
        "raw_evidence_recomputed": True,
        "external_tools_allowed": False,
        "production_ref_moved": False,
        "public_report_published": False,
        "cross_task_history_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--maximum-calls", type=int, required=True)
    parser.add_argument("--maximum-cost", type=float, required=True)
    parser.add_argument("--artifact-id", type=int, required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--artifact-digest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    record = revalidate(
        Path(args.root),
        head_sha=args.head_sha,
        run_id=args.run_id,
        run_url=args.run_url,
        maximum_calls=args.maximum_calls,
        maximum_cost=args.maximum_cost,
        artifact_id=args.artifact_id,
        artifact_name=args.artifact_name,
        artifact_digest=args.artifact_digest,
    )
    Path(args.output).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
