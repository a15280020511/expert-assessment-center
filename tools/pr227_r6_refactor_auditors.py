#!/usr/bin/env python3
"""One-shot PR #227 R6 native and independent audit complexity refactor."""
from __future__ import annotations

from pathlib import Path


NATIVE_PATH = Path("open-model-market/v5_execution_auditor_integrity.py")
INDEPENDENT_PATH = Path("open-model-market/v5_independent_artifact_revalidation.py")


def replace_once(path: Path, text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replacement, found {count}")
    return text.replace(old, new, 1)


def refactor_native_auditor() -> None:
    text = NATIVE_PATH.read_text(encoding="utf-8")
    old = '''def _native_phase_contract_evidence(root: Path) -> dict[str, Any]:
    result = load_json_or_default(root / "expert-team-result.json", {})
    result = result if isinstance(result, Mapping) else {}
    graph = load_json_or_default(root / "v5-execution-graph.json", {})
    graph = graph if isinstance(graph, Mapping) else {}
    request_audit = load_json_or_default(root / "request-audit.json", {})
    request_audit = request_audit if isinstance(request_audit, Mapping) else {}
    report_manifest = load_json_or_default(
        root / "report-comments" / "report-comments-manifest.json",
        {},
    )
    report_manifest = report_manifest if isinstance(report_manifest, Mapping) else {}

    failures: list[str] = []
    history_disabled = result.get("cross_task_history_used") is False
    if not history_disabled:
        failures.append(
            "expert-team-result does not prove cross_task_history_used=false"
        )

    final_ids = {
        str(value)
        for value in graph.get("final_nodes", [])
        if str(value)
    }
    raw_nodes = graph.get("nodes", [])
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    node_contract_rows: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            failures.append("execution graph contains a non-object node")
            continue
        node_id = str(node.get("node_id") or "")
        expected = node_id in final_ids
        contract = node.get("output_contract")
        observed = (
            contract.get("final_delivery_node")
            if isinstance(contract, Mapping)
            else None
        )
        node_contract_rows.append(
            {
                "node_id": node_id,
                "expected_final_delivery_node": expected,
                "observed_final_delivery_node": observed,
            }
        )
        if observed is not expected:
            failures.append(
                "graph output_contract final_delivery_node mismatch: "
                + (node_id or "missing-node-id")
            )

    raw_requests = request_audit.get("requests", [])
    requests = raw_requests if isinstance(raw_requests, list) else []
    request_lock_rows: list[bool] = []
    for index, request in enumerate(requests, 1):
        valid = isinstance(request, Mapping) and _canonical_provider_lock(request)
        request_lock_rows.append(valid)
        if not valid:
            failures.append(
                f"request {index} provider.only/order lock is missing or inconsistent"
            )

    report_preparation_status = str(
        report_manifest.get("report_comment_preparation_status") or ""
    )
    report_preparation_mode = str(
        report_manifest.get("report_comment_preparation_mode") or ""
    )
    issue_context_required = report_manifest.get("issue_context_required")
    if (
        report_preparation_status != "PASS"
        or report_preparation_mode != "deterministic-files"
        or issue_context_required is not False
    ):
        failures.append(
            "deterministic report-comment preparation receipt is missing"
        )

    return {
        "failures": failures,
        "checks": {
            "cross_task_history_used": result.get("cross_task_history_used"),
            "cross_task_history_disabled": history_disabled,
            "final_delivery_node_contracts": node_contract_rows,
            "provider_lock_contract": "provider.only/order-exact-single-endpoint",
            "provider_lock_rows_valid": request_lock_rows,
            "legacy_provider_order_required": False,
            "report_comment_preparation_status": report_preparation_status,
            "report_comment_preparation_mode": report_preparation_mode,
            "issue_context_required": issue_context_required,
            "independent_artifact_revalidation_status": "PENDING_POST_UPLOAD",
        },
    }


'''
    new = '''def _history_isolation_evidence(
    result: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    disabled = result.get("cross_task_history_used") is False
    failures = [] if disabled else [
        "expert-team-result does not prove cross_task_history_used=false"
    ]
    return disabled, failures


def _final_delivery_contract_evidence(
    graph: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    final_ids = {
        str(value)
        for value in graph.get("final_nodes", [])
        if str(value)
    }
    raw_nodes = graph.get("nodes", [])
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            failures.append("execution graph contains a non-object node")
            continue
        node_id = str(node.get("node_id") or "")
        expected = node_id in final_ids
        contract = node.get("output_contract")
        observed = (
            contract.get("final_delivery_node")
            if isinstance(contract, Mapping)
            else None
        )
        rows.append(
            {
                "node_id": node_id,
                "expected_final_delivery_node": expected,
                "observed_final_delivery_node": observed,
            }
        )
        if observed is not expected:
            failures.append(
                "graph output_contract final_delivery_node mismatch: "
                + (node_id or "missing-node-id")
            )
    return rows, failures


def _provider_lock_evidence(
    request_audit: Mapping[str, Any],
) -> tuple[list[bool], list[str]]:
    raw_requests = request_audit.get("requests", [])
    requests = raw_requests if isinstance(raw_requests, list) else []
    rows: list[bool] = []
    failures: list[str] = []
    for index, request in enumerate(requests, 1):
        valid = isinstance(request, Mapping) and _canonical_provider_lock(request)
        rows.append(valid)
        if not valid:
            failures.append(
                f"request {index} provider.only/order lock is missing or inconsistent"
            )
    return rows, failures


def _report_preparation_evidence(
    report_manifest: Mapping[str, Any],
) -> tuple[str, str, Any, list[str]]:
    status = str(report_manifest.get("report_comment_preparation_status") or "")
    mode = str(report_manifest.get("report_comment_preparation_mode") or "")
    issue_required = report_manifest.get("issue_context_required")
    valid = (
        status == "PASS"
        and mode == "deterministic-files"
        and issue_required is False
    )
    failures = [] if valid else [
        "deterministic report-comment preparation receipt is missing"
    ]
    return status, mode, issue_required, failures


def _native_phase_contract_evidence(root: Path) -> dict[str, Any]:
    result = load_json_or_default(root / "expert-team-result.json", {})
    result = result if isinstance(result, Mapping) else {}
    graph = load_json_or_default(root / "v5-execution-graph.json", {})
    graph = graph if isinstance(graph, Mapping) else {}
    request_audit = load_json_or_default(root / "request-audit.json", {})
    request_audit = request_audit if isinstance(request_audit, Mapping) else {}
    report_manifest = load_json_or_default(
        root / "report-comments" / "report-comments-manifest.json",
        {},
    )
    report_manifest = report_manifest if isinstance(report_manifest, Mapping) else {}

    history_disabled, history_failures = _history_isolation_evidence(result)
    node_rows, node_failures = _final_delivery_contract_evidence(graph)
    request_rows, request_failures = _provider_lock_evidence(request_audit)
    report_status, report_mode, issue_required, report_failures = (
        _report_preparation_evidence(report_manifest)
    )
    return {
        "failures": [
            *history_failures,
            *node_failures,
            *request_failures,
            *report_failures,
        ],
        "checks": {
            "cross_task_history_used": result.get("cross_task_history_used"),
            "cross_task_history_disabled": history_disabled,
            "final_delivery_node_contracts": node_rows,
            "provider_lock_contract": "provider.only/order-exact-single-endpoint",
            "provider_lock_rows_valid": request_rows,
            "legacy_provider_order_required": False,
            "report_comment_preparation_status": report_status,
            "report_comment_preparation_mode": report_mode,
            "issue_context_required": issue_required,
            "independent_artifact_revalidation_status": "PENDING_POST_UPLOAD",
        },
    }


'''
    text = replace_once(NATIVE_PATH, text, old, new)
    NATIVE_PATH.write_text(text, encoding="utf-8")


def refactor_independent_auditor() -> None:
    text = INDEPENDENT_PATH.read_text(encoding="utf-8")
    text = replace_once(
        INDEPENDENT_PATH,
        text,
        "def _final_contract_violations(\n",
        '''def _final_delivery_flag_violations(
    nodes: list[Any],
    final_ids: set[str],
) -> list[str]:
    violations: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("node_id") or "")
        expected_final = node_id in final_ids
        contract = node.get("output_contract")
        observed_final = (
            contract.get("final_delivery_node")
            if isinstance(contract, Mapping)
            else None
        )
        if observed_final is not expected_final:
            violations.append(
                "node final_delivery_node flag is missing or inconsistent: "
                + (node_id or "missing-node-id")
            )
    return violations


def _final_contract_violations(
''',
    )
    old = '''    violations: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("node_id") or "")
        expected_final = node_id in final_ids
        contract = node.get("output_contract")
        observed_final = (
            contract.get("final_delivery_node")
            if isinstance(contract, Mapping)
            else None
        )
        if observed_final is not expected_final:
            violations.append(
                "node final_delivery_node flag is missing or inconsistent: "
                + (node_id or "missing-node-id")
            )

    task_contract = _recompiled_task_contract(task)
'''
    new = '''    violations = _final_delivery_flag_violations(nodes, final_ids)
    task_contract = _recompiled_task_contract(task)
'''
    text = replace_once(INDEPENDENT_PATH, text, old, new)
    INDEPENDENT_PATH.write_text(text, encoding="utf-8")


def main() -> int:
    refactor_native_auditor()
    refactor_independent_auditor()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
