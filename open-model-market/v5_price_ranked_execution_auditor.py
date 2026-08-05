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
from v5_governance_selection import SELECTION_AUTHORITY
from v5_json_io import write_json
from v5_price_ranked_support import load_mapping, mapping_rows
from v5_provider_lock import canonical_provider_lock

RUNTIME_VERSION = "v5-price-ranked-runtime-1"
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


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


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
    ticket = load_mapping(root, "ticket-status.json")
    runtime = load_mapping(root, "production-runtime.json")
    runtime_config = load_mapping(root, "v5-runtime-config.json")
    plan = load_mapping(root, "governance-selection.json")
    validation = load_mapping(root, "governance-selection-validation.json")
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
    _record(failures, ticket.get("accepted") is True, "production ticket was not accepted")

    total = int(ticket.get("calls") or 0)
    recovery = int(ticket.get("maximum_recovery_calls") or 0)
    initial = int(ticket.get("maximum_initial_calls") or 0)
    budget_valid = (
        4 <= total <= 16
        and 0 <= recovery < total
        and initial == total - recovery
        and initial >= 3
    )
    _record(failures, budget_valid, "approved expert budget is invalid or inconsistent")

    _record(
        failures,
        runtime.get("runtime_version") == RUNTIME_VERSION,
        "production runtime version is invalid",
    )
    _record(
        failures,
        result.get("runtime_version") == RUNTIME_VERSION,
        "result runtime version is invalid",
    )
    for document, label in (
        (runtime, "production runtime"),
        (runtime_config, "runtime config"),
        (plan, "governance plan"),
        (validation, "governance validation"),
        (selection, "selection audit"),
        (result, "normalized result"),
        (bundle, "evidence bundle"),
    ):
        _record(
            failures,
            document.get("selection_authority") == SELECTION_AUTHORITY,
            f"{label} does not prove governance selection authority",
        )

    plan_sha = str(plan.get("plan_sha256") or "")
    _record(failures, bool(plan_sha), "governance selection plan digest is missing")
    for document, field, label in (
        (runtime, "selection_plan_sha256", "production runtime"),
        (runtime_config, "selection_plan_sha256", "runtime config"),
        (validation, "plan_sha256", "governance validation"),
        (selection, "selection_plan_sha256", "selection audit"),
        (result, "selection_plan_sha256", "normalized result"),
        (bundle, "selection_plan_sha256", "evidence bundle"),
    ):
        _record(
            failures,
            str(document.get(field) or "") == plan_sha,
            f"{label} governance plan digest mismatch",
        )
    _record(
        failures,
        validation.get("status") == "PASS",
        "governance selection validation is not PASS",
    )
    _record(
        failures,
        plan.get("expert_center_selection_allowed") is False,
        "governance plan does not disable expert-center selection",
    )
    _record(
        failures,
        plan.get("expert_center_catalog_fetch_allowed") is False,
        "governance plan does not disable expert-center catalog access",
    )
    _record(
        failures,
        plan.get("local_fallback_allowed") is False,
        "governance plan does not disable local fallback",
    )
    for document, field, label in (
        (runtime, "expert_center_selection_performed", "runtime selection"),
        (runtime, "expert_center_catalog_fetch_performed", "runtime catalog access"),
        (runtime_config, "expert_center_selection_present", "pipeline selection"),
        (runtime_config, "expert_center_catalog_fetch_present", "pipeline catalog access"),
        (selection, "expert_center_selection_performed", "selection audit local selection"),
        (selection, "expert_center_catalog_fetch_performed", "selection audit catalog access"),
        (selection, "local_fallback_used", "selection audit fallback"),
        (result, "expert_center_selection_performed", "result local selection"),
        (result, "expert_center_catalog_fetch_performed", "result catalog access"),
        (result, "local_fallback_used", "result local fallback"),
        (bundle, "expert_center_selection_performed", "bundle local selection"),
        (bundle, "expert_center_catalog_fetch_performed", "bundle catalog access"),
        (bundle, "local_fallback_used", "bundle local fallback"),
    ):
        _record(failures, document.get(field) is False, f"{label} is not false")
    _record(
        failures,
        runtime.get("local_selection_fallback_allowed") is False,
        "runtime does not explicitly forbid local selection fallback",
    )
    _record(
        failures,
        runtime.get("fallback_policy") == "fail-closed-no-local-selection-runtime",
        "fail-closed selection policy is missing",
    )
    _record(
        failures,
        runtime.get("legacy_runtime_present") is False,
        "legacy runtime absence is not proven",
    )
    _record(
        failures,
        int(governance.get("actual_governance_calls") or 0) == 0,
        "governance inference call ledger is not zero",
    )
    _record(
        failures,
        int(governance.get("claude_red_team_calls") or 0) == 0,
        "Claude call ledger is not zero",
    )

    status = str(summary.get("status") or result.get("status") or "")
    completion = str(
        summary.get("completion_mode") or result.get("completion_mode") or ""
    )
    quality = str(
        summary.get("quality_status") or result.get("quality_status") or ""
    )
    integrity = _mapping(summary.get("quality_integrity"))
    answer = str(
        summary.get("final_answer") or result.get("final_answer") or ""
    ).strip()
    executor = str(summary.get("executor") or result.get("executor") or "")
    _record(failures, status == "success", f"delivery status is {status or 'missing'}")
    _record(failures, completion == "full", f"completion mode is {completion or 'missing'}")
    _record(failures, quality == "full_success", f"quality status is {quality or 'missing'}")
    _record(failures, integrity.get("status") == "PASS", "quality integrity is not PASS")
    _record(failures, executor == EXECUTOR_ID, "native executor evidence is missing")
    _record(failures, len(answer) >= 160, "final answer is missing or too short")

    graph_nodes = mapping_rows(graph.get("nodes"))
    selected = mapping_rows(selection.get("selected_endpoints"))
    recoveries = mapping_rows(selection.get("recovery_endpoints"))
    expected_nodes = int(plan.get("selected_expert_count") or 0)
    _record(
        failures,
        3 <= len(graph_nodes) == expected_nodes <= min(6, initial if initial > 0 else 0),
        "graph node count differs from the governance-selected count",
    )
    _record(
        failures,
        len(selected) == expected_nodes,
        "selected endpoint evidence count differs from governance plan",
    )
    _record(
        failures,
        len(recoveries) == recovery,
        "recovery endpoint evidence count differs from approved reserve",
    )
    all_models = [
        str(row.get("model") or "") for row in [*selected, *recoveries]
    ]
    companies = [
        model.split("/", 1)[0].casefold() if "/" in model else ""
        for model in all_models
    ]
    _record(failures, all(companies), "one or more model companies are unresolved")
    _record(
        failures,
        len(companies) == len(set(companies)),
        "expert and recovery model companies are not globally unique",
    )
    _record(
        failures,
        selection.get("networkx_used_for_dag_validation") is True,
        "NetworkX DAG validation evidence is missing",
    )

    requests = mapping_rows(request_audit.get("requests"))
    ledger_summary = _mapping(ledger.get("summary"))
    call_count = int(ledger_summary.get("call_count") or 0)
    expert_calls = int(ledger_summary.get("expert_calls") or 0)
    governance_calls = int(ledger_summary.get("governance_calls") or 0)
    actual_cost = float(ledger_summary.get("provider_actual_cost_usd") or 0.0)
    _record(failures, request_audit.get("status") == "PASS", "request audit is not PASS")
    _record(
        failures,
        all(canonical_provider_lock(row) for row in requests),
        "one or more requests lacks an exact provider lock",
    )
    _record(
        failures,
        request_audit.get("external_tools_allowed") is False,
        "request audit does not prove external tools forbidden",
    )
    _record(
        failures,
        request_audit.get("provider_fallback_allowed") is False,
        "request audit does not prove provider fallback forbidden",
    )
    _record(failures, governance_calls == 0, "normalized ledger reports governance calls")
    _record(
        failures,
        call_count == expert_calls == len(requests),
        "call ledger and request audit disagree",
    )
    _record(failures, call_count <= total, "model calls exceed approved total ceiling")
    _record(
        failures,
        math.isfinite(actual_cost) and actual_cost >= 0,
        "provider actual cost is invalid",
    )

    publication_status = str(report_manifest.get("publication_status") or "")
    files = report_manifest.get("files")
    _record(
        failures,
        publication_status == "prepared_full_success",
        "report publication package is not prepared_full_success",
    )
    _record(failures, isinstance(files, list) and bool(files), "report package has no files")
    _record(
        failures,
        bundle.get("business_evidence_frozen") is True,
        "business evidence is not marked frozen",
    )

    checks: dict[str, Any] = {
        "approved_total_calls": total,
        "approved_recovery_calls": recovery,
        "approved_initial_calls": initial,
        "budget_valid": budget_valid,
        "selection_authority": SELECTION_AUTHORITY,
        "selection_plan_sha256": plan_sha,
        "selected_endpoint_count": len(selected),
        "recovery_endpoint_count": len(recoveries),
        "graph_node_count": len(graph_nodes),
        "model_companies": companies,
        "call_count": call_count,
        "actual_cost_usd": actual_cost,
        "publication_status": publication_status,
        "evidence_frozen": bundle.get("business_evidence_frozen"),
    }
    if require_manifest:
        manifest_valid, manifest_failures = _manifest_files_valid(root)
        checks["artifact_manifest_valid"] = manifest_valid
        failures.extend(manifest_failures)

    failures = list(dict.fromkeys(failures))
    diagnosis = {
        "schema_version": "v5-governance-selected-execution-audit-1",
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS" if not failures else "FAIL",
        "selection_authority": SELECTION_AUTHORITY,
        "selection_plan_sha256": plan_sha,
        "expert_center_selection_performed": False,
        "expert_center_catalog_fetch_performed": False,
        "local_fallback_used": False,
        "checks": checks,
        "failures": failures,
        "primary_failure": (
            {}
            if not failures
            else {
                "code": "GOVERNANCE_SELECTED_AUDIT_FAILED",
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
