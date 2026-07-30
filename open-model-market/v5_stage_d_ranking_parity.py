"""Make Stage-D V5 planning use the same model breadth as production.

The zero-call gate and production pipeline evaluate the configured ranking
limit (normally 50), while the legacy benchmark helper silently truncated the
live endpoint market to 24 models. That created a time-of-check/time-of-use
mismatch: a graph could pass exact zero-call reconstruction and then become
infeasible in the paid benchmark. This module replaces only the benchmark V5
strategy; the V5 planner/executor remain unchanged.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

import model_market as market
import v5_live_benchmark as base
from execution_graph import ExecutionGraph
from resource_matrix import compile_v5_task_resources
from task_resource_artifacts import write_task_resource_artifacts
from v5_executor import V5ExecutionError, execute_v5_graph
from v5_pipeline import _rank_v5_models
from v5_planner import compile_and_optimize_v5, fetch_live_endpoint_payloads

_INSTALLED = False


def production_parity_v5_strategy(
    task: Mapping[str, Any],
    root: Path,
    ledger: base.GlobalLedger,
    models: Mapping[str, Any],
    endpoint_cache: dict[str, Mapping[str, Any]],
    strategy_cap: float,
) -> tuple[base.StrategyOutcome, Mapping[str, Any]]:
    """Plan and execute V5 with the configured production ranking limit."""
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
    profile = market.classify_task(run.task, run)
    ranked = _rank_v5_models(models, profile, run)
    candidate_model_limit = max(1, min(len(ranked), int(run.ranking_limit)))
    considered = ranked[:candidate_model_limit]
    missing = [row for row in considered if row.id not in endpoint_cache]
    if missing:
        endpoint_cache.update(
            fetch_live_endpoint_payloads(
                missing,
                run,
                maximum_models=candidate_model_limit,
            )
        )
    payloads = {row.id: endpoint_cache.get(row.id, {}) for row in considered}
    resources = compile_v5_task_resources(profile, run)
    write_task_resource_artifacts(resources, root)
    limits = base.GraphLimits(
        max_nodes=16,
        max_edges=64,
        max_stages=8,
        max_model_calls=16,
        max_retries=1,
        max_replacements=2,
        max_budget_usd=strategy_cap,
    )
    planner = compile_and_optimize_v5(
        ranked,
        resources,
        endpoint_payloads=payloads,
        allow_synthetic_fixture=False,
        ranking_limit=candidate_model_limit,
        limits=limits,
        maximum_per_group=12,
        quality_tolerance_pct=2.0,
        solver_timeout_seconds=20.0,
    )
    planner["market"]["stage_d_candidate_model_limit"] = candidate_model_limit
    planner["market"]["ranking_parity_policy"] = (
        "stage-d-zero-call-paid-and-production-use-configured-ranking-limit"
    )
    base._write_json(root / "v5-model-endpoint-market.json", planner["market"])
    base._write_json(root / "v5-candidate-graph.json", planner["candidate_graph"])
    base._write_json(root / "v5-optimization.json", planner["optimization"])
    base._write_json(
        root / "v5-execution-graph.json",
        planner["optimization"]["execution_graph"],
    )
    graph = ExecutionGraph.from_mapping(planner["optimization"]["execution_graph"])
    result: Mapping[str, Any] = {}
    error = ""
    try:
        result = execute_v5_graph(graph, run, run.task, output_dir=root, limits=limits)
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
            "candidate_model_limit": candidate_model_limit,
            "ranking_parity_verified": candidate_model_limit
            == int(run.ranking_limit),
        },
    )
    return outcome, planner["market"]


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    base._v5_strategy = production_parity_v5_strategy
    _INSTALLED = True
