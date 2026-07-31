"""Enforce consistency between node quality gates and run-level status.

A usable truncated answer may be delivered as bounded degradation, but it must
never be represented as ``full_success``. This wrapper runs after the R8
fault-aware executor, inspects every node result, corrects the run-level quality
state, and rewrites the execution summary and request-audit semantics.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

import v5_executor as executor

STRICT_SUCCESS_STATUSES = {
    "success",
    "success_retried",
    "success_recovered",
}
DEGRADED_SUCCESS_STATUSES = {"success_degraded"}

_INSTALLED = False
_ORIGINAL_EXECUTE: Any = None


def _attempt_quality_failures(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    attempts = row.get("attempts", [])
    if not isinstance(attempts, list):
        return failures
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            continue
        status = str(attempt.get("status") or "")
        reasons = attempt.get("gate_reasons", [])
        reasons = [str(value) for value in reasons] if isinstance(reasons, list) else []
        if status == "quality_gate_failed" or reasons:
            failures.append(
                {
                    "attempt_index": int(attempt.get("attempt_index") or 0),
                    "status": status,
                    "gate_reasons": reasons,
                    "quality_score": float(attempt.get("quality_score") or 0.0),
                }
            )
    return failures


def enforce_result_integrity(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return a corrected mutable copy of a V5 execution result."""
    normalized = dict(result)
    rows = normalized.get("node_results", [])
    rows = rows if isinstance(rows, list) else []
    degraded_nodes: list[dict[str, Any]] = []
    strict_nodes: list[str] = []
    failed_nodes: list[str] = []

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        node_id = str(row.get("node_id") or "")
        status = str(row.get("status") or "")
        attempt_failures = _attempt_quality_failures(row)
        if status in STRICT_SUCCESS_STATUSES:
            strict_nodes.append(node_id)
        elif status in DEGRADED_SUCCESS_STATUSES or status.startswith("success"):
            degraded_nodes.append(
                {
                    "node_id": node_id,
                    "status": status,
                    "quality_score": float(row.get("quality_score") or 0.0),
                    "attempt_quality_failures": attempt_failures,
                }
            )
        else:
            failed_nodes.append(node_id)

    all_nodes_strict = bool(rows) and len(strict_nodes) == len(rows)
    if degraded_nodes:
        normalized["completion_mode"] = "degraded"
        normalized["quality_status"] = "degraded_success"
        normalized["stop_reason"] = "usable-output-with-node-quality-gate-failure"
        degradation = dict(normalized.get("degradation") or {})
        degradation.update(
            {
                "used": True,
                "mode": "usable-node-output-after-quality-gate-failure",
                "extra_model_calls": int(degradation.get("extra_model_calls") or 0),
                "degraded_node_ids": [row["node_id"] for row in degraded_nodes],
            }
        )
        normalized["degradation"] = degradation
    elif all_nodes_strict and normalized.get("status") == "success":
        normalized["completion_mode"] = "full"
        normalized["quality_status"] = "full_success"

    normalized["quality_integrity"] = {
        "status": (
            "DEGRADED"
            if degraded_nodes
            else "FAIL"
            if failed_nodes
            else "PASS"
            if all_nodes_strict
            else "UNKNOWN"
        ),
        "strict_success_statuses": sorted(STRICT_SUCCESS_STATUSES),
        "strict_node_ids": strict_nodes,
        "degraded_nodes": degraded_nodes,
        "failed_node_ids": failed_nodes,
        "full_success_allowed": all_nodes_strict and not degraded_nodes and not failed_nodes,
    }
    return normalized


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _rewrite_artifacts(root: Path, result: Mapping[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _write_json(
        root / "v5-execution-summary.json",
        {key: value for key, value in result.items() if key != "node_results"},
    )

    audit_path = root / "v5-request-audit.json"
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        audit = {}
    if isinstance(audit, Mapping):
        audit = dict(audit)
        requests = audit.get("requests", [])
        requests = requests if isinstance(requests, list) else []
        dynamic_allowance = any(
            isinstance(request, Mapping)
            and (
                "max_tokens" in request
                or "max_completion_tokens" in request
            )
            for request in requests
        )
        audit["bounded_output_allowance_sent"] = dynamic_allowance
        audit["dynamic_output_allowance_sent"] = dynamic_allowance
        audit["artificial_token_ceiling_sent"] = False
        audit["output_allowance_policy"] = (
            "dynamic-reasoning-aware-truncation-protection-not-billed-assumption"
            if dynamic_allowance
            else "provider-default-no-explicit-allowance"
        )
        integrity = result.get("quality_integrity", {})
        audit["quality_integrity_status"] = (
            integrity.get("status") if isinstance(integrity, Mapping) else "UNKNOWN"
        )
        _write_json(audit_path, audit)


def integrity_execute_v5_graph(*args: Any, **kwargs: Any) -> dict[str, Any]:
    if _ORIGINAL_EXECUTE is None:
        raise RuntimeError("v5_quality_status_integrity.install() has not been called")
    result = _ORIGINAL_EXECUTE(*args, **kwargs)
    normalized = enforce_result_integrity(result)
    output_dir = kwargs.get("output_dir")
    if output_dir is not None:
        _rewrite_artifacts(Path(output_dir), normalized)
    return normalized


def _patch_loaded_callers() -> None:
    for module_name in (
        "v5_pipeline",
        "v5_live_benchmark",
        "v5_live_benchmark_hardened",
        "v5_live_benchmark_economy",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "execute_v5_graph"):
            setattr(module, "execute_v5_graph", integrity_execute_v5_graph)


def install() -> None:
    global _INSTALLED
    global _ORIGINAL_EXECUTE
    if not _INSTALLED:
        _ORIGINAL_EXECUTE = executor.execute_v5_graph
        executor.execute_v5_graph = integrity_execute_v5_graph
        _INSTALLED = True
    _patch_loaded_callers()
