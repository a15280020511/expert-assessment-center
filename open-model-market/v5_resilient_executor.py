"""Resilient V5 DAG execution with deterministic partial-success synthesis."""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_executor as executor
from execution_graph import ExecutionGraph, GraphLimits
from execution_graph_validator import validate_execution_graph

MIN_DEGRADED_WORK_COVERAGE = 2.0 / 3.0
_INSTALLED = False


def _content_work_ids(graph: ExecutionGraph) -> set[str]:
    synthesis = {
        work_id
        for node in graph.nodes
        if "synthesis" in node.functions
        for work_id in node.assigned_work
    }
    content = set(graph.required_work) - synthesis
    return content or set(graph.required_work)


def _best_outputs_by_work(
    graph: ExecutionGraph,
    outputs: Mapping[str, executor.NodeExecutionResult],
) -> dict[str, executor.NodeExecutionResult]:
    best: dict[str, executor.NodeExecutionResult] = {}
    content = _content_work_ids(graph)
    for result in outputs.values():
        if not result.status.startswith("success") or not result.answer:
            continue
        for work_id in result.assigned_work:
            if work_id not in content:
                continue
            previous = best.get(work_id)
            if previous is None or result.quality_score > previous.quality_score:
                best[work_id] = result
    return best


def _degraded_synthesis(
    best_by_work: Mapping[str, executor.NodeExecutionResult],
    missing_work: Sequence[str],
) -> str:
    sections = [
        "# V5降级合成结果",
        "",
        "本结果由已通过质量门的节点确定性合成；未调用额外模型。",
    ]
    if missing_work:
        sections.extend(["", "## 未覆盖工作", "、".join(sorted(missing_work))])
    for index, (work_id, result) in enumerate(sorted(best_by_work.items()), 1):
        sections.extend(["", f"## {index}. {work_id}", result.answer or ""])
    return "\n".join(sections).strip()


