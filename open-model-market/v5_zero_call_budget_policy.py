#!/usr/bin/env python3
"""Request-bound budget policy and evidence expansion for zero-call V5 diagnostics.

This wrapper preserves the existing metadata-only feasibility diagnostic while
making its readiness gate obey the request's per-strategy task cap. It never
calls a model completion endpoint and never executes V3 or V5 inference.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_economy_zero_call_diagnostic as diagnostic
import v5_planner
import v5_value_optimizer
from execution_graph import GraphLimits

MAX_DIAGNOSTIC_BUDGET_USD = 0.30
MIN_DIAGNOSTIC_BUDGET_USD = 0.01


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise diagnostic.DiagnosticError("diagnostic config must be one JSON object")
    return value


def _write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _write_output(name: str, value: Any) -> None:
    diagnostic._write_output(name, value)


def _requested_budget(config: Mapping[str, Any]) -> float:
    context = config.get("context_from_paid_benchmark")
    context = context if isinstance(context, Mapping) else {}
    raw = context.get("max_strategy_cost_usd")
    if raw is None:
        return MAX_DIAGNOSTIC_BUDGET_USD
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise diagnostic.DiagnosticError(
            "max_strategy_cost_usd must be numeric"
        ) from exc
    if not MIN_DIAGNOSTIC_BUDGET_USD <= value <= MAX_DIAGNOSTIC_BUDGET_USD:
        raise diagnostic.DiagnosticError(
            "max_strategy_cost_usd must be between 0.01 and 0.30 USD"
        )
    return round(value, 6)


def _budget_grid(target: float) -> tuple[float, ...]:
    nearby = {
        target - 0.02,
        target - 0.01,
        target - 0.005,
        target,
        target + 0.0025,
        target + 0.005,
        target + 0.01,
        target + 0.02,
    }
    values = set(float(value) for value in diagnostic.BUDGET_GRID_USD)
    values.update(nearby)
    return tuple(
        sorted(
            round(value, 6)
            for value in values
            if MIN_DIAGNOSTIC_BUDGET_USD <= value <= 0.50
        )
    )


def prepare(event_path: str | Path, output_dir: str | Path) -> int:
    result = diagnostic.prepare(event_path, output_dir)
    config_path = Path(output_dir) / "zero-call-diagnostic-config.json"
    config = _load_json(config_path)
    target = _requested_budget(config)
    grid = _budget_grid(target)
    config.update(
        {
            "version": 2,
            "requested_v5_budget_ceiling_usd": target,
            "budget_grid_usd": list(grid),
            "readiness_budget_policy": (
                "request-max_strategy_cost_usd; fallback-0.30; never-above-0.30"
            ),
            "model_inference_calls_allowed": 0,
            "paid_model_calls_allowed": 0,
            "production_entrypoint_changed": False,
            "v3_deleted": False,
        }
    )
    _write_json(config_path, config)
    _write_output("target_v5_budget_ceiling_usd", f"{target:.6f}")
    return result


def _node_audit(node: Mapping[str, Any]) -> dict[str, Any]:
    parameter = node.get("parameter_profile")
    parameter = parameter if isinstance(parameter, Mapping) else {}
    reasoning = node.get("reasoning_profile")
    reasoning = reasoning if isinstance(reasoning, Mapping) else {}
    return {
        "node_id": node.get("node_id"),
        "assigned_work": list(node.get("assigned_work") or []),
        "copy_indices": list(node.get("copy_indices") or []),
        "model": node.get("model"),
        "provider_endpoint": node.get("provider_endpoint"),
        "estimated_cost_usd": round(float(node.get("estimated_cost", 0.0) or 0.0), 8),
        "failure_probability": round(
            float(node.get("failure_probability", 0.0) or 0.0), 6
        ),
        "functions": list(node.get("functions") or []),
        "reasoning_effort": reasoning.get("effort"),
        "reasoning_depth": reasoning.get("depth"),
        "recommended_output_allowance_tokens": parameter.get(
            "recommended_output_allowance_tokens"
        ),
        "configured_max_tokens": parameter.get("max_tokens"),
        "cost_estimation_policy": parameter.get("cost_estimation_policy"),
    }


def audited_attempt(
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

    graph = optimized.get("execution_graph")
    graph = graph if isinstance(graph, Mapping) else {}
    nodes = graph.get("nodes")
    nodes = nodes if isinstance(nodes, list) else []
    stages = graph.get("execution_stages")
    stages = stages if isinstance(stages, list) else []
    estimated = round(float(graph.get("estimated_total_cost", 0.0) or 0.0), 8)
    selected_nodes = [
        _node_audit(node) for node in nodes if isinstance(node, Mapping)
    ]
    node_cost_sum = round(
        sum(float(node.get("estimated_cost_usd", 0.0)) for node in selected_nodes),
        8,
    )
    return {
        "feasible": True,
        "max_nodes": max_nodes,
        "max_budget_usd": max_budget_usd,
        "budget_slack_usd": (
            round(float(max_budget_usd) - estimated, 8)
            if max_budget_usd is not None
            else None
        ),
        "selected_node_count": len(nodes),
        "selected_stage_count": len(stages),
        "estimated_total_cost_usd": estimated,
        "selected_node_cost_sum_usd": node_cost_sum,
        "selected_interpretation": optimized.get("selected_interpretation"),
        "selected_candidate_ids": list(
            optimized.get("selected_candidate_ids") or []
        ),
        "selected_model_count": len(
            {str(node.get("model") or "") for node in selected_nodes}
        ),
        "selected_provider_endpoint_count": len(
            {
                str(node.get("provider_endpoint") or "")
                for node in selected_nodes
            }
        ),
        "selected_nodes": selected_nodes,
        "execution_stages": stages,
        "independence_policy": (
            graph.get("metadata", {}).get("independence_policy")
            if isinstance(graph.get("metadata"), Mapping)
            else None
        ),
        "cost_performance_ratio": optimized.get("cost_performance_ratio"),
        "solver_status": optimized.get("solver_status"),
    }


def run(config_path: str | Path, suite_path: str | Path, output_dir: str | Path) -> int:
    config = _load_json(config_path)
    target = float(config.get("requested_v5_budget_ceiling_usd", _requested_budget(config)))
    diagnostic.ECONOMY_V5_BUDGET_CEILING_USD = target
    diagnostic.BUDGET_GRID_USD = tuple(
        float(value) for value in config.get("budget_grid_usd", _budget_grid(target))
    )
    diagnostic._attempt = audited_attempt
    result = diagnostic.run(config_path, suite_path, output_dir)

    bundle_path = Path(output_dir) / "v5-economy-zero-call-diagnostic.json"
    if bundle_path.exists():
        bundle = _load_json(bundle_path)
        bundle.update(
            {
                "version": 2,
                "requested_v5_budget_ceiling_usd": target,
                "economy_v5_budget_ceiling_usd": target,
                "readiness_budget_policy": (
                    "request-max_strategy_cost_usd; fallback-0.30; never-above-0.30"
                ),
            }
        )
        _write_json(bundle_path, bundle)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Request-bound zero-call V5 feasibility diagnostic"
    )
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
