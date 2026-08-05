#!/usr/bin/env python3
"""Deterministic audit for the zero-governance price-ranked production path."""
from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from artifact_manifest import sha256_file
from v5_json_io import write_json
from v5_price_ranked_support import load_mapping, mapping_rows
from v5_provider_lock import canonical_provider_lock

RUNTIME_VERSION = "v5-price-ranked-runtime-1"
EXECUTOR_ID = "v5-native-execution-engine"


@dataclass(frozen=True)
class BudgetState:
    total: int
    recovery: int
    initial: int
    valid: bool

    def checks(self) -> dict[str, Any]:
        return {
            "approved_total_calls": self.total,
            "approved_recovery_calls": self.recovery,
            "approved_initial_calls": self.initial,
            "budget_valid": self.valid,
        }


@dataclass(frozen=True)
class CallState:
    count: int
    expert_calls: int
    governance_calls: int
    actual_cost: float


@dataclass(frozen=True)
class AuditSource:
    root: Path
    ticket: Mapping[str, Any]
    runtime: Mapping[str, Any]
    runtime_config: Mapping[str, Any]
    result: Mapping[str, Any]
    summary: Mapping[str, Any]
    graph: Mapping[str, Any]
    request_audit: Mapping[str, Any]
    ledger: Mapping[str, Any]
    selection: Mapping[str, Any]
    governance: Mapping[str, Any]
    bundle: Mapping[str, Any]
    report_manifest: Mapping[str, Any]

    @classmethod
    def from_root(cls, root: Path) -> "AuditSource":
        return cls(
            root=root,
            ticket=load_mapping(root, "ticket-status.json"),
            runtime=load_mapping(root, "production-runtime.json"),
            runtime_config=load_mapping(root, "v5-runtime-config.json"),
            result=load_mapping(root, "expert-team-result.json"),
            summary=load_mapping(root, "v5-execution-summary.json"),
            graph=load_mapping(root, "v5-execution-graph.json"),
            request_audit=load_mapping(root, "request-audit.json"),
            ledger=load_mapping(root, "call-ledger.json"),
            selection=load_mapping(root, "v5-price-ranked-selection.json"),
            governance=load_mapping(root, "v5-governance-calls.json"),
            bundle=load_mapping(root, "evidence-bundle.json"),
            report_manifest=load_mapping(
                root,
                "report-comments/report-comments-manifest.json",
            ),
        )

    @property
    def nodes(self) -> tuple[Mapping[str, Any], ...]:
        return mapping_rows(self.graph.get("nodes"))

    @property
    def final_nodes(self) -> tuple[str, ...]:
        value = self.graph.get("final_nodes")
        if not isinstance(value, list):
            return ()
        return tuple(str(item) for item in value)

    @property
    def requests(self) -> tuple[Mapping[str, Any], ...]:
        return mapping_rows(self.request_audit.get("requests"))

    @property
    def ledger_summary(self) -> Mapping[str, Any]:
        value = self.ledger.get("summary")
        if isinstance(value, Mapping):
            return value
        return {}