def _write_artifacts(
    root: Path,
    result: Mapping[str, Any],
    outputs: Mapping[str, executor.NodeExecutionResult],
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    executor._write_json(root / "v5-node-results.json", result["node_results"])
    executor._write_json(
        root / "v5-execution-summary.json",
        {key: value for key, value in result.items() if key != "node_results"},
    )
    requests = [attempt.request for row in outputs.values() for attempt in row.attempts]
    executor._write_json(root / "v5-request-audit.json", {
        "status": "PASS" if all(
            not executor.FORBIDDEN_FIELDS.intersection(request)
            for request in requests
        ) else "FAIL",
        "request_count": len(requests),
        "requests": requests,
        "artificial_token_ceiling_sent": any(
            "max_tokens" in request or "max_completion_tokens" in request
            for request in requests
        ),
        "external_tools_allowed": False,
        "global_limits": result["execution_budget"],
        "degraded_synthesis_is_deterministic": bool(
            result.get("degradation", {}).get("used")
        ),
    })
    (root / "v5-final-report.md").write_text(
        str(result.get("final_answer") or "# V5 execution failed\n"),
        encoding="utf-8",
    )


def resilient_execute_v5_graph(
    graph: ExecutionGraph | Mapping[str, Any],
    run: Any,
    original_task: str,
    *,
    call_fn: Any | None = None,
    output_dir: str | Path | None = None,
    limits: GraphLimits | None = None,
) -> dict[str, Any]:
    """Continue after isolated failures and return an audited usable answer when possible."""
    graph = graph if isinstance(graph, ExecutionGraph) else ExecutionGraph.from_mapping(graph)
    limits = limits or GraphLimits()
    issues = validate_execution_graph(graph, limits)
    structural_issues = [item for item in issues if item.code != "budget_limit"]
    if structural_issues:
        raise executor.V5ExecutionError(
            "Invalid execution graph: "
            + "; ".join(f"{item.code}:{item.message}" for item in structural_issues)
        )

    planned_cost = round(sum(node.estimated_cost for node in graph.nodes), 8)
    preflight = {
        "estimated_initial_cost_usd": planned_cost,
        "max_budget_usd": limits.max_budget_usd,
        "status": "pass",
        "policy": "reasoning-inclusive-risk-reserved-before-first-call",
    }
    if limits.max_budget_usd is not None and planned_cost > limits.max_budget_usd + 1e-12:
        preflight["status"] = "rejected"
        result = {
            "version": 5,
            "status": "failed",
            "completion_mode": "none",
            "quality_status": "failed",
            "execution_stages": [],
            "node_results": [],
            "final_node_ids": list(graph.final_nodes),
            "final_answer": None,
            "actual_cost_usd": 0.0,
            "recovery_used": False,
            "execution_budget": {
                "max_budget_usd": limits.max_budget_usd,
                "actual_cost_usd": 0.0,
                "calls_reserved": 0,
                "denials": [{"reason": "graph-cost-preflight-rejected"}],
            },
            "cost_preflight": preflight,
            "stop_reason": "graph-cost-preflight-rejected",
        }
        if output_dir is not None:
            _write_artifacts(Path(output_dir), result, {})
        raise executor.V5ExecutionError(
            "V5 graph rejected before model calls because conservative estimated cost exceeds budget."
        )

    call = call_fn or executor._default_call
    node_by_id = {node.node_id: node for node in graph.nodes}
    incoming: dict[str, list[str]] = {node.node_id: [] for node in graph.nodes}
    for edge in graph.edges:
        incoming[edge.target].append(edge.source)

    budget = executor.ExecutionBudget(
        max_planned_calls=limits.max_model_calls,
        max_retries=limits.max_retries,
        max_replacements=limits.max_replacements,
        max_budget_usd=limits.max_budget_usd,
    )
    outputs: dict[str, executor.NodeExecutionResult] = {}
    recovery = graph.metadata.get("recovery_pool", {}) if isinstance(graph.metadata, Mapping) else {}
    stage_records: list[dict[str, Any]] = []

    for stage_index, stage in enumerate(graph.execution_stages):
        configured = max(1, int(getattr(run, "parallel_workers", len(stage) or 1)))
        # Reconcile actual spend after every call when a hard budget exists.
        workers = 1 if limits.max_budget_usd is not None else min(configured, len(stage))
        futures = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for node_id in stage:
                upstream = [
                    {
                        "node_id": source,
                        "answer": outputs[source].answer,
                        "quality_score": outputs[source].quality_score,
                    }
                    for source in incoming[node_id]
                    if source in outputs and outputs[source].answer
                ]
                futures[pool.submit(
                    executor._execute_node,
                    node_by_id[node_id],
                    original_task,
                    upstream,
                    run,
                    call,
                    list(recovery.get(node_id, [])) if isinstance(recovery, Mapping) else [],
                    budget,
                )] = node_id
            stage_results = [future.result() for future in as_completed(futures)]

        stage_results.sort(key=lambda row: row.node_id)
        for item in stage_results:
            outputs[item.node_id] = item
        failed = [
            item.node_id
            for item in stage_results
            if not item.status.startswith("success")
        ]
        stage_records.append({
            "stage_index": stage_index,
            "node_ids": list(stage),
            "failed_node_ids": failed,
            "status": "degraded" if failed else "success",
            "continued_after_failure": bool(failed),
        })
        snapshot = budget.snapshot()
        if (
            limits.max_budget_usd is not None
            and snapshot["actual_cost_usd"] >= limits.max_budget_usd - 1e-12
        ):
            break

    successful_finals = [
        outputs[node_id]
        for node_id in graph.final_nodes
        if node_id in outputs
        and outputs[node_id].status.startswith("success")
        and outputs[node_id].answer
    ]
    preferred_final = "\n\n".join(item.answer or "" for item in successful_finals).strip()
    content_work = _content_work_ids(graph)
    best_by_work = _best_outputs_by_work(graph, outputs)
    covered = set(best_by_work)
    missing = sorted(content_work - covered)
    coverage = len(covered) / max(1, len(content_work))
    complete_nodes = (
        len(outputs) == len(graph.nodes)
        and all(item.status.startswith("success") for item in outputs.values())
    )

    degradation_used = False
    final_answer = preferred_final
    if not final_answer and coverage >= MIN_DEGRADED_WORK_COVERAGE:
        final_answer = _degraded_synthesis(best_by_work, missing)
        degradation_used = True
    elif preferred_final and (missing or not complete_nodes):
        degradation_used = True

    if final_answer and not degradation_used and complete_nodes and not missing:
        status = "success"
        completion_mode = "full"
        quality_status = "full_success"
        stop_reason = "all-quality-gates-passed"
    elif final_answer and coverage >= MIN_DEGRADED_WORK_COVERAGE:
        # A usable answer is success; degradation stays explicit for quality gates.
        status = "success"
        completion_mode = "degraded"
        quality_status = "degraded_success"
        stop_reason = "partial-success-deterministic-synthesis"
    else:
        status = "failed"
        completion_mode = "none"
        quality_status = "failed"
        stop_reason = "insufficient-work-coverage-after-recovery"

    budget_snapshot = budget.snapshot()
    result = {
        "version": 5,
        "status": status,
        "completion_mode": completion_mode,
        "quality_status": quality_status,
        "execution_stages": stage_records,
        "node_results": [asdict(outputs[node_id]) for node_id in sorted(outputs)],
        "final_node_ids": list(graph.final_nodes),
        "final_answer": final_answer or None,
        "actual_cost_usd": round(sum(item.actual_cost_usd for item in outputs.values()), 8),
        "recovery_used": any(
            attempt.replacement or attempt.retry
            for item in outputs.values()
            for attempt in item.attempts
        ),
        "execution_budget": budget_snapshot,
        "cost_preflight": preflight,
        "work_coverage": {
            "required_content_work_ids": sorted(content_work),
            "covered_work_ids": sorted(covered),
            "missing_work_ids": missing,
            "coverage_ratio": round(coverage, 6),
            "minimum_degraded_coverage": MIN_DEGRADED_WORK_COVERAGE,
        },
        "degradation": {
            "used": degradation_used,
            "mode": "deterministic-successful-node-synthesis" if degradation_used else None,
            "extra_model_calls": 0,
        },
        "stop_reason": stop_reason,
    }
    if output_dir is not None:
        _write_artifacts(Path(output_dir), result, outputs)
    if status == "failed":
        raise executor.V5ExecutionError(
            "V5 execution could not reach the minimum audited work-coverage gate."
        )
    return result


def _patch_loaded_callers() -> None:
    for module_name in (
        "v5_pipeline",
        "v5_live_benchmark",
        "v5_live_benchmark_hardened",
        "v5_live_benchmark_economy",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "execute_v5_graph"):
            setattr(module, "execute_v5_graph", resilient_execute_v5_graph)


def install() -> None:
    global _INSTALLED
    if not _INSTALLED:
        executor.execute_v5_graph = resilient_execute_v5_graph
        _INSTALLED = True
    _patch_loaded_callers()
