#!/usr/bin/env python3
"""Zero-model-call feasibility gate for the economical V5 versus V3 benchmark.

This diagnostic may read the live OpenRouter model catalog and provider endpoint
metadata, but it never calls the chat/completions endpoint and never executes V3
or V5 model inference. It determines whether each benchmark task has a feasible
V5 execution graph under bounded endpoint-price, node and estimated-cost tiers.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import model_market as market
import v5_candidate_diagnostics
import v5_candidate_diversity
import v5_live_benchmark as base
import v5_planner
import v5_value_optimizer
from artifact_manifest import write_manifest
from execution_graph import GraphLimits

DEFAULT_TASK_IDS = (
    "retail-expansion-unit-economics",
    "software-job-runner-security",
    "public-health-rumor-response",
)
MAX_TASKS = 3
NODE_LIMITS = (8, 9, 10, 12, 16)
BUDGET_GRID_USD = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50)
ECONOMY_V5_BUDGET_CEILING_USD = 0.30
PRICE_TIERS = (
    {
        "name": "strict-economy",
        "prompt_usd_per_million": 2.50,
        "completion_usd_per_million": 8.00,
        "minimum_reliability": 0.80,
    },
    {
        "name": "expanded-value",
        "prompt_usd_per_million": 3.00,
        "completion_usd_per_million": 10.00,
        "minimum_reliability": 0.80,
    },
    {
        "name": "bounded-capability",
        "prompt_usd_per_million": 5.00,
        "completion_usd_per_million": 15.00,
        "minimum_reliability": 0.80,
    },
)


class DiagnosticError(RuntimeError):
    """Raised when the zero-call diagnostic cannot produce trustworthy evidence."""


def _write_json(path: str | Path, value: Any) -> None:
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _write_output(name: str, value: Any) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    text = str(value).replace("\n", " ").replace("\r", " ")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={text}\n")


def prepare(event_path: str | Path, output_dir: str | Path) -> int:
    event = base._load_json(event_path)
    issue = event.get("issue") if isinstance(event.get("issue"), Mapping) else {}
    body = str(issue.get("body") or "").strip()
    raw: Mapping[str, Any] = {}
    if body:
        parsed = json.loads(body)
        if not isinstance(parsed, Mapping):
            raise DiagnosticError("Issue body must be one JSON object")
        raw = parsed

    # Accept the existing economy benchmark body unchanged. Cost and call fields
    # are recorded only as context; this diagnostic cannot spend them.
    allowed = {
        "benchmark_id",
        "diagnostic_id",
        "max_cost_usd",
        "max_calls",
        "max_strategy_cost_usd",
        "output_allowance_tokens",
        "task_ids",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise DiagnosticError(f"Unknown diagnostic config fields: {unknown}")

    suite = base._load_json(base.DEFAULT_SUITE)
    available = {
        str(row.get("task_id"))
        for row in suite.get("tasks", [])
        if isinstance(row, Mapping) and row.get("task_id")
    }
    task_ids = [str(value) for value in raw.get("task_ids", DEFAULT_TASK_IDS)]
    if len(task_ids) != MAX_TASKS:
        raise DiagnosticError(f"Zero-call cutover diagnostic requires exactly {MAX_TASKS} tasks")
    if len(set(task_ids)) != len(task_ids) or any(value not in available for value in task_ids):
        raise DiagnosticError("task_ids must be three distinct known benchmark tasks")

    config = {
        "version": 1,
        "mode": "economy-zero-call-feasibility",
        "diagnostic_id": str(
            raw.get("diagnostic_id")
            or raw.get("benchmark_id")
            or "v5-economy-zero-call-feasibility-20260730"
        ),
        "task_ids": task_ids,
        "node_limits": list(NODE_LIMITS),
        "budget_grid_usd": list(BUDGET_GRID_USD),
        "economy_v5_budget_ceiling_usd": ECONOMY_V5_BUDGET_CEILING_USD,
        "price_tiers": list(PRICE_TIERS),
        "context_from_paid_benchmark": {
            "max_cost_usd": raw.get("max_cost_usd"),
            "max_calls": raw.get("max_calls"),
            "max_strategy_cost_usd": raw.get("max_strategy_cost_usd"),
            "output_allowance_tokens": raw.get("output_allowance_tokens"),
        },
        "model_inference_calls_allowed": 0,
        "paid_model_calls_allowed": 0,
        "production_entrypoint_changed": False,
        "v3_deleted": False,
        "issue_number": int(issue.get("number") or 0),
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "zero-call-diagnostic-config.json", config)
    _write_output("diagnostic_id", config["diagnostic_id"])
    _write_output("task_count", len(task_ids))
    _write_output("paid_model_calls_allowed", 0)
    return 0


def _filter_market(raw_market: Mapping[str, Any], tier: Mapping[str, Any]) -> dict[str, Any]:
    source = [row for row in raw_market.get("endpoints", []) if isinstance(row, Mapping)]
    kept = [
        dict(row)
        for row in source
        if float(row.get("prompt_price_per_million", float("inf")))
        <= float(tier["prompt_usd_per_million"])
        and float(row.get("completion_price_per_million", float("inf")))
        <= float(tier["completion_usd_per_million"])
        and float(row.get("reliability", 0.0)) >= float(tier["minimum_reliability"])
        and not bool(row.get("synthetic_fixture_only"))
    ]
    result = dict(raw_market)
    result.update(
        {
            "endpoints": kept,
            "endpoint_count": len(kept),
            "real_endpoint_count": len(kept),
            "synthetic_fixture_count": 0,
            "zero_call_price_tier": dict(tier),
        }
    )
    return result


def _attempt(
    candidate_bundle: Mapping[str, Any],
    *,
    max_nodes: int,
    max_budget_usd: float | None,
) -> dict[str, Any]:
    limits = GraphLimits(
        max_nodes=max_nodes,
        max_edges=64,
        max_stages=8,
        max_model_calls=max_nodes,
        max_retries=0,
        max_replacements=0,
        max_budget_usd=max_budget_usd,
    )
    try:
        optimized = v5_value_optimizer.optimize_execution_graph(
            candidate_bundle,
            limits=limits,
            solver_timeout_seconds=20.0,
        )
    except v5_planner.V5PlanningError as exc:
        return {
            "feasible": False,
            "max_nodes": max_nodes,
            "max_budget_usd": max_budget_usd,
            "error": str(exc),
        }
    graph = optimized.get("execution_graph", {})
    nodes = graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []
    stages = graph.get("execution_stages", []) if isinstance(graph.get("execution_stages"), list) else []
    return {
        "feasible": True,
        "max_nodes": max_nodes,
        "max_budget_usd": max_budget_usd,
        "selected_node_count": len(nodes),
        "selected_stage_count": len(stages),
        "estimated_total_cost_usd": round(float(graph.get("estimated_total_cost", 0.0) or 0.0), 8),
        "selected_interpretation": optimized.get("selected_interpretation"),
        "cost_performance_ratio": optimized.get("cost_performance_ratio"),
        "solver_status": optimized.get("solver_status"),
    }


def _diagnose_tier(
    resources: Mapping[str, Any],
    raw_market: Mapping[str, Any],
    tier: Mapping[str, Any],
) -> dict[str, Any]:
    filtered_market = _filter_market(raw_market, tier)
    endpoint_count = int(filtered_market.get("endpoint_count", 0) or 0)
    model_count = len(
        {
            str(row.get("model_id"))
            for row in filtered_market.get("endpoints", [])
            if isinstance(row, Mapping) and row.get("model_id")
        }
    )
    if endpoint_count == 0:
        return {
            "tier": dict(tier),
            "endpoint_count": 0,
            "model_count": 0,
            "candidate_generation_succeeded": False,
            "error": "no real provider endpoint satisfies this price/reliability tier",
            "model_inference_calls": 0,
            "actual_cost_usd": 0.0,
        }

    try:
        candidates = v5_planner.generate_candidate_graph(
            resources,
            filtered_market,
            maximum_per_group=12,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "tier": dict(tier),
            "endpoint_count": endpoint_count,
            "model_count": model_count,
            "candidate_generation_succeeded": False,
            "error": str(exc),
            "model_inference_calls": 0,
            "actual_cost_usd": 0.0,
        }

    structure = v5_candidate_diagnostics.analyze_candidate_structure(
        filtered_market,
        candidates,
    )
    node_attempts = [
        _attempt(candidates, max_nodes=limit, max_budget_usd=None)
        for limit in NODE_LIMITS
    ]
    budget_attempts = [
        _attempt(candidates, max_nodes=max(NODE_LIMITS), max_budget_usd=budget)
        for budget in BUDGET_GRID_USD
    ]
    feasible_nodes = [row for row in node_attempts if row.get("feasible")]
    feasible_budgets = [row for row in budget_attempts if row.get("feasible")]
    minimum_node_limit = min((int(row["max_nodes"]) for row in feasible_nodes), default=None)
    minimum_budget = min((float(row["max_budget_usd"]) for row in feasible_budgets), default=None)
    feasible_within_economy = [
        row
        for row in feasible_budgets
        if float(row.get("max_budget_usd") or 0.0) <= ECONOMY_V5_BUDGET_CEILING_USD + 1e-12
    ]
    best_within_economy = min(
        feasible_within_economy,
        key=lambda row: (
            float(row.get("estimated_total_cost_usd", float("inf"))),
            int(row.get("selected_node_count", 10**9)),
        ),
        default=None,
    )
    return {
        "tier": dict(tier),
        "endpoint_count": endpoint_count,
        "model_count": model_count,
        "candidate_generation_succeeded": True,
        "candidate_count_before_pareto": candidates.get("candidate_count_before_pareto"),
        "candidate_count_after_pareto": candidates.get("candidate_count_after_pareto"),
        "candidate_structure": structure,
        "node_limit_attempts": node_attempts,
        "budget_attempts": budget_attempts,
        "minimum_feasible_node_limit": minimum_node_limit,
        "minimum_feasible_budget_usd": minimum_budget,
        "feasible_within_economy_v5_budget": bool(feasible_within_economy),
        "best_within_economy": best_within_economy,
        "model_inference_calls": 0,
        "actual_cost_usd": 0.0,
    }


def _task_diagnostic(
    task: Mapping[str, Any],
    root: Path,
    models: Mapping[str, Any],
    endpoint_cache: dict[str, Mapping[str, Any]],
) -> dict[str, Any]:
    task_id = str(task["task_id"])
    task_root = root / "tasks" / task_id
    task_root.mkdir(parents=True, exist_ok=True)
    run = market.build_run_config(
        base._namespace(base._task_text(task), task_root, ranking_limit=50)
    )
    profile = market.classify_task(run.task, run)
    ranked = base._rank_v5_models(models, profile, run)
    scoped = ranked[:24]
    missing = [row for row in scoped if row.id not in endpoint_cache]
    if missing:
        endpoint_cache.update(
            v5_planner.fetch_live_endpoint_payloads(
                missing,
                run,
                maximum_models=24,
            )
        )
    endpoint_payloads = {row.id: endpoint_cache.get(row.id, {}) for row in scoped}
    resources = base.compile_v5_task_resources(profile, run)
    base.write_task_resource_artifacts(resources, task_root)
    raw_market = v5_planner.compile_model_endpoint_market(
        scoped,
        resources,
        endpoint_payloads=endpoint_payloads,
        ranking_limit=24,
        allow_synthetic_fixture=False,
    )
    _write_json(task_root / "raw-model-endpoint-market.json", raw_market)

    tiers = [_diagnose_tier(resources, raw_market, tier) for tier in PRICE_TIERS]
    feasible = [row for row in tiers if row.get("feasible_within_economy_v5_budget")]
    recommended = min(
        feasible,
        key=lambda row: (
            PRICE_TIERS.index(row["tier"]),
            float((row.get("best_within_economy") or {}).get("estimated_total_cost_usd", float("inf"))),
        ),
        default=None,
    )
    result = {
        "task_id": task_id,
        "domain": task.get("domain"),
        "ranked_model_count": len(ranked),
        "raw_market_endpoint_count": raw_market.get("endpoint_count"),
        "tiers": tiers,
        "ready_for_economy_paid_benchmark": recommended is not None,
        "recommended_tier": recommended.get("tier") if recommended else None,
        "recommended_plan": recommended.get("best_within_economy") if recommended else None,
        "model_inference_calls": 0,
        "actual_cost_usd": 0.0,
    }
    _write_json(task_root / "zero-call-task-diagnostic.json", result)
    return result


def _summary_markdown(bundle: Mapping[str, Any]) -> str:
    lines = [
        "# V5 Economy Zero-Call Feasibility Diagnostic",
        "",
        f"- Status: `{bundle.get('status')}`",
        f"- Tasks ready: `{bundle.get('tasks_ready')}` / `{bundle.get('tasks_requested')}`",
        f"- Ready for paid benchmark: `{str(bool(bundle.get('ready_for_paid_benchmark'))).lower()}`",
        "- Model inference calls: `0`",
        "- Actual model cost: `$0.000000`",
        "- Production entrypoint changed: `false`",
        "- V3 deleted: `false`",
        "",
        "## Task feasibility",
        "",
        "| Task | Ready | Tier | Nodes | Estimated V5 cost USD |",
        "|---|---:|---|---:|---:|",
    ]
    for task in bundle.get("tasks", []):
        if not isinstance(task, Mapping):
            continue
        plan = task.get("recommended_plan") if isinstance(task.get("recommended_plan"), Mapping) else {}
        tier = task.get("recommended_tier") if isinstance(task.get("recommended_tier"), Mapping) else {}
        lines.append(
            "| {task} | {ready} | {tier} | {nodes} | {cost} |".format(
                task=task.get("task_id"),
                ready=str(bool(task.get("ready_for_economy_paid_benchmark"))).lower(),
                tier=tier.get("name", "-"),
                nodes=plan.get("selected_node_count", "-"),
                cost=(
                    f"{float(plan.get('estimated_total_cost_usd')):.6f}"
                    if plan.get("estimated_total_cost_usd") is not None
                    else "-"
                ),
            )
        )
    lines.extend(
        [
            "",
            "This workflow only reads catalog and provider endpoint metadata. It does not call any model completion endpoint.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def run(config_path: str | Path, suite_path: str | Path, output_dir: str | Path) -> int:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise DiagnosticError("OPENROUTER_API_KEY is not set")
    config = base._load_json(config_path)
    suite = base._load_json(suite_path)
    requested = [str(value) for value in config.get("task_ids", [])]
    by_id = {
        str(row.get("task_id")): row
        for row in suite.get("tasks", [])
        if isinstance(row, Mapping) and row.get("task_id")
    }
    tasks = [by_id[value] for value in requested if value in by_id]
    if len(tasks) != len(requested):
        raise DiagnosticError("one or more configured tasks are absent from the suite")

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    v5_candidate_diversity.install()
    catalog_run = market.build_run_config(
        base._namespace(base._task_text(tasks[0]), root / "catalog", ranking_limit=50)
    )
    models, catalog_source = market.fetch_catalog(catalog_run)
    endpoint_cache: dict[str, Mapping[str, Any]] = {}
    task_results: list[dict[str, Any]] = []
    status = "complete"
    error: str | None = None
    try:
        for task in tasks:
            task_results.append(_task_diagnostic(task, root, models, endpoint_cache))
    except Exception as exc:  # noqa: BLE001
        status = "technical_failure"
        error = str(exc)

    tasks_ready = sum(bool(row.get("ready_for_economy_paid_benchmark")) for row in task_results)
    bundle = {
        "version": 1,
        "mode": "economy-zero-call-feasibility",
        "diagnostic_id": config.get("diagnostic_id"),
        "status": status,
        "error": error,
        "catalog_source": catalog_source,
        "tasks_requested": len(tasks),
        "tasks_completed": len(task_results),
        "tasks_ready": tasks_ready,
        "ready_for_paid_benchmark": status == "complete" and tasks_ready == len(tasks),
        "economy_v5_budget_ceiling_usd": ECONOMY_V5_BUDGET_CEILING_USD,
        "tasks": task_results,
        "model_inference_calls": 0,
        "paid_model_calls": 0,
        "actual_cost_usd": 0.0,
        "production_entrypoint_changed": False,
        "v3_deleted": False,
        "paid_benchmark_triggered": False,
    }
    _write_json(root / "v5-economy-zero-call-diagnostic.json", bundle)
    (root / "v5-economy-zero-call-summary.md").write_text(
        _summary_markdown(bundle),
        encoding="utf-8",
    )
    write_manifest(root)
    print(json.dumps(bundle, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Zero-call V5 economy feasibility diagnostic")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--event-path", required=True)
    prepare_parser.add_argument("--output-dir", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--suite", required=True)
    run_parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        if args.command == "prepare":
            return prepare(args.event_path, args.output_dir)
        return run(args.config, args.suite, args.output_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
