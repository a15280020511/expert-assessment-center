#!/usr/bin/env python3
"""Deterministic audit for governance-selected expert execution."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from artifact_manifest import sha256_file
from v5_governance_model_plan import validate_governance_model_plan
from v5_json_io import write_json
from v5_price_ranked_support import load_mapping, mapping_rows, models_from_graph
from v5_provider_lock import canonical_provider_lock

RUNTIME_VERSION = "v5-governance-plan-runtime-1"
EXECUTOR_ID = "v5-native-execution-engine"


def _write_output(name: str, value: Any) -> None:
    target = os.getenv("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            rendered = str(value).replace("\n", " ").replace("\r", " ")
            handle.write(f"{name}={rendered}\n")


def _record(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def _manifest_files_valid(root: Path) -> tuple[bool, list[str]]:
    rows = mapping_rows(load_mapping(root, "artifact-manifest.json").get("files"))
    if not rows:
        return False, ["artifact manifest is missing or empty"]
    failures: list[str] = []
    for row in rows:
        relative = str(row.get("path") or "")
        path = root / relative
        if not relative or not path.is_file():
            failures.append(f"manifest file is missing: {relative or 'unknown'}")
            continue
        if int(row.get("size") or -1) != path.stat().st_size:
            failures.append(f"manifest size mismatch: {relative}")
        if str(row.get("sha256") or "") != sha256_file(path):
            failures.append(f"manifest digest mismatch: {relative}")
    return not failures, failures


def audit(
    root: Path,
    *,
    execute_outcome: str,
    publish_outcome: str,
    require_manifest: bool = False,
) -> dict[str, Any]:
    ticket_status = load_mapping(root, "ticket-status.json")
    ticket = load_mapping(root, "ticket.json")
    plan_file = load_mapping(root, "governance-model-plan.json")
    runtime = load_mapping(root, "production-runtime.json")
    runtime_config = load_mapping(root, "v5-runtime-config.json")
    result = load_mapping(root, "expert-team-result.json")
    summary = load_mapping(root, "v5-execution-summary.json")
    graph = load_mapping(root, "v5-execution-graph.json")
    request_audit = load_mapping(root, "request-audit.json")
    ledger = load_mapping(root, "call-ledger.json")
    selection = load_mapping(root, "v5-price-ranked-selection.json")
    governance = load_mapping(root, "v5-governance-calls.json")
    bundle = load_mapping(root, "evidence-bundle.json")
    report_manifest = load_mapping(
        root, "report-comments/report-comments-manifest.json"
    )

    failures: list[str] = []
    _record(failures, execute_outcome == "success", f"execution step outcome is {execute_outcome}")
    _record(failures, publish_outcome == "success", f"report preparation outcome is {publish_outcome}")
    _record(failures, ticket_status.get("accepted") is True, "production ticket was not accepted")

    try:
        plan = validate_governance_model_plan(ticket, plan_file)
    except Exception as exc:  # noqa: BLE001
        plan = {}
        failures.append(f"governance model plan validation failed: {exc}")
    plan_sha = str(plan.get("plan_sha256") or "")

    total = int(ticket_status.get("calls") or 0)
    recovery = int(ticket_status.get("maximum_recovery_calls") or 0)
    initial = int(ticket_status.get("maximum_initial_calls") or 0)
    budget_valid = (
        4 <= total <= 16
        and 0 <= recovery < total
        and initial == total - recovery
        and initial >= 3
    )
    _record(failures, budget_valid, "approved governance-plan budget is invalid")

    _record(failures, runtime.get("runtime_version") == RUNTIME_VERSION, "production runtime version mismatch")
    _record(failures, result.get("runtime_version") == RUNTIME_VERSION, "result runtime version mismatch")
    _record(failures, runtime.get("fallback_policy") == "fail-closed-no-alternate-runtime", "fail-closed runtime evidence is missing")
    _record(failures, runtime.get("legacy_runtime_present") is False, "legacy runtime absence is not proven")
    _record(failures, runtime.get("selection_authority") == "decision-system-governance", "runtime selection authority is not governance")
    _record(failures, runtime_config.get("selection_authority") == "decision-system-governance", "runtime config selection authority is not governance")
    _record(failures, result.get("selection_authority") == "decision-system-governance", "result selection authority is not governance")
    _record(failures, selection.get("selection_authority") == "decision-system-governance", "selection materialization authority is not governance")
    _record(failures, runtime.get("governance_model_plan_sha256") == plan_sha, "runtime model plan digest mismatch")
    _record(failures, runtime_config.get("governance_model_plan_sha256") == plan_sha, "runtime config model plan digest mismatch")
    _record(failures, result.get("governance_model_plan_sha256") == plan_sha, "result model plan digest mismatch")
    _record(failures, bundle.get("governance_model_plan_sha256") == plan_sha, "evidence bundle model plan digest mismatch")

    for name, document in (
        ("runtime", runtime),
        ("runtime_config", runtime_config),
        ("result", result),
        ("selection", selection),
        ("bundle", bundle),
    ):
        _record(
            failures,
            document.get("model_selection_performed_locally") is False,
            f"{name} does not prove local model selection disabled",
        )
    for name, document in (
        ("runtime", runtime),
        ("runtime_config", runtime_config),
        ("selection", selection),
    ):
        _record(
            failures,
            document.get("model_reranking_performed_locally") is False,
            f"{name} does not prove local model reranking disabled",
        )
    _record(failures, runtime.get("model_substitution_allowed") is False, "runtime permits model substitution")
    _record(failures, runtime_config.get("model_substitution_allowed") is False, "runtime config permits model substitution")
    _record(failures, int(governance.get("actual_governance_calls") or 0) == 0, "expert center governance calls are not zero")

    graph_nodes = mapping_rows(graph.get("nodes"))
    planned_models = tuple(
        str(row.get("model") or "")
        for row in plan.get("selected_models", [])
        if isinstance(row, Mapping)
    )
    graph_models = tuple(models_from_graph(graph))
    _record(failures, graph_models == planned_models, "executed graph models differ from governance plan")
    _record(failures, 3 <= len(graph_nodes) <= min(6, initial if initial > 0 else 0), "expert graph node count violates approved bounds")
    final_nodes = graph.get("final_nodes")
    _record(failures, isinstance(final_nodes, list) and "expert-final-synthesis" in final_nodes, "final synthesis node is missing")
    _record(failures, selection.get("status") == "PASS", "governance plan materialization status is not PASS")
    _record(failures, selection.get("networkx_used_for_dag_validation") is True, "NetworkX DAG validation evidence is missing")

    requests = mapping_rows(request_audit.get("requests"))
    _record(failures, request_audit.get("status") == "PASS", "request audit status is not PASS")
    _record(failures, all(canonical_provider_lock(row) for row in requests), "one or more requests lacks exact provider lock")
    _record(failures, request_audit.get("external_tools_allowed") is False, "request audit does not prohibit tools")
    _record(failures, request_audit.get("provider_fallback_allowed") is False, "request audit permits provider fallback")

    ledger_summary = ledger.get("summary")
    ledger_summary = dict(ledger_summary) if isinstance(ledger_summary, Mapping) else {}
    call_count = int(ledger_summary.get("call_count") or 0)
    expert_calls = int(ledger_summary.get("expert_calls") or 0)
    local_governance_calls = int(
        ledger_summary.get("governance_calls_in_expert_center") or 0
    )
    actual_cost = float(ledger_summary.get("provider_actual_cost_usd") or 0.0)
    _record(failures, local_governance_calls == 0, "call ledger reports governance calls in expert center")
    _record(failures, call_count == expert_calls == len(requests), "call ledger and request audit disagree")
    _record(failures, call_count <= total, "model calls exceed approved ceiling")
    _record(failures, math.isfinite(actual_cost) and actual_cost >= 0, "provider actual cost is invalid")

    integrity = summary.get("quality_integrity")
    integrity = dict(integrity) if isinstance(integrity, Mapping) else {}
    answer = str(summary.get("final_answer") or result.get("final_answer") or "").strip()
    executor = str(summary.get("executor") or result.get("executor") or "")
    _record(failures, str(summary.get("status") or result.get("status") or "") == "success", "delivery status is not success")
    _record(failures, str(summary.get("completion_mode") or result.get("completion_mode") or "") == "full", "completion mode is not full")
    _record(failures, str(summary.get("quality_status") or result.get("quality_status") or "") == "full_success", "quality status is not full_success")
    _record(failures, integrity.get("status") == "PASS", "quality integrity status is not PASS")
    _record(failures, executor == EXECUTOR_ID, "native executor evidence is missing")
    _record(failures, len(answer) >= 160, "final answer is missing or too short")

    publication_status = str(report_manifest.get("publication_status") or "")
    report_files = report_manifest.get("files")
    _record(failures, publication_status == "prepared_full_success", "report publication package is not prepared_full_success")
    _record(failures, isinstance(report_files, list) and bool(report_files), "report publication package has no files")
    _record(failures, bundle.get("business_evidence_frozen") is True, "business evidence is not frozen")

    checks: dict[str, Any] = {
        "approved_total_calls": total,
        "approved_recovery_calls": recovery,
        "approved_initial_calls": initial,
        "budget_valid": budget_valid,
        "selection_authority": "decision-system-governance",
        "governance_model_plan_sha256": plan_sha,
        "planned_models": list(planned_models),
        "executed_models": list(graph_models),
        "node_count": len(graph_nodes),
        "request_count": len(requests),
        "call_count": call_count,
        "actual_cost_usd": actual_cost,
        "publication_status": publication_status,
    }
    if require_manifest:
        manifest_valid, manifest_failures = _manifest_files_valid(root)
        checks["artifact_manifest_valid"] = manifest_valid
        failures.extend(manifest_failures)

    failures = list(dict.fromkeys(failures))
    diagnosis = {
        "schema_version": "v5-governance-plan-execution-diagnosis-1",
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS" if not failures else "FAIL",
        "selection_authority": "decision-system-governance",
        "model_selection_performed_locally": False,
        "model_reranking_performed_locally": False,
        "model_substitution_allowed": False,
        "checks": checks,
        "failures": failures,
        "primary_failure": (
            {}
            if not failures
            else {
                "code": "GOVERNANCE_PLAN_AUDIT_FAILED",
                "stage": "deterministic-audit",
                "message": failures[0],
                "retryable": False,
            }
        ),
    }
    write_json(root / "execution-diagnosis.json", diagnosis)
    return diagnosis


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--execute-outcome", default="unknown")
    parser.add_argument("--publish-outcome", default="unknown")
    parser.add_argument("--require-manifest", action="store_true")
    args = parser.parse_args()
    diagnosis = audit(
        Path(args.output_dir),
        execute_outcome=args.execute_outcome,
        publish_outcome=args.publish_outcome,
        require_manifest=args.require_manifest,
    )
    _write_output("status", diagnosis["status"])
    _write_output("diagnosis", str(Path(args.output_dir) / "execution-diagnosis.json"))
    print(json.dumps(diagnosis, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