def _write_output(name: str, value: Any) -> None:
    target = os.getenv("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            rendered = str(value).replace("\n", " ").replace("\r", " ")
            handle.write(f"{name}={rendered}\n")


def _record(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def _budget_state(ticket: Mapping[str, Any]) -> BudgetState:
    total = int(ticket.get("calls") or 0)
    recovery = int(ticket.get("maximum_recovery_calls") or 0)
    initial = int(ticket.get("maximum_initial_calls") or 0)
    valid = (
        4 <= total <= 16
        and 0 <= recovery < total
        and initial == total - recovery
        and initial >= 3
    )
    return BudgetState(total, recovery, initial, valid)


def _call_state(source: AuditSource) -> CallState:
    summary = source.ledger_summary
    return CallState(
        count=int(summary.get("call_count") or 0),
        expert_calls=int(summary.get("expert_calls") or 0),
        governance_calls=int(summary.get("governance_calls") or 0),
        actual_cost=float(summary.get("provider_actual_cost_usd") or 0.0),
    )


def _check_outcomes(
    source: AuditSource,
    execute_outcome: str,
    publish_outcome: str,
) -> list[str]:
    failures: list[str] = []
    _record(
        failures,
        execute_outcome == "success",
        f"execution step outcome is {execute_outcome}",
    )
    _record(
        failures,
        publish_outcome == "success",
        f"report preparation outcome is {publish_outcome}",
    )
    _record(
        failures,
        source.ticket.get("accepted") is True,
        "production ticket was not accepted",
    )
    return failures


def _check_runtime(source: AuditSource) -> list[str]:
    failures: list[str] = []
    _record(
        failures,
        source.runtime.get("runtime_version") == RUNTIME_VERSION,
        "production runtime version is not price-ranked runtime v1",
    )
    _record(
        failures,
        source.result.get("runtime_version") == RUNTIME_VERSION,
        "result runtime version is not price-ranked runtime v1",
    )
    _record(
        failures,
        source.runtime.get("fallback_policy")
        == "fail-closed-no-alternate-runtime",
        "fail-closed runtime evidence is missing",
    )
    _record(
        failures,
        source.runtime.get("legacy_runtime_present") is False,
        "legacy runtime absence is not proven",
    )
    _record(
        failures,
        source.runtime.get("claude_mechanism_enabled") is False,
        "Claude mechanism is not explicitly disabled",
    )
    _record(
        failures,
        int(source.runtime.get("claude_calls") or 0) == 0,
        "runtime reports a Claude call",
    )
    _record(
        failures,
        int(source.runtime.get("governance_model_calls") or 0) == 0,
        "runtime reports governance model calls",
    )
    _record(
        failures,
        source.runtime_config.get("claude_mechanism_enabled") is False,
        "pipeline runtime config does not disable Claude",
    )
    _record(
        failures,
        int(source.governance.get("actual_governance_calls") or 0) == 0,
        "governance ledger is not zero-call",
    )
    _record(
        failures,
        int(source.governance.get("claude_red_team_calls") or 0) == 0,
        "governance ledger reports a Claude red-team call",
    )
    return failures


def _delivery_values(source: AuditSource) -> dict[str, Any]:
    integrity = source.summary.get("quality_integrity")
    integrity = dict(integrity) if isinstance(integrity, Mapping) else {}
    return {
        "status": str(
            source.summary.get("status") or source.result.get("status") or ""
        ),
        "completion": str(
            source.summary.get("completion_mode")
            or source.result.get("completion_mode")
            or ""
        ),
        "quality": str(
            source.summary.get("quality_status")
            or source.result.get("quality_status")
            or ""
        ),
        "integrity_status": integrity.get("status"),
        "answer": str(
            source.summary.get("final_answer")
            or source.result.get("final_answer")
            or ""
        ).strip(),
        "executor": str(
            source.summary.get("executor")
            or source.result.get("executor")
            or ""
        ),
    }


def _check_delivery(source: AuditSource) -> list[str]:
    values = _delivery_values(source)
    failures: list[str] = []
    _record(
        failures,
        values["status"] == "success",
        f"delivery status is {values['status'] or 'missing'}",
    )
    _record(
        failures,
        values["completion"] == "full",
        f"completion mode is {values['completion'] or 'missing'}, not full",
    )
    _record(
        failures,
        values["quality"] == "full_success",
        f"quality status is {values['quality'] or 'missing'}, not full_success",
    )
    _record(
        failures,
        values["integrity_status"] == "PASS",
        "quality integrity status is not PASS",
    )
    _record(
        failures,
        values["executor"] == EXECUTOR_ID,
        "native executor evidence is missing",
    )
    _record(
        failures,
        len(values["answer"]) >= 160,
        "final answer is missing or too short",
    )
    return failures


def _selection_checks(
    selection: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    candidate_set = mapping_rows(selection.get("cheapest_candidate_set"))
    assigned = mapping_rows(selection.get("selected_endpoints"))
    costs = tuple(
        float(row.get("estimated_call_cost_usd") or 0.0)
        for row in candidate_set
    )
    companies = tuple(str(row.get("company") or "") for row in candidate_set)
    failures: list[str] = []
    _record(
        failures,
        selection.get("status") == "PASS",
        "price-ranked selection status is not PASS",
    )
    _record(
        failures,
        selection.get("selection_authority")
        == "python-price-ranked-orchestrator",
        "selection authority is not the deterministic Python orchestrator",
    )
    _record(
        failures,
        selection.get("claude_calls") in {0, None},
        "selection audit reports a Claude call",
    )
    _record(
        failures,
        selection.get("gpt_selection_calls") in {0, None},
        "selection audit reports a GPT selection call",
    )
    _record(
        failures,
        selection.get("networkx_used_for_dag_validation") is True,
        "NetworkX DAG validation evidence is missing",
    )
    _record(
        failures,
        bool(candidate_set and assigned),
        "selected endpoint evidence is missing",
    )
    _record(
        failures,
        costs == tuple(sorted(costs)),
        "candidate set is not ordered by estimated task cost",
    )
    _record(
        failures,
        all(company and company != "unknown" for company in companies),
        "one or more selected model companies is unresolved",
    )
    _record(
        failures,
        len(companies) == len(set(companies)),
        "selected model companies are not globally distinct",
    )
    checks = {
        "candidate_count": len(candidate_set),
        "assigned_count": len(assigned),
        "candidate_costs": list(costs),
        "companies": list(companies),
    }
    return checks, failures


def _check_graph(
    source: AuditSource,
    budget: BudgetState,
    selection_checks: Mapping[str, Any],
) -> list[str]:
    failures: list[str] = []
    node_limit = min(6, budget.initial if budget.initial > 0 else 0)
    _record(
        failures,
        3 <= len(source.nodes) <= node_limit,
        "expert graph node count violates approved bounds",
    )
    _record(
        failures,
        "expert-final-synthesis" in source.final_nodes,
        "final synthesis node is missing from final_nodes",
    )
    _record(
        failures,
        int(selection_checks.get("candidate_count") or 0) == len(source.nodes),
        "cheapest selected candidate count does not match graph node count",
    )
    return failures


def _check_calls(
    source: AuditSource,
    budget: BudgetState,
    calls: CallState,
) -> list[str]:
    failures: list[str] = []
    provider_locks_valid = all(
        canonical_provider_lock(row) for row in source.requests
    )
    _record(
        failures,
        source.request_audit.get("status") == "PASS",
        "request audit status is not PASS",
    )
    _record(
        failures,
        provider_locks_valid,
        "one or more expert requests lacks an exact provider lock",
    )
    _record(
        failures,
        source.request_audit.get("external_tools_allowed") is False,
        "request audit does not prove external tools forbidden",
    )
    _record(
        failures,
        source.request_audit.get("provider_fallback_allowed") is False,
        "request audit does not prove provider fallback forbidden",
    )
    _record(
        failures,
        calls.governance_calls == 0,
        "normalized call ledger reports governance calls",
    )
    _record(
        failures,
        calls.count == calls.expert_calls == len(source.requests),
        "call ledger and complete request audit disagree",
    )
    _record(
        failures,
        calls.count <= budget.total,
        "model calls exceed the approved total ceiling",
    )
    _record(
        failures,
        math.isfinite(calls.actual_cost) and calls.actual_cost >= 0,
        "provider actual cost is invalid",
    )
    return failures


def _publication_values(source: AuditSource) -> tuple[str, tuple[Any, ...]]:
    status = str(source.report_manifest.get("publication_status") or "")
    files = source.report_manifest.get("files")
    if not isinstance(files, list):
        return status, ()
    return status, tuple(files)


def _check_publication(source: AuditSource) -> tuple[list[str], str]:
    publication_status, report_files = _publication_values(source)
    failures: list[str] = []
    _record(
        failures,
        publication_status == "prepared_full_success",
        "report publication package is not prepared_full_success",
    )
    _record(
        failures,
        bool(report_files),
        "report publication package has no comment files",
    )
    _record(
        failures,
        source.bundle.get("business_evidence_frozen") is True,
        "business evidence is not marked frozen",
    )
    _record(
        failures,
        source.bundle.get("claude_mechanism_enabled") is False,
        "evidence bundle does not prove Claude disabled",
    )
    return failures, publication_status


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


def _primary_failure(failures: list[str]) -> dict[str, Any]:
    if not failures:
        return {}
    return {
        "code": "PRICE_RANKED_AUDIT_FAILED",
        "stage": "deterministic-audit",
        "message": failures[0],
        "retryable": False,
    }


def _diagnosis(
    source: AuditSource,
    failures: list[str],
    checks: Mapping[str, Any],
    calls: CallState,
    publication_status: str,
) -> dict[str, Any]:
    unique_failures = list(dict.fromkeys(failures))
    return {
        "schema_version": "v5-price-ranked-execution-diagnosis-1",
        "status": "PASS" if not unique_failures else "FAIL",
        "runtime_version": RUNTIME_VERSION,
        "failures": unique_failures,
        "degradations": [],
        "primary_failure": _primary_failure(unique_failures),
        "checks": {
            **checks,
            "runtime_version": source.runtime.get("runtime_version"),
            "claude_mechanism_enabled": source.runtime.get(
                "claude_mechanism_enabled"
            ),
            "governance_model_calls": calls.governance_calls,
            "node_count": len(source.nodes),
            "model_calls": calls.count,
            "request_count": len(source.requests),
            "actual_cost_usd": calls.actual_cost,
            "publication_status": publication_status,
            "evidence_frozen": source.bundle.get("business_evidence_frozen"),
        },
    }


def audit(
    root: Path,
    *,
    execute_outcome: str,
    publish_outcome: str,
    require_manifest: bool = False,
) -> dict[str, Any]:
    source = AuditSource.from_root(root)
    budget = _budget_state(source.ticket)
    calls = _call_state(source)
    selection_checks, selection_failures = _selection_checks(source.selection)
    publication_failures, publication_status = _check_publication(source)
    failures = [
        *_check_outcomes(source, execute_outcome, publish_outcome),
        *([] if budget.valid else [
            "approved price-ranked budget is invalid or inconsistent"
        ]),
        *_check_runtime(source),
        *_check_delivery(source),
        *selection_failures,
        *_check_graph(source, budget, selection_checks),
        *_check_calls(source, budget, calls),
        *publication_failures,
    ]
    checks = {**budget.checks(), **selection_checks}
    if require_manifest:
        manifest_valid, manifest_failures = _manifest_files_valid(root)
        checks["artifact_manifest_valid"] = manifest_valid
        failures.extend(manifest_failures)
    diagnosis = _diagnosis(
        source,
        failures,
        checks,
        calls,
        publication_status,
    )
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
