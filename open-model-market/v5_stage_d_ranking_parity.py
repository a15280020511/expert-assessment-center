"""Execute the exact V5 graph approved by the Stage-D zero-call gate.

Stage-D used to run a second live catalog/endpoint compilation after the
zero-call gate. Even with the same nominal ranking breadth, endpoint inventory,
capability scores, Pareto pruning, and CP-SAT choices could diverge between the
check and the paid execution. That is a time-of-check/time-of-use defect.

This adapter reconstructs the graph exclusively from the frozen resources and
raw endpoint market written by the immediately preceding zero-call gate,
verifies the selected candidate IDs and runtime preflight against the gate
report, and only then permits model calls. It performs no catalog or endpoint
fetches and never substitutes a newly planned graph.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

import model_market as market
import v5_candidate_diversity
import v5_economy_zero_call_diagnostic as diagnostic
import v5_executor
import v5_live_benchmark as base
import v5_planner
import v5_r8_executor as runtime
import v5_value_optimizer
from execution_graph import ExecutionGraph
from v5_executor import V5ExecutionError

_INSTALLED = False
EXPECTED_GATE = "v5-r8-stage-d-exact-runtime-zero-call-preflight"
EXPECTED_COST_POLICY = "reasoning-inclusive-p95-usage-not-max-allowance-r8"


class FrozenGraphEvidenceError(base.LiveBenchmarkError):
    """Raised before inference when frozen Stage-D evidence is absent or drifts."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FrozenGraphEvidenceError(f"cannot read frozen evidence: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FrozenGraphEvidenceError(f"frozen evidence must be a JSON object: {path}")
    return value


def _stage_d_root(strategy_root: Path) -> Path:
    """Return the benchmark output root from tasks/<task>/v5_joint_graph."""
    resolved = Path(strategy_root)
    if len(resolved.parents) < 3 or resolved.name != "v5_joint_graph":
        raise FrozenGraphEvidenceError(
            f"unexpected Stage-D strategy output path: {resolved}"
        )
    return resolved.parents[2]


def _resource_bundle(task_root: Path) -> dict[str, Any]:
    return {
        "version": 5,
        "phase_a_complete": True,
        "model_market_accessed": False,
        "task_semantics": _load_object(task_root / "task-interpretations.json"),
        "atomic_work_graphs": _load_object(task_root / "atomic-work-graph.json"),
        "resource_matrices": _load_object(task_root / "task-resource-matrix.json"),
    }


def _strict_price_tier() -> Mapping[str, Any]:
    for tier in diagnostic.PRICE_TIERS:
        if tier.get("name") == "strict-economy":
            return tier
    raise FrozenGraphEvidenceError("strict-economy price tier is not defined")


def _approved_task(gate: Mapping[str, Any], task_id: str) -> Mapping[str, Any]:
    if gate.get("gate") != EXPECTED_GATE:
        raise FrozenGraphEvidenceError("unexpected zero-call gate identity")
    if gate.get("status") != "passed" or gate.get("paid_inference_allowed") is not True:
        raise FrozenGraphEvidenceError("zero-call gate did not authorize paid inference")
    if int(gate.get("model_inference_calls", -1)) != 0:
        raise FrozenGraphEvidenceError("zero-call gate itself made model calls")
    for row in gate.get("tasks", []):
        if isinstance(row, Mapping) and str(row.get("task_id")) == task_id:
            if row.get("passed") is not True or row.get("blockers"):
                raise FrozenGraphEvidenceError(f"task was not approved by zero-call gate: {task_id}")
            return row
    raise FrozenGraphEvidenceError(f"task is absent from zero-call gate: {task_id}")


