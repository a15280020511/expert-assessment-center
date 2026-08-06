#!/usr/bin/env python3
"""Deterministic audit for top-50 OR-Tools expert execution."""
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
POOL_AUTHORITY = "decision-system-governance"
ASSIGNMENT_AUTHORITY = "expert-assessment-center-ortools"


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


def _state(root: Path) -> dict[str, Any]:
    return {
        "ticket_status": load_mapping(root, "ticket-status.json"),
        "ticket": load_mapping(root, "ticket.json"),
        "plan_file": load_mapping(root, "governance-model-plan.json"),
        "runtime": load_mapping(root, "production-runtime.json"),
        "runtime_config": load_mapping(root, "v5-runtime-config.json"),
        "result": load_mapping(root, "expert-team-result.json"),
        "summary": load_mapping(root, "v5-execution-summary.json"),
        "graph": load_mapping(root, "v5-execution-graph.json"),
        "request_audit": load_mapping(root, "request-audit.json"),
        "ledger": load_mapping(root, "call-ledger.json"),
        "selection": load_mapping(root, "v5-price-ranked-selection.json"),
        "governance": load_mapping(root, "v5-governance-calls.json"),
        "bundle": load_mapping(root, "evidence-bundle.json"),
        "report_manifest": load_mapping(root, "report-comments/report-comments-manifest.json"),
    }


def _validated_plan(state: Mapping[str, Any], failures: list[str]) -> dict[str, Any]:
    try:
        return validate_governance_model_plan(state["ticket"], state["plan_file"])
    except Exception as exc:  # noqa: BLE001
        failures.append(f"governance model plan validation failed: {exc}")
        return {}


def _budget(state: Mapping[str, Any], failures: list[str]) -> tuple[int, int, int, bool]:
    status = state["ticket_status"]
    total = int(status.get("calls") or 0)
    recovery = int(status.get("maximum_recovery_calls") or 0)
    initial = int(status.get("maximum_initial_calls") or 0)
    valid = 4 <= total <= 16 and 0 <= recovery < total and initial == total - recovery and initial >= 3
    _record(failures, valid, "approved governance-plan budget is invalid")
    return total, recovery, initial, valid


def _check_authority(
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
    failures: list[str],
) -> None:
    plan_sha = str(plan.get("plan_sha256") or "")
    top50 = plan.get("selected_from_top50_reasoning_pool_only") is True
    runtime = state["runtime"]
    config = state["runtime_config"]
    result = state["result"]
    selection = state["selection"]
    bundle = state["bundle"]
    expected_authority = ASSIGNMENT_AUTHORITY if top50 else POOL_AUTHORITY

    checks = (
        (runtime.get("runtime_version") == RUNTIME_VERSION, "production runtime version mismatch"),
        (result.get("runtime_version") == RUNTIME_VERSION, "result runtime version mismatch"),
        (runtime.get("fallback_policy") == "fail-closed-no-alternate-runtime", "fail-closed runtime evidence is missing"),
        (runtime.get("legacy_runtime_present") is False, "legacy runtime absence is not proven"),
        (runtime.get("candidate_pool_authority") == POOL_AUTHORITY, "runtime candidate-pool authority mismatch"),
        (config.get("candidate_pool_authority") == POOL_AUTHORITY, "runtime config candidate-pool authority mismatch"),
        (result.get("candidate_pool_authority") == POOL_AUTHORITY, "result candidate-pool authority mismatch"),
        (selection.get("candidate_pool_authority") == POOL_AUTHORITY, "selection candidate-pool authority mismatch"),
        (runtime.get("selection_authority") == expected_authority, "runtime selection authority mismatch"),
        (config.get("selection_authority") == expected_authority, "runtime config selection authority mismatch"),
        (result.get("selection_authority") == expected_authority, "result selection authority mismatch"),
        (selection.get("model_assignment_authority") == expected_authority, "selection assignment authority mismatch"),
        (runtime.get("governance_model_plan_sha256") == plan_sha, "runtime model plan digest mismatch"),
        (config.get("governance_model_plan_sha256") == plan_sha, "runtime config model plan digest mismatch"),
        (result.get("governance_model_plan_sha256") == plan_sha, "result model plan digest mismatch"),
        (bundle.get("governance_model_plan_sha256") == plan_sha, "evidence bundle model plan digest mismatch"),
    )
    for condition, message in checks:
        _record(failures, condition, message)

    if top50:
        for name, document in (
            ("runtime", runtime),
            ("runtime_config", config),
            ("result", result),
            ("selection", selection),
            ("bundle", bundle),
        ):
            _record(failures, document.get("model_selection_performed_locally") is True, f"{name} does not prove expert-center OR-Tools assignment")
        _record(failures, config.get("optimizer") == "ortools-cp-sat", "runtime config optimizer is not OR-Tools CP-SAT")
        _record(failures, config.get("optimizer_optimality_proven") is True, "runtime config lacks OR-Tools optimality proof")
        _record(failures, selection.get("optimizer_used") is True, "selection evidence does not prove optimizer use")
        _record(failures, selection.get("optimizer_optimality_proven") is True, "selection evidence lacks OR-Tools optimality proof")

    for name, document in (("runtime", runtime), ("runtime_config", config), ("selection", selection)):
        _record(failures, document.get("model_reranking_performed_locally") is False, f"{name} reports forbidden candidate-pool reranking")
    _record(failures, runtime.get("model_substitution_allowed") is False, "runtime permits model substitution")
    _record(failures, config.get("model_substitution_allowed") is False, "runtime config permits model substitution")
    _record(failures, runtime.get("claude_mechanism_enabled") is False, "runtime does not prove Claude mechanism disabled")
    governance_model_calls = int(state["governance"].get("actual_governance_calls") or 0)
    _record(failures, governance_model_calls == 0, "expert center governance calls are not zero")


