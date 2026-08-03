#!/usr/bin/env python3
"""Deterministically audit one native V5 production ticket execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from v5_claude_red_team_policy import CLAUDE_RED_TEAM_GOVERNANCE_CALLS
from v5_json_io import load_json_or_default
RUNTIME_VERSION = "v5-gpt-claude-runtime-1"
EXECUTOR_ID = "v5-native-execution-engine"
ABSOLUTE_MAX_MODEL_CALLS = 16
ABSOLUTE_MAX_NODES = 16


def _write_output(name: str, value: Any) -> None:
    target = os.getenv("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            rendered = str(value).replace(chr(10), " ").replace(chr(13), " ")
            handle.write(f"{name}={rendered}\n")


def _positive_optional(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


class AuditEvidence:
    def __init__(self, root: Path) -> None:
        self.ticket = load_json_or_default(root / "ticket-status.json", {})
        self.result = load_json_or_default(root / "expert-team-result.json", {})
        self.summary = load_json_or_default(root / "v5-execution-summary.json", {})
        self.graph = load_json_or_default(root / "v5-execution-graph.json", {})
        self.request_audit = load_json_or_default(root / "request-audit.json", {})
        self.ledger = load_json_or_default(root / "call-ledger.json", {})
        self.runtime = load_json_or_default(root / "production-runtime.json", {})
        self.company_audit = load_json_or_default(root / "actual-model-company-audit.json", {})
        self.report_manifest = load_json_or_default(
            root / "report-comments" / "report-comments-manifest.json",
            {},
        )
        self.error = load_json_or_default(root / "expert-team-error.json", {})


class BudgetState:
    def __init__(
        self,
        total: int,
        governance: int,
        expert_total: int,
        recovery: int,
        initial: int,
        anomaly: float | None,
        valid: bool,
    ) -> None:
        self.total = total
        self.governance = governance
        self.expert_total = expert_total
        self.recovery = recovery
        self.initial = initial
        self.anomaly = anomaly
        self.valid = valid


class DeliveryState:
    def __init__(self, status: str, completion_mode: str, executor: str, answer: str) -> None:
        self.status = status
        self.completion_mode = completion_mode
        self.executor = executor
        self.answer = answer


class GraphState:
    def __init__(self, nodes: list[Any], final_nodes: list[Any], node_limit: int) -> None:
        self.nodes = nodes
        self.final_nodes = final_nodes
        self.node_limit = node_limit


class CompanyState:
    def __init__(
        self,
        status: str,
        policy: str,
        successful_models: list[Any],
        duplicate_companies: Mapping[str, Any],
        actual_companies: list[str],
    ) -> None:
        self.status = status
        self.policy = policy
        self.successful_models = successful_models
        self.duplicate_companies = duplicate_companies
        self.actual_companies = actual_companies


class CallState:
    def __init__(
        self,
        expert_calls: int,
        total_calls: int,
        runtime_expert_total: int,
        runtime_expert_initial: int,
        actual_cost: float,
    ) -> None:
        self.expert_calls = expert_calls
        self.total_calls = total_calls
        self.runtime_expert_total = runtime_expert_total
        self.runtime_expert_initial = runtime_expert_initial
        self.actual_cost = actual_cost


class RequestState:
    def __init__(self, status: str, expected: int, captured: int, ceiling: int) -> None:
        self.status = status
        self.expected = expected
        self.captured = captured
        self.ceiling = ceiling


class ReportState:
    def __init__(self, path: Path, manifest: Any, files: list[Any]) -> None:
        self.path = path
        self.manifest = manifest
        self.files = files


def _entry_outcome_failures(
    ticket: Any,
    budget_valid: bool,
    execute_outcome: str,
    publish_outcome: str,
    failures: list[str],
) -> None:
    if execute_outcome != "success":
        failures.append(f"V5 execution outcome is {execute_outcome}")
    if publish_outcome != "success":
        failures.append(f"report publication outcome is {publish_outcome}")
    if not isinstance(ticket, Mapping) or ticket.get("accepted") is not True:
        failures.append("production ticket was not accepted")
    if not budget_valid:
        failures.append(
            "approved V5 budget contract is missing, invalid, or internally inconsistent"
        )


def _runtime_envelope_failures(
    evidence: AuditEvidence,
    checks: dict[str, Any],
    failures: list[str],
) -> None:
    result = evidence.result
    runtime = evidence.runtime
    result_version = (
        str(result.get("runtime_version") or "")
        if isinstance(result, Mapping)
        else ""
    )
    runtime_version = (
        str(runtime.get("runtime_version") or "")
        if isinstance(runtime, Mapping)
        else ""
    )
    checks.update(
        {
            "native_runtime_version": RUNTIME_VERSION,
            "observed_result_runtime_version": result_version,
            "observed_runtime_envelope_version": runtime_version,
            "native_runtime_versions_consistent": (
                result_version == runtime_version == RUNTIME_VERSION
            ),
        }
    )
    if result_version != RUNTIME_VERSION or runtime_version != RUNTIME_VERSION:
        failures.append(
            "native runtime version evidence is missing or inconsistent"
        )
    if (
        not isinstance(runtime, Mapping)
        or runtime.get("fallback_policy") != "fail-closed-no-alternate-runtime"
    ):
        failures.append("fail-closed V5 runtime evidence is missing")
    if isinstance(result, Mapping) and result.get("fallback_used") is not False:
        failures.append(
            "an alternate runtime fallback was used or not explicitly disabled"
        )
    if not isinstance(runtime, Mapping) or runtime.get("legacy_runtime_present") is not False:
        failures.append("legacy runtime absence was not proven")
    if isinstance(result, Mapping) and result.get("legacy_runtime_present") is not False:
        failures.append("result envelope does not prove legacy runtime absence")


def _audit_entry_contract(
    evidence: AuditEvidence,
    checks: dict[str, Any],
    failures: list[str],
    *,
    execute_outcome: str,
    publish_outcome: str,
) -> BudgetState:
    ticket = evidence.ticket
    total = int(ticket.get("calls") or 0) if isinstance(ticket, Mapping) else 0
    governance = CLAUDE_RED_TEAM_GOVERNANCE_CALLS
    expert_total = max(0, total - governance)
    recovery = (
        int(ticket.get("maximum_recovery_calls") or 0)
        if isinstance(ticket, Mapping)
        else 0
    )
    initial = (
        int(ticket.get("maximum_initial_calls") or 0)
        if isinstance(ticket, Mapping)
        else 0
    )
    anomaly = (
        _positive_optional(ticket.get("cost_anomaly_usd"))
        if isinstance(ticket, Mapping)
        else None
    )
    valid = (
        4 <= total <= ABSOLUTE_MAX_MODEL_CALLS
        and expert_total >= 1
        and 0 <= recovery < expert_total
        and initial == expert_total - recovery
        and ticket.get("cost_policy") == "unbounded_with_anomaly_guard"
    ) if isinstance(ticket, Mapping) else False
    checks.update({
        "runtime_version": (
            evidence.result.get("runtime_version")
            if isinstance(evidence.result, Mapping)
            else None
        ),
        "execute_outcome": execute_outcome,
        "publish_outcome": publish_outcome,
        "approved_total_calls": total,
        "approved_governance_calls": governance,
        "approved_expert_calls": expert_total,
        "approved_recovery_calls": recovery,
        "approved_expert_initial_calls": initial,
        "cost_anomaly_usd": anomaly,
        "budget_contract_valid": valid,
    })
    _entry_outcome_failures(
        ticket,
        valid,
        execute_outcome,
        publish_outcome,
        failures,
    )
    _runtime_envelope_failures(evidence, checks, failures)
    return BudgetState(
        total,
        governance,
        expert_total,
        recovery,
        initial,
        anomaly,
        valid,
    )


def _record_native_executor_contract(
    checks: dict[str, Any],
    executor: str,
) -> bool:
    executor_valid = executor == EXECUTOR_ID
    checks.update(
        {
            "expected_executor": EXECUTOR_ID,
            "native_executor_valid": executor_valid,
            "native_contract_status": (
                "PASS"
                if executor_valid
                and checks.get("native_runtime_versions_consistent") is True
                else "FAIL"
            ),
        }
    )
    return executor_valid


def _audit_delivery(
    evidence: AuditEvidence,
    checks: dict[str, Any],
    failures: list[str],
) -> DeliveryState:
    summary = evidence.summary
    result = evidence.result
    status = (
        str(summary.get("status") or result.get("status") or "")
        if isinstance(summary, Mapping)
        else ""
    )
    completion_mode = (
        str(summary.get("completion_mode") or result.get("completion_mode") or "")
        if isinstance(summary, Mapping)
        else ""
    )
    executor = (
        str(summary.get("executor") or result.get("executor") or "")
        if isinstance(summary, Mapping)
        else ""
    )
    answer = (
        str(summary.get("final_answer") or result.get("final_answer") or "")
        if isinstance(summary, Mapping)
        else ""
    )
    executor_valid = _record_native_executor_contract(checks, executor)
    checks.update({
        "v5_status": status,
        "completion_mode": completion_mode,
        "executor": executor,
        "final_answer_chars": len(answer.strip()),
    })
    if status != "success":
        failures.append(f"V5 delivery status is {status or 'missing'}")
    if completion_mode != "full":
        failures.append(
            f"V5 completion mode is {completion_mode or 'missing'}, not full"
        )
    if not executor_valid:
        failures.append("native executor evidence is missing or inconsistent")
    if len(answer.strip()) < 160:
        failures.append("V5 final answer is missing or too short")
    return DeliveryState(status, completion_mode, executor, answer)


def _audit_graph(
    evidence: AuditEvidence,
    budget: BudgetState,
    checks: dict[str, Any],
    failures: list[str],
) -> GraphState:
    graph = evidence.graph
    nodes = (
        graph.get("nodes")
        if isinstance(graph, Mapping) and isinstance(graph.get("nodes"), list)
        else []
    )
    final_nodes = (
        graph.get("final_nodes")
        if isinstance(graph, Mapping) and isinstance(graph.get("final_nodes"), list)
        else []
    )
    node_limit = (
        min(ABSOLUTE_MAX_NODES, budget.initial) if budget.initial > 0 else 0
    )
    checks.update({
        "node_count": len(nodes),
        "final_node_count": len(final_nodes),
        "approved_node_limit": node_limit,
    })
    if not nodes or node_limit <= 0 or len(nodes) > node_limit:
        failures.append(
            "V5 graph node count exceeds the approved initial-call capacity: "
            f"{len(nodes)} > {node_limit}"
        )
    if not final_nodes:
        failures.append("V5 graph has no final node")
    return GraphState(nodes, final_nodes, node_limit)


def _audit_companies(
    evidence: AuditEvidence,
    graph: GraphState,
    checks: dict[str, Any],
    failures: list[str],
) -> CompanyState:
    audit = evidence.company_audit
    status = (
        str(audit.get("status") or "missing")
        if isinstance(audit, Mapping)
        else "missing"
    )
    policy = str(audit.get("policy") or "") if isinstance(audit, Mapping) else ""
    successful_models = (
        audit.get("successful_node_models")
        if isinstance(audit, Mapping)
        and isinstance(audit.get("successful_node_models"), list)
        else []
    )
    duplicate_companies = (
        audit.get("duplicate_successful_companies")
        if isinstance(audit, Mapping)
        and isinstance(audit.get("duplicate_successful_companies"), Mapping)
        else {}
    )
    actual_companies = [
        str(row.get("company") or "")
        for row in successful_models
        if isinstance(row, Mapping)
    ]
    checks.update({
        "actual_model_company_audit_status": status,
        "actual_model_company_audit_policy": policy,
        "actual_successful_model_count": len(successful_models),
        "actual_successful_companies": actual_companies,
        "duplicate_actual_successful_companies": duplicate_companies,
    })
    if status != "PASS":
        failures.append("actual successful model-company audit is missing or failed")
    if policy != "recompute-from-actual-successful-node-models":
        failures.append(
            "actual model-company audit did not recompute from resolved models"
        )
    if len(successful_models) != len(graph.nodes):
        failures.append(
            "actual successful model-company evidence does not cover every node: "
            f"{len(successful_models)}/{len(graph.nodes)}"
        )
    if duplicate_companies or len(actual_companies) != len(set(actual_companies)):
        failures.append("actual successful model companies are not globally unique")
    if any(not company or company == "unknown" for company in actual_companies):
        failures.append("actual successful model company identity is unresolved")
    return CompanyState(
        status,
        policy,
        successful_models,
        duplicate_companies,
        actual_companies,
    )


def _audit_calls(
    evidence: AuditEvidence,
    budget_state: BudgetState,
    checks: dict[str, Any],
    failures: list[str],
) -> CallState:
    summary = evidence.summary
    budget = (
        summary.get("execution_budget")
        if isinstance(summary, Mapping)
        and isinstance(summary.get("execution_budget"), Mapping)
        else {}
    )
    expert_calls = int(budget.get("calls_reserved") or 0)
    runtime_expert_total = int(budget.get("maximum_total_calls") or 0)
    runtime_expert_initial = int(budget.get("maximum_initial_calls") or 0)
    total_calls = budget_state.governance + expert_calls
    actual_cost = (
        float(summary.get("actual_cost_usd") or budget.get("actual_cost_usd") or 0.0)
        if isinstance(summary, Mapping)
        else 0.0
    )
    checks.update({
        "model_calls": total_calls,
        "governance_model_calls": budget_state.governance,
        "expert_model_calls": expert_calls,
        "runtime_expert_call_ceiling": runtime_expert_total,
        "runtime_expert_initial_call_ceiling": runtime_expert_initial,
        "absolute_maximum_model_calls": ABSOLUTE_MAX_MODEL_CALLS,
        "actual_cost_usd": actual_cost,
    })
    if expert_calls <= 0:
        failures.append("V5 execution performed no expert model calls")
    elif expert_calls > budget_state.expert_total:
        failures.append(
            "V5 expert calls exceed the approved expert bound: "
            f"{expert_calls}/{budget_state.expert_total}"
        )
    if total_calls > budget_state.total:
        failures.append(
            "V5 total calls exceed the approved ticket bound: "
            f"{total_calls}/{budget_state.total}"
        )
    if runtime_expert_total != budget_state.expert_total:
        failures.append(
            "runtime expert-call ceiling differs from approved ticket: "
            f"{runtime_expert_total}/{budget_state.expert_total}"
        )
    if runtime_expert_initial != budget_state.initial:
        failures.append(
            "runtime expert initial-call ceiling differs from approved ticket: "
            f"{runtime_expert_initial}/{budget_state.initial}"
        )
    if not math.isfinite(actual_cost) or actual_cost < 0:
        failures.append("V5 actual cost is invalid")
    if (
        budget_state.anomaly is not None
        and actual_cost > budget_state.anomaly + 1e-12
    ):
        failures.append(
            "V5 actual cost exceeded the approved anomaly stop: "
            f"{actual_cost}/{budget_state.anomaly}"
        )
    return CallState(
        expert_calls,
        total_calls,
        runtime_expert_total,
        runtime_expert_initial,
        actual_cost,
    )


def _request_audit_values(
    evidence: AuditEvidence,
    calls: CallState,
) -> tuple[
    str,
    int,
    int,
    int,
    int,
    int,
    list[Any],
    Any,
]:
    audit = evidence.request_audit if isinstance(evidence.request_audit, Mapping) else {}
    status = str(audit.get("status") or "missing")
    expected = calls.total_calls
    captured = int(
        audit.get("request_count")
        or audit.get("captured_request_count")
        or 0
    )
    ceiling = int(audit.get("approved_total_call_ceiling") or 0)
    governance_count = int(audit.get("governance_request_count") or 0)
    expert_count = int(audit.get("expert_request_count") or 0)
    requests = audit.get("requests", [])
    if not isinstance(requests, list):
        requests = []
    return (
        status,
        expected,
        captured,
        ceiling,
        governance_count,
        expert_count,
        requests,
        audit.get("external_tools_allowed"),
    )


def _record_request_checks(
    checks: dict[str, Any],
    *,
    status: str,
    expected: int,
    captured: int,
    ceiling: int,
    governance_count: int,
    expert_count: int,
    requests: list[Any],
    tools_allowed: Any,
) -> None:
    checks.update(
        {
            "request_audit_status": status,
            "expected_request_count": expected,
            "captured_request_count": captured,
            "governance_request_count": governance_count,
            "expert_request_count": expert_count,
            "request_row_count": len(requests),
            "request_approved_total_call_ceiling": ceiling,
            "external_tools_allowed": tools_allowed,
        }
    )


def _append_request_failures(
    failures: list[str],
    *,
    status: str,
    expected: int,
    captured: int,
    ceiling: int,
    governance_count: int,
    expert_count: int,
    requests: list[Any],
    tools_allowed: Any,
    budget: BudgetState,
    calls: CallState,
) -> None:
    if status != "PASS":
        failures.append(f"V5 request audit status is {status}")
    if captured != expected:
        failures.append(
            "V5 request evidence is incomplete: "
            f"captured={captured}, expected={expected}"
        )
    if governance_count != budget.governance:
        failures.append(
            "governance request count differs from approved governance calls"
        )
    if expert_count != calls.expert_calls:
        failures.append("expert request count differs from expert attempts")
    if requests and len(requests) != captured:
        failures.append("request detail rows differ from captured request count")
    if captured > budget.total or ceiling != budget.total:
        failures.append(
            "request audit does not prove compliance with the approved total-call ceiling"
        )
    if tools_allowed is not False:
        failures.append("external-tool prohibition evidence is missing")


def _audit_requests(
    evidence: AuditEvidence,
    budget: BudgetState,
    calls: CallState,
    checks: dict[str, Any],
    failures: list[str],
) -> RequestState:
    (
        status,
        expected,
        captured,
        ceiling,
        governance_count,
        expert_count,
        requests,
        tools_allowed,
    ) = _request_audit_values(evidence, calls)
    _record_request_checks(
        checks,
        status=status,
        expected=expected,
        captured=captured,
        ceiling=ceiling,
        governance_count=governance_count,
        expert_count=expert_count,
        requests=requests,
        tools_allowed=tools_allowed,
    )
    _append_request_failures(
        failures,
        status=status,
        expected=expected,
        captured=captured,
        ceiling=ceiling,
        governance_count=governance_count,
        expert_count=expert_count,
        requests=requests,
        tools_allowed=tools_allowed,
        budget=budget,
        calls=calls,
    )
    return RequestState(status, expected, captured, ceiling)


def _audit_ledger(
    evidence: AuditEvidence,
    budget: BudgetState,
    calls: CallState,
    checks: dict[str, Any],
    failures: list[str],
) -> None:
    summary = (
        evidence.ledger.get("summary")
        if isinstance(evidence.ledger, Mapping)
        and isinstance(evidence.ledger.get("summary"), Mapping)
        else {}
    )
    if int(summary.get("call_count") or 0) != calls.total_calls:
        failures.append("V5 call ledger total does not match execution evidence")
    if int(summary.get("governance_calls") or 0) != budget.governance:
        failures.append("V5 call ledger governance count is inconsistent")
    if int(summary.get("expert_calls") or 0) != calls.expert_calls:
        failures.append("V5 call ledger expert count is inconsistent")
    if int(summary.get("approved_total_call_ceiling") or 0) != budget.total:
        failures.append(
            "V5 call ledger does not preserve the approved total-call ceiling"
        )
    if int(summary.get("approved_recovery_call_ceiling") or 0) != budget.recovery:
        failures.append(
            "V5 call ledger does not preserve the approved recovery-call ceiling"
        )
    provider_count = int(summary.get("substantive_provider_count") or 0)
    checks["substantive_provider_count"] = provider_count
    checks["substantive_providers"] = summary.get("substantive_providers") or []
    if provider_count <= 0:
        failures.append("V5 Provider evidence is missing")


def _audit_report(
    root: Path,
    evidence: AuditEvidence,
    checks: dict[str, Any],
    failures: list[str],
) -> ReportState:
    report_path = root / "expert-team-report.md"
    manifest = evidence.report_manifest
    files = (
        manifest.get("files")
        if isinstance(manifest, Mapping) and isinstance(manifest.get("files"), list)
        else []
    )
    checks["report_comment_count"] = len(files)
    if not report_path.is_file() or not manifest:
        failures.append("V5 report or publication manifest is missing")
        return ReportState(report_path, manifest, files)
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    checks["report_sha256"] = digest
    if digest != manifest.get("report_sha256"):
        failures.append("published report SHA256 does not match V5 report")
    run_url = str(manifest.get("run_url") or "").strip().rstrip("/")
    run_id = str(manifest.get("run_id") or "").strip()
    expected_suffix = f"/actions/runs/{run_id}" if run_id else ""
    run_evidence_valid = bool(run_url) and run_id.isdigit() and run_url.endswith(expected_suffix)
    checks.update({
        "report_run_url": run_url,
        "report_run_id": run_id,
        "report_run_evidence_valid": run_evidence_valid,
    })
    if not run_evidence_valid:
        failures.append("published report run identity is missing or invalid")
    for index, filename in enumerate(files, 1):
        comment_path = root / "report-comments" / str(filename)
        if not comment_path.is_file():
            failures.append(f"report comment file is missing: {filename}")
            continue
        comment = comment_path.read_text(encoding="utf-8")
        marker = f"expert-team-report-run:{run_id}:part:{index:03d}"
        if run_evidence_valid and (
            marker not in comment or f"- Run: `{run_url}`" not in comment
        ):
            failures.append(
                "report comment run identity is inconsistent: " f"{filename}"
            )
    return ReportState(report_path, manifest, files)


def _primary_failure(error: Any, failures: list[str]) -> dict[str, Any]:
    return {
        "code": (
            str(error.get("error_code") or ("NONE" if not failures else "V5_PRODUCTION_AUDIT_FAILED"))
            if isinstance(error, Mapping)
            else "V5_PRODUCTION_AUDIT_FAILED"
        ),
        "stage": (
            str(error.get("stage") or "v5-production-audit")
            if isinstance(error, Mapping)
            else "v5-production-audit"
        ),
        "message": (
            str(error.get("message") or (failures[0] if failures else ""))
            if isinstance(error, Mapping)
            else (failures[0] if failures else "")
        ),
        "retryable": bool(error.get("retryable")) if isinstance(error, Mapping) else False,
    }


def _stage_status(
    evidence: AuditEvidence,
    budget: BudgetState,
    delivery: DeliveryState,
    graph: GraphState,
    companies: CompanyState,
    calls: CallState,
    requests: RequestState,
    report: ReportState,
) -> dict[str, str]:
    return {
        "ticket": (
            "PASS"
            if isinstance(evidence.ticket, Mapping)
            and evidence.ticket.get("accepted")
            and budget.valid
            else "FAIL"
        ),
        "runtime": (
            "PASS"
            if delivery.status == "success" and delivery.completion_mode == "full"
            else "FAIL"
        ),
        "requests": (
            "PASS"
            if requests.status == "PASS"
            and requests.captured == requests.expected == calls.total_calls
            and requests.captured <= budget.total
            else "FAIL"
        ),
        "graph": (
            "PASS"
            if graph.nodes
            and len(graph.nodes) <= graph.node_limit
            and graph.final_nodes
            and companies.status == "PASS"
            and len(companies.successful_models) == len(graph.nodes)
            and not companies.duplicate_companies
            else "FAIL"
        ),
        "report": "PASS" if report.path.is_file() and report.manifest else "FAIL",
        "primary_artifact_manifest": "PENDING_UPLOAD",
        "final_attestation": "PENDING_POST_UPLOAD",
    }


def audit(
    root: Path,
    *,
    execute_outcome: str,
    publish_outcome: str,
) -> dict[str, Any]:
    failures: list[str] = []
    degradations: list[str] = []
    checks: dict[str, Any] = {}
    evidence = AuditEvidence(root)
    budget = _audit_entry_contract(
        evidence,
        checks,
        failures,
        execute_outcome=execute_outcome,
        publish_outcome=publish_outcome,
    )
    delivery = _audit_delivery(evidence, checks, failures)
    graph = _audit_graph(evidence, budget, checks, failures)
    companies = _audit_companies(evidence, graph, checks, failures)
    calls = _audit_calls(evidence, budget, checks, failures)
    requests = _audit_requests(evidence, budget, calls, checks, failures)
    _audit_ledger(evidence, budget, calls, checks, failures)
    report = _audit_report(root, evidence, checks, failures)
    audit_status = "FAIL" if failures else "DEGRADED" if degradations else "PASS"
    return {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": audit_status,
        "primary_failure": _primary_failure(evidence.error, failures),
        "stage_status": _stage_status(
            evidence,
            budget,
            delivery,
            graph,
            companies,
            calls,
            requests,
            report,
        ),
        "checks": checks,
        "failures": failures,
        "degradations": degradations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--execute-outcome", default="unknown")
    parser.add_argument("--publish-outcome", default="unknown")
    args = parser.parse_args()
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    result = audit(
        root,
        execute_outcome=args.execute_outcome,
        publish_outcome=args.publish_outcome,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    (root / "execution-audit.json").write_text(
        serialized,
        encoding="utf-8",
    )
    (root / "execution-diagnosis.json").write_text(
        serialized,
        encoding="utf-8",
    )
    _write_output("status", result["status"])
    _write_output(
        "reason",
        "; ".join(result["failures"] or result["degradations"]),
    )
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