def _frozen_plan(
    task_id: str,
    strategy_root: Path,
    strategy_cap: float,
) -> tuple[ExecutionGraph, Mapping[str, Any], Mapping[str, Any]]:
    output_root = _stage_d_root(strategy_root)
    preflight_root = output_root / "zero-call-preflight"
    task_root = preflight_root / "tasks" / task_id
    gate = _load_object(preflight_root / "r8-joint-gate.json")
    approval = _approved_task(gate, task_id)
    exact = approval.get("exact_runtime_preflight")
    exact = exact if isinstance(exact, Mapping) else {}
    approved_ids = {str(value) for value in exact.get("selected_candidate_ids", [])}
    if not approved_ids:
        raise FrozenGraphEvidenceError("zero-call gate has no approved candidate IDs")
    if float(gate.get("max_estimated_v5_cost_per_task_usd", 0.0) or 0.0) > strategy_cap + 1e-12:
        raise FrozenGraphEvidenceError("zero-call gate budget exceeds paid strategy cap")

    resources = _resource_bundle(task_root)
    raw_market = _load_object(task_root / "raw-model-endpoint-market.json")
    frozen_market = diagnostic._filter_market(raw_market, _strict_price_tier())
    v5_candidate_diversity.install()
    candidates = v5_planner.generate_candidate_graph(
        resources,
        frozen_market,
        maximum_per_group=12,
    )
    limits = base.GraphLimits(
        max_nodes=16,
        max_edges=64,
        max_stages=8,
        max_model_calls=16,
        max_retries=1,
        max_replacements=2,
        max_budget_usd=strategy_cap,
    )
    optimization = v5_value_optimizer.optimize_execution_graph(
        candidates,
        limits=limits,
        solver_timeout_seconds=20.0,
    )
    selected_ids = {str(value) for value in optimization.get("selected_candidate_ids", [])}
    if selected_ids != approved_ids:
        raise FrozenGraphEvidenceError(
            "frozen graph candidate IDs differ from zero-call approval: "
            f"approved={sorted(approved_ids)} rebuilt={sorted(selected_ids)}"
        )
    graph = ExecutionGraph.from_mapping(optimization["execution_graph"])
    adjusted, preflight = runtime._preflight(graph, limits)
    blockers = [str(value) for value in preflight.get("blockers", [])]
    if blockers:
        raise FrozenGraphEvidenceError(
            "frozen graph no longer passes runtime preflight: " + "; ".join(blockers)
        )
    expected_count = int(exact.get("selected_node_count", 0) or 0)
    if len(adjusted.nodes) != expected_count:
        raise FrozenGraphEvidenceError(
            f"frozen graph node count drift: approved={expected_count} rebuilt={len(adjusted.nodes)}"
        )
    approved_endpoints = {str(value) for value in exact.get("selected_provider_endpoints", [])}
    rebuilt_endpoints = {node.provider_endpoint for node in adjusted.nodes}
    if approved_endpoints and rebuilt_endpoints != approved_endpoints:
        raise FrozenGraphEvidenceError(
            "frozen graph Provider endpoints differ from zero-call runtime approval"
        )
    if float(adjusted.estimated_total_cost) > strategy_cap + 1e-12:
        raise FrozenGraphEvidenceError("frozen graph exceeds paid strategy cap")

    evidence = {
        "version": 1,
        "policy": "execute-exact-zero-call-approved-graph-no-live-replanning",
        "task_id": task_id,
        "gate": EXPECTED_GATE,
        "approved_candidate_ids": sorted(approved_ids),
        "rebuilt_candidate_ids": sorted(selected_ids),
        "approved_node_count": expected_count,
        "executed_node_count": len(adjusted.nodes),
        "approved_provider_endpoints": sorted(approved_endpoints),
        "executed_provider_endpoints": sorted(rebuilt_endpoints),
        "estimated_total_cost_usd": adjusted.estimated_total_cost,
        "strategy_cap_usd": strategy_cap,
        "runtime_preflight": dict(preflight),
        "model_inference_calls_before_approval": 0,
        "live_catalog_refetched": False,
        "live_endpoints_refetched": False,
        "cost_estimation_policy": EXPECTED_COST_POLICY,
        "passed": True,
    }
    return adjusted, frozen_market, {
        "candidate_graph": candidates,
        "optimization": optimization,
        "evidence": evidence,
    }


def production_parity_v5_strategy(
    task: Mapping[str, Any],
    root: Path,
    ledger: base.GlobalLedger,
    models: Mapping[str, Any],
    endpoint_cache: dict[str, Mapping[str, Any]],
    strategy_cap: float,
) -> tuple[base.StrategyOutcome, Mapping[str, Any]]:
    """Execute only the graph approved by the immediately preceding zero-call gate."""
    del models, endpoint_cache
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    task_id = str(task["task_id"])
    started = time.monotonic()
    run = market.build_run_config(
        base._namespace(
            base._task_text(task),
            root,
            ranking_limit=50,
            max_cost_usd=strategy_cap,
        )
    )
    graph, frozen_market, planner = _frozen_plan(task_id, root, strategy_cap)
    base._write_json(root / "v5-model-endpoint-market.json", frozen_market)
    base._write_json(root / "v5-candidate-graph.json", planner["candidate_graph"])
    base._write_json(root / "v5-optimization.json", planner["optimization"])
    base._write_json(root / "v5-execution-graph.json", graph.to_dict())
    base._write_json(root / "v5-stage-d-frozen-graph-evidence.json", planner["evidence"])

    result: Mapping[str, Any] = {}
    error = ""
    try:
        result = v5_executor.execute_v5_graph(
            graph,
            run,
            run.task,
            output_dir=root,
            limits=base.GraphLimits(
                max_nodes=16,
                max_edges=64,
                max_stages=8,
                max_model_calls=16,
                max_retries=1,
                max_replacements=2,
                max_budget_usd=strategy_cap,
            ),
        )
    except V5ExecutionError as exc:
        error = str(exc)
        summary = root / "v5-execution-summary.json"
        if summary.exists():
            loaded = json.loads(summary.read_text(encoding="utf-8"))
            result = loaded if isinstance(loaded, Mapping) else {}
    cost = float(result.get("actual_cost_usd", 0.0) or 0.0)
    budget = (
        result.get("execution_budget")
        if isinstance(result.get("execution_budget"), Mapping)
        else {}
    )
    calls = int(budget.get("calls_reserved", 0) or 0)
    ledger.add_external(
        task_id=task_id,
        strategy="v5_joint_graph",
        calls=calls,
        cost_usd=cost,
    )
    answer = str(result.get("final_answer") or "").strip()
    audit_path = root / "v5-request-audit.json"
    audit = base._load_json(audit_path) if audit_path.exists() else {}
    success = result.get("status") == "success" and len(answer) >= 160
    outcome = base.StrategyOutcome(
        task_id=task_id,
        strategy="v5_joint_graph",
        status="success" if success else "failed",
        answer=answer or None,
        actual_cost_usd=round(cost, 8),
        latency_seconds=round(time.monotonic() - started, 6),
        call_count=calls,
        models=sorted({node.model for node in graph.nodes}),
        providers=sorted({node.provider_endpoint for node in graph.nodes}),
        safety_failure=bool(audit and audit.get("status") != "PASS"),
        error=None if success else (error or "V5 execution failed"),
        artifacts={
            "execution_budget": dict(budget),
            "request_audit": dict(audit),
            "frozen_graph_evidence": dict(planner["evidence"]),
            "ranking_parity_verified": True,
            "zero_call_graph_reused": True,
        },
    )
    return outcome, frozen_market


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    base._v5_strategy = production_parity_v5_strategy
    _INSTALLED = True