def _check_graph(
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
    initial: int,
    failures: list[str],
) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    graph = state["graph"]
    selection = state["selection"]
    graph_nodes = mapping_rows(graph.get("nodes"))
    planned_models = tuple(
        str(row.get("model") or "")
        for row in plan.get("selected_models", [])
        if isinstance(row, Mapping)
    )
    graph_models = tuple(models_from_graph(graph))
    _record(failures, graph_models == planned_models, "executed graph models differ from expert OR-Tools plan")
    _record(failures, 3 <= len(graph_nodes) <= min(6, max(initial, 0)), "expert graph node count violates approved bounds")
    final_nodes = graph.get("final_nodes")
    _record(failures, isinstance(final_nodes, list) and "expert-final-synthesis" in final_nodes, "final synthesis node is missing")
    _record(failures, selection.get("status") == "PASS", "expert plan materialization status is not PASS")
    _record(failures, selection.get("networkx_used_for_dag_validation") is True, "NetworkX DAG validation evidence is missing")
    return planned_models, graph_models, len(graph_nodes)


def _check_requests_and_cost(
    state: Mapping[str, Any], total: int, failures: list[str]
) -> tuple[int, int, float]:
    request_audit = state["request_audit"]
    requests = mapping_rows(request_audit.get("requests"))
    _record(failures, request_audit.get("status") == "PASS", "request audit status is not PASS")
    _record(failures, all(canonical_provider_lock(row) for row in requests), "one or more requests contains a Provider routing restriction")
    _record(failures, all("provider" not in row for row in requests), "production request unexpectedly contains a provider object")
    _record(failures, request_audit.get("provider_routing_mode") == "unrestricted-openrouter", "request audit does not prove unrestricted Provider routing")
    _record(failures, request_audit.get("provider_restrictions_applied") is False, "request audit reports Provider restrictions")
    _record(failures, request_audit.get("provider_fallback_allowed") is True, "request audit disables Provider fallback")
    _record(failures, request_audit.get("unrestricted_provider_fallback_allowed") is True, "request audit does not allow unrestricted Provider fallback")
    _record(failures, request_audit.get("external_tools_allowed") is False, "request audit does not prohibit tools")

    summary = state["ledger"].get("summary")
    summary = dict(summary) if isinstance(summary, Mapping) else {}
    call_count = int(summary.get("call_count") or 0)
    expert_calls = int(summary.get("expert_calls") or 0)
    actual_cost = float(summary.get("provider_actual_cost_usd") or 0.0)
    _record(failures, int(summary.get("governance_calls_in_expert_center") or 0) == 0, "call ledger reports governance calls in expert center")
    _record(failures, call_count == expert_calls == len(requests), "call ledger and request audit disagree")
    _record(failures, call_count <= total, "model calls exceed approved ceiling")
    _record(failures, math.isfinite(actual_cost) and actual_cost >= 0, "provider actual cost is invalid")
    return len(requests), call_count, actual_cost


