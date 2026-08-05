#!/usr/bin/env python3
"""Deterministic audit for the zero-governance price-ranked production path."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from v5_json_io import load_json_or_default, write_json
from v5_price_ranked_artifact_manifest import sha256_file
from v5_provider_lock import canonical_provider_lock

RUNTIME_VERSION = "v5-price-ranked-runtime-1"
EXECUTOR_ID = "v5-native-execution-engine"


def _write_output(name: str, value: Any) -> None:
    target = os.getenv("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            rendered = str(value).replace("\n", " ").replace("\r", " ")
            handle.write(f"{name}={rendered}\n")


def _mapping(root: Path, name: str) -> dict[str, Any]:
    value = load_json_or_default(root / name, {})
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _manifest_files_valid(root: Path) -> tuple[bool, list[str]]:
    manifest = _mapping(root, "artifact-manifest.json")
    rows = _rows(manifest.get("files"))
    failures: list[str] = []
    if not rows:
        return False, ["artifact manifest is missing or empty"]
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


def _selection_checks(
    selection: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    candidate_set = _rows(selection.get("cheapest_candidate_set"))
    assigned = _rows(selection.get("selected_endpoints"))
    costs = [float(row.get("estimated_call_cost_usd") or 0.0) for row in candidate_set]
    companies = [str(row.get("company") or "") for row in candidate_set]
    if selection.get("status") != "PASS":
        failures.append("price-ranked selection status is not PASS")
    if selection.get("selection_authority") != "python-price-ranked-orchestrator":
        failures.append(
            "selection authority is not the deterministic Python orchestrator"
        )
    if selection.get("claude_calls") not in {0, None}:
        failures.append("selection audit reports a Claude call")
    if selection.get("gpt_selection_calls") not in {0, None}:
        failures.append("selection audit reports a GPT selection call")
    if selection.get("networkx_used_for_dag_validation") is not True:
        failures.append("NetworkX DAG validation evidence is missing")
    if not candidate_set or not assigned:
        failures.append("selected endpoint evidence is missing")
    if costs != sorted(costs):
        failures.append("candidate set is not ordered by estimated task cost")
    if any(not company or company == "unknown" for company in companies):
        failures.append("one or more selected model companies is unresolved")
    if len(companies) != len(set(companies)):
        failures.append("selected model companies are not globally distinct")
    return {
        "candidate_count": len(candidate_set),
        "assigned_count": len(assigned),
        "candidate_costs": costs,
        "companies": companies,
    }, failures


def audit(
    root: Path,
    *,
    execute_outcome: str,
    publish_outcome: str,
    require_manifest: bool = False,
) -> dict[str, Any]:
    ticket = _mapping(root, "ticket-status.json")
    runtime = _mapping(root, "production-runtime.json")
    runtime_config = _mapping(root, "v5-runtime-config.json")
    result = _mapping(root, "expert-team-result.json")
    summary = _mapping(root, "v5-execution-summary.json")
    graph = _mapping(root, "v5-execution-graph.json")
    request_audit = _mapping(root, "request-audit.json")
    ledger = _mapping(root, "call-ledger.json")
    selection = _mapping(root, "v5-price-ranked-selection.json")
    governance = _mapping(root, "v5-governance-calls.json")
    bundle = _mapping(root, "evidence-bundle.json")
    report_manifest = _mapping(
        root,
        "report-comments/report-comments-manifest.json",
    )
    nodes = _rows(graph.get("nodes"))
    final_nodes = (
        graph.get("final_nodes")
        if isinstance(graph.get("final_nodes"), list)
        else []
    )
    requests = _rows(request_audit.get("requests"))
    ledger_summary = ledger.get("summary")
    ledger_summary = (
        dict(ledger_summary) if isinstance(ledger_summary, Mapping) else {}
    )
    failures: list[str] = []
    checks: dict[str, Any] = {}

    if execute_outcome != "success":
        failures.append(f"execution step outcome is {execute_outcome}")
    if publish_outcome != "success":
        failures.append(f"report preparation outcome is {publish_outcome}")
    if ticket.get("accepted") is not True:
        failures.append("production ticket was not accepted")

    total = int(ticket.get("calls") or 0)
    recovery = int(ticket.get("maximum_recovery_calls") or 0)
    initial = int(ticket.get("maximum_initial_calls") or 0)
    budget_valid = (
        4 <= total <= 16
        and 0 <= recovery < total
        and initial == total - recovery
        and initial >= 3
    )
    if not budget_valid:
        failures.append("approved price-ranked budget is invalid or inconsistent")
    checks.update(
        {
            "approved_total_calls": total,
            "approved_recovery_calls": recovery,
            "approved_initial_calls": initial,
            "budget_valid": budget_valid,
        }
    )

    if runtime.get("runtime_version") != RUNTIME_VERSION:
        failures.append("production runtime version is not price-ranked runtime v1")
    if result.get("runtime_version") != RUNTIME_VERSION:
        failures.append("result runtime version is not price-ranked runtime v1")
    if runtime.get("fallback_policy") != "fail-closed-no-alternate-runtime":
        failures.append("fail-closed runtime evidence is missing")
    if runtime.get("legacy_runtime_present") is not False:
        failures.append("legacy runtime absence is not proven")
    if runtime.get("claude_mechanism_enabled") is not False:
        failures.append("Claude mechanism is not explicitly disabled")
    if int(runtime.get("claude_calls") or 0) != 0:
        failures.append("runtime reports a Claude call")
    if int(runtime.get("governance_model_calls") or 0) != 0:
        failures.append("runtime reports governance model calls")
    if runtime_config.get("claude_mechanism_enabled") is not False:
        failures.append("pipeline runtime config does not disable Claude")
    if int(governance.get("actual_governance_calls") or 0) != 0:
        failures.append("governance ledger is not zero-call")
    if int(governance.get("claude_red_team_calls") or 0) != 0:
        failures.append("governance ledger reports a Claude red-team call")

    status = str(summary.get("status") or result.get("status") or "")
    completion = str(
        summary.get("completion_mode") or result.get("completion_mode") or ""
    )
    quality = str(
        summary.get("quality_status") or result.get("quality_status") or ""
    )
    integrity = summary.get("quality_integrity")
    integrity = dict(integrity) if isinstance(integrity, Mapping) else {}
    answer = str(
        summary.get("final_answer") or result.get("final_answer") or ""
    ).strip()
    executor = str(summary.get("executor") or result.get("executor") or "")
    if status != "success":
        failures.append(f"delivery status is {status or 'missing'}")
    if completion != "full":
        failures.append(f"completion mode is {completion or 'missing'}, not full")
    if quality != "full_success":
        failures.append(f"quality status is {quality or 'missing'}, not full_success")
    if integrity.get("status") != "PASS":
        failures.append("quality integrity status is not PASS")
    if executor != EXECUTOR_ID:
        failures.append("native executor evidence is missing")
    if len(answer) < 160:
        failures.append("final answer is missing or too short")

    if not 3 <= len(nodes) <= min(6, initial if initial > 0 else 0):
        failures.append("expert graph node count violates approved bounds")
    if "expert-final-synthesis" not in final_nodes:
        failures.append("final synthesis node is missing from final_nodes")
    selection_checks, selection_failures = _selection_checks(selection)
    failures.extend(selection_failures)
    if selection_checks["candidate_count"] != len(nodes):
        failures.append(
            "cheapest selected candidate count does not match graph node count"
        )

    call_count = int(ledger_summary.get("call_count") or 0)
    expert_calls = int(ledger_summary.get("expert_calls") or 0)
    governance_calls = int(ledger_summary.get("governance_calls") or 0)
    if request_audit.get("status") != "PASS":
        failures.append("request audit status is not PASS")
    if any(not canonical_provider_lock(row) for row in requests):
        failures.append("one or more expert requests lacks an exact provider lock")
    if request_audit.get("external_tools_allowed") is not False:
        failures.append("request audit does not prove external tools forbidden")
    if request_audit.get("provider_fallback_allowed") is not False:
        failures.append("request audit does not prove provider fallback forbidden")
    if governance_calls != 0:
        failures.append("normalized call ledger reports governance calls")
    if call_count != expert_calls or call_count != len(requests):
        failures.append("call ledger and complete request audit disagree")
    if call_count > total:
        failures.append("model calls exceed the approved total ceiling")
    actual_cost = float(ledger_summary.get("provider_actual_cost_usd") or 0.0)
    if not math.isfinite(actual_cost) or actual_cost < 0:
        failures.append("provider actual cost is invalid")

    publication_status = str(report_manifest.get("publication_status") or "")
    report_files = report_manifest.get("files")
    report_files = report_files if isinstance(report_files, list) else []
    if publication_status != "prepared_full_success":
        failures.append("report publication package is not prepared_full_success")
    if not report_files:
        failures.append("report publication package has no comment files")
    if bundle.get("business_evidence_frozen") is not True:
        failures.append("business evidence is not marked frozen")
    if bundle.get("claude_mechanism_enabled") is not False:
        failures.append("evidence bundle does not prove Claude disabled")

    if require_manifest:
        manifest_valid, manifest_failures = _manifest_files_valid(root)
        checks["artifact_manifest_valid"] = manifest_valid
        failures.extend(manifest_failures)

    failures = list(dict.fromkeys(failures))
    status_out = "PASS" if not failures else "FAIL"
    diagnosis = {
        "schema_version": "v5-price-ranked-execution-diagnosis-1",
        "status": status_out,
        "runtime_version": RUNTIME_VERSION,
        "failures": failures,
        "degradations": [],
        "primary_failure": (
            {
                "code": "PRICE_RANKED_AUDIT_FAILED",
                "stage": "deterministic-audit",
                "message": failures[0],
                "retryable": False,
            }
            if failures
            else {}
        ),
        "checks": {
            **checks,
            **selection_checks,
            "runtime_version": runtime.get("runtime_version"),
            "claude_mechanism_enabled": runtime.get("claude_mechanism_enabled"),
            "governance_model_calls": governance_calls,
            "node_count": len(nodes),
            "model_calls": call_count,
            "request_count": len(requests),
            "actual_cost_usd": actual_cost,
            "publication_status": publication_status,
            "evidence_frozen": bundle.get("business_evidence_frozen"),
        },
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
    _write_output(
        "diagnosis",
        str(Path(args.output_dir) / "execution-diagnosis.json"),
    )
    print(json.dumps(diagnosis, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
