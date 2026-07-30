"""Wire GraphLimits delivery policy into the compatible resilient executor."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

import v5_executor as executor
import v5_resilient_executor as legacy
from execution_graph import ExecutionGraph, GraphLimits

_INSTALLED = False
_LOCK = Lock()
_ORIGINAL_EXECUTE = legacy.resilient_execute_v5_graph
_ORIGINAL_CONTENT_WORK_IDS = legacy._content_work_ids


def _content_work_ids(graph: ExecutionGraph) -> set[str]:
    content = _ORIGINAL_CONTENT_WORK_IDS(graph)
    metadata = graph.metadata if isinstance(graph.metadata, Mapping) else {}
    optional = {str(value) for value in metadata.get("optional_work_ids", [])}
    return content - optional or content


def _write_failed_artifacts(
    output_dir: str | Path | None,
    result: Mapping[str, Any],
) -> None:
    if output_dir is None:
        return
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    summary = {key: value for key, value in result.items() if key != "node_results"}
    executor._write_json(root / "v5-execution-summary.json", summary)
    (root / "v5-final-report.md").write_text(
        "# V5 execution failed production delivery policy\n",
        encoding="utf-8",
    )
    audit_path = root / "v5-request-audit.json"
    if audit_path.exists():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            audit = {}
        audit["delivery_policy_status"] = "FAIL"
        audit["delivery_policy_blockers"] = list(result.get("delivery_policy_blockers", []))
        executor._write_json(audit_path, audit)


def execute_with_graph_limits(
    graph: ExecutionGraph | Mapping[str, Any],
    run: Any,
    original_task: str,
    *,
    call_fn: Any | None = None,
    output_dir: str | Path | None = None,
    limits: GraphLimits | None = None,
) -> dict[str, Any]:
    graph = graph if isinstance(graph, ExecutionGraph) else ExecutionGraph.from_mapping(graph)
    limits = limits or GraphLimits()
    threshold = max(0.0, min(1.0, float(limits.min_required_work_coverage)))

    # The compatible executor stores the threshold as a module constant. Serialize
    # this narrow compatibility section so concurrent graphs cannot leak policy.
    with _LOCK:
        old_threshold = legacy.MIN_DEGRADED_WORK_COVERAGE
        old_content = legacy._content_work_ids
        legacy.MIN_DEGRADED_WORK_COVERAGE = threshold
        legacy._content_work_ids = _content_work_ids
        try:
            result = _ORIGINAL_EXECUTE(
                graph,
                run,
                original_task,
                call_fn=call_fn,
                output_dir=output_dir,
                limits=limits,
            )
        finally:
            legacy._content_work_ids = old_content
            legacy.MIN_DEGRADED_WORK_COVERAGE = old_threshold

    metadata = graph.metadata if isinstance(graph.metadata, Mapping) else {}
    non_degradable = {str(value) for value in metadata.get("non_degradable_work_ids", [])}
    missing = {
        str(value)
        for value in result.get("work_coverage", {}).get("missing_work_ids", [])
    }
    successful_content_nodes = {
        str(row.get("node_id"))
        for row in result.get("node_results", [])
        if str(row.get("status", "")).startswith("success")
        and row.get("answer")
        and "synthesis" not in {
            str(value) for value in row.get("functions", [])
        }
    }
    # Older serialized node results do not include functions. Count successful
    # non-final nodes deterministically in that compatibility case.
    if not successful_content_nodes:
        final_ids = set(graph.final_nodes)
        successful_content_nodes = {
            str(row.get("node_id"))
            for row in result.get("node_results", [])
            if str(row.get("status", "")).startswith("success")
            and row.get("answer")
            and str(row.get("node_id")) not in final_ids
        }
        if not successful_content_nodes and result.get("work_coverage", {}).get("covered_work_ids"):
            successful_content_nodes = {"covered-content"}

    blockers: list[str] = []
    if result.get("completion_mode") == "degraded" and not limits.allow_degraded_success:
        blockers.append("degraded-success-disabled")
    if len(successful_content_nodes) < max(1, int(limits.min_successful_content_nodes)):
        blockers.append("insufficient-successful-content-nodes")
    missing_non_degradable = sorted(non_degradable & missing)
    if missing_non_degradable:
        blockers.append("missing-non-degradable-work:" + ",".join(missing_non_degradable))

    result["delivery_policy"] = {
        "minimum_required_work_coverage": threshold,
        "minimum_successful_content_nodes": max(1, int(limits.min_successful_content_nodes)),
        "successful_content_node_count": len(successful_content_nodes),
        "optional_work_ids": sorted({str(value) for value in metadata.get("optional_work_ids", [])}),
        "non_degradable_work_ids": sorted(non_degradable),
        "allow_degraded_success": bool(limits.allow_degraded_success),
    }
    if blockers:
        result["status"] = "failed"
        result["quality_status"] = "failed"
        result["delivery_policy_blockers"] = blockers
        result["stop_reason"] = "production-delivery-policy-rejected"
        result["final_answer"] = None
        _write_failed_artifacts(output_dir, result)
        raise executor.V5ExecutionError(
            "V5 result rejected by production delivery policy: " + ", ".join(blockers)
        )

    if output_dir is not None:
        root = Path(output_dir)
        executor._write_json(
            root / "v5-execution-summary.json",
            {key: value for key, value in result.items() if key != "node_results"},
        )
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    legacy.resilient_execute_v5_graph = execute_with_graph_limits
    _INSTALLED = True