def _check_delivery(state: Mapping[str, Any], failures: list[str]) -> str:
    summary = state["summary"]
    result = state["result"]
    integrity = summary.get("quality_integrity")
    integrity = dict(integrity) if isinstance(integrity, Mapping) else {}
    answer = str(summary.get("final_answer") or result.get("final_answer") or "").strip()
    executor = str(summary.get("executor") or result.get("executor") or "")
    checks = (
        (str(summary.get("status") or result.get("status") or "") == "success", "delivery status is not success"),
        (str(summary.get("completion_mode") or result.get("completion_mode") or "") == "full", "completion mode is not full"),
        (str(summary.get("quality_status") or result.get("quality_status") or "") == "full_success", "quality status is not full_success"),
        (integrity.get("status") == "PASS", "quality integrity status is not PASS"),
        (executor == EXECUTOR_ID, "native executor evidence is missing"),
        (len(answer) >= 160, "final answer is missing or too short"),
    )
    for condition, message in checks:
        _record(failures, condition, message)
    manifest = state["report_manifest"]
    publication_status = str(manifest.get("publication_status") or "")
    report_files = manifest.get("files")
    _record(failures, publication_status == "prepared_full_success", "report publication package is not prepared_full_success")
    _record(failures, isinstance(report_files, list) and bool(report_files), "report publication package has no files")
    _record(failures, state["bundle"].get("business_evidence_frozen") is True, "business evidence is not frozen")
    return publication_status


def _diagnosis(
    failures: list[str], checks: Mapping[str, Any], *, top50: bool
) -> dict[str, Any]:
    unique = list(dict.fromkeys(failures))
    primary = {}
    if unique:
        primary = {
            "code": "GOVERNANCE_PLAN_AUDIT_FAILED",
            "stage": "deterministic-audit",
            "message": unique[0],
            "retryable": False,
        }
    return {
        "schema_version": "v5-governance-plan-execution-diagnosis-2-open-provider",
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS" if not unique else "FAIL",
        "candidate_pool_authority": POOL_AUTHORITY,
        "selection_authority": ASSIGNMENT_AUTHORITY if top50 else POOL_AUTHORITY,
        "model_selection_performed_locally": top50,
        "model_reranking_performed_locally": False,
        "model_substitution_allowed": False,
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "unrestricted_provider_fallback_allowed": True,
        "claude_mechanism_enabled": False,
        "governance_model_calls": 0,
        "checks": dict(checks),
        "failures": unique,
        "primary_failure": primary,
    }


def audit(
    root: Path,
    *,
    execute_outcome: str,
    publish_outcome: str,
    require_manifest: bool = False,
) -> dict[str, Any]:
    state = _state(root)
    failures: list[str] = []
    _record(failures, execute_outcome == "success", f"execution step outcome is {execute_outcome}")
    _record(failures, publish_outcome == "success", f"report preparation outcome is {publish_outcome}")
    _record(failures, state["ticket_status"].get("accepted") is True, "production ticket was not accepted")
    plan = _validated_plan(state, failures)
    total, recovery, initial, budget_valid = _budget(state, failures)
    plan_sha = str(plan.get("plan_sha256") or "")
    top50 = plan.get("selected_from_top50_reasoning_pool_only") is True
    _check_authority(state, plan, failures)
    planned, executed, node_count = _check_graph(state, plan, initial, failures)
    request_count, call_count, actual_cost = _check_requests_and_cost(state, total, failures)
    publication_status = _check_delivery(state, failures)
    checks: dict[str, Any] = {
        "approved_total_calls": total,
        "approved_recovery_calls": recovery,
        "approved_initial_calls": initial,
        "budget_valid": budget_valid,
        "candidate_pool_authority": POOL_AUTHORITY,
        "selection_authority": ASSIGNMENT_AUTHORITY if top50 else POOL_AUTHORITY,
        "governance_model_plan_sha256": plan_sha,
        "planned_models": list(planned),
        "executed_models": list(executed),
        "node_count": node_count,
        "request_count": request_count,
        "call_count": call_count,
        "actual_cost_usd": actual_cost,
        "provider_routing_mode": "unrestricted-openrouter",
        "publication_status": publication_status,
    }
    if require_manifest:
        valid, manifest_failures = _manifest_files_valid(root)
        checks["artifact_manifest_valid"] = valid
        failures.extend(manifest_failures)
    diagnosis = _diagnosis(failures, checks, top50=top50)
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
