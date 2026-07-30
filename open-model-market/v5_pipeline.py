#!/usr/bin/env python3
"""End-to-end V5 planning/execution entrypoint isolated from the V3 runtime."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import model_market as market
from artifact_manifest import write_manifest
from execution_graph import ExecutionGraph, GraphLimits
from resource_matrix import compile_v5_task_resources
from task_resource_artifacts import write_task_resource_artifacts
from v5_benchmark import planning_benchmark, write_benchmark
from v5_executor import build_node_payload, execute_v5_graph
from v5_planner import compile_and_optimize_v5, fetch_live_endpoint_payloads


def _load_json(path: str | Path) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile and execute a V5 dynamic expert DAG without importing or changing the V3 production runtime."
    )
    parser.add_argument("--task", help="Task text. Can also use EXPERT_TASK.")
    parser.add_argument("--config", default=str(market.DEFAULT_CONFIG))
    parser.add_argument("--output-dir", default="v5-artifacts")
    parser.add_argument("--quality-tier", choices=["budget", "value", "quality"])
    parser.add_argument("--ranking-limit", type=int)
    parser.add_argument("--max-estimated-cost-usd")
    parser.add_argument("--max-completion-tokens", type=int)
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"])
    parser.add_argument("--catalog-file", help="Deterministic catalog fixture for tests.")
    parser.add_argument("--require-live-catalog", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--endpoint-file", help="Deterministic model endpoint fixture keyed by model ID.")
    parser.add_argument("--maximum-candidates-per-work", type=int, default=12)
    parser.add_argument("--quality-tolerance-pct", type=float, default=2.0)
    parser.add_argument("--solver-timeout-seconds", type=float, default=20.0)
    return parser


def _rank_v5_models(models: Mapping[str, Any], profile: Any, run: Any) -> list[Any]:
    """Rank only with current catalog intelligence, price, fit, and context.

    This function intentionally does not import V3 selector modules, history,
    popularity, throughput, latency, fixed-seat pools, or unstable-name heuristics.
    Endpoint stability is compiled separately from the real endpoint inventory.
    """
    ranked: list[Any] = []
    for model in models.values():
        model_id = str(getattr(model, "id", ""))
        if not model_id or model_id.startswith("openrouter/") or ":online" in model_id or ":batch" in model_id:
            continue
        if market._expired(getattr(model, "expiration_date", None)):
            continue
        if int(getattr(model, "context_length", 0) or 0) < int(profile.requested_context):
            continue
        if int(getattr(model, "max_completion_tokens", 0) or 0) <= 0:
            continue
        if getattr(model, "input_modalities", None) and "text" not in model.input_modalities:
            continue
        if getattr(model, "output_modalities", None) and "text" not in model.output_modalities:
            continue
        if getattr(model, "prompt_price_per_million", None) is None or getattr(model, "completion_price_per_million", None) is None:
            continue
        intelligence_rank = int((getattr(model, "ranks", {}) or {}).get("intelligence-high-to-low", 10_000))
        fit, reasons = market._task_fit(model, profile)
        price = float(model.blended_price_per_million or math.inf)
        context_ratio = min(1.0, int(model.context_length) / max(1, int(profile.requested_context) * 4))
        intelligence = 1.0 / max(1, intelligence_rank)
        value = intelligence / max(0.25, price)
        model.components = {
            "intelligence_rank": intelligence_rank,
            "task_fit": fit,
            "value_index": value,
            "context_fit": context_ratio,
            "history_used": 0.0,
            "speed_used": 0.0,
            "popularity_used": 0.0,
        }
        model.score = 0.52 * intelligence + 0.24 * fit + 0.19 * min(1.0, value) + 0.05 * context_ratio
        model.fit_reasons = list(reasons) + ["V5隔离排序：未使用历史、速度、热度或固定席位过滤"]
        ranked.append(model)
    ranked.sort(
        key=lambda row: (
            int((row.ranks or {}).get("intelligence-high-to-low", 10_000)),
            -(float(row.components.get("task_fit", 0.0))),
            float(row.blended_price_per_million or math.inf),
            row.id,
        )
    )
    if len(ranked) < 2:
        raise market.ExpertTeamError("V5 requires at least two eligible direct models after task-independent safety filtering.")
    return ranked[: max(2, int(run.ranking_limit))]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run = market.build_run_config(args)
    output = Path(run.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    profile = market.classify_task(run.task, run)
    models, catalog_source = market.fetch_catalog(run)
    ranked = _rank_v5_models(models, profile, run)

    resources = compile_v5_task_resources(profile, run)
    write_task_resource_artifacts(resources, output)

    if args.endpoint_file:
        endpoint_payloads = _load_json(args.endpoint_file)
        endpoint_source = f"fixture:{args.endpoint_file}"
        allow_synthetic = False
    elif run.dry_run and run.catalog_file:
        endpoint_payloads = {}
        endpoint_source = "synthetic-fixture-derived-from-model-catalog"
        allow_synthetic = True
    else:
        endpoint_payloads = fetch_live_endpoint_payloads(ranked, run, maximum_models=run.ranking_limit)
        endpoint_source = "openrouter-live-model-endpoints"
        allow_synthetic = False

    limits = GraphLimits(
        max_nodes=16,
        max_edges=64,
        max_stages=8,
        max_model_calls=16,
        max_retries=min(2, max(0, int(run.model_max_retries))),
        max_replacements=min(2, max(0, int(run.maximum_replacements))),
        max_budget_usd=run.max_estimated_cost_usd,
    )
    planner = compile_and_optimize_v5(
        ranked,
        resources,
        endpoint_payloads=endpoint_payloads,
        allow_synthetic_fixture=allow_synthetic,
        ranking_limit=run.ranking_limit,
        limits=limits,
        maximum_per_group=max(3, min(30, int(args.maximum_candidates_per_work))),
        quality_tolerance_pct=max(0.0, min(20.0, float(args.quality_tolerance_pct))),
        solver_timeout_seconds=max(1.0, float(args.solver_timeout_seconds)),
    )
    planner["market"]["catalog_source"] = catalog_source
    planner["market"]["endpoint_source"] = endpoint_source
    planner["market"]["ranked_models"] = [
        {
            "rank": index,
            "model": model.id,
            "official_intelligence_rank": (model.ranks or {}).get("intelligence-high-to-low"),
            "prompt_usd_per_million": model.prompt_price_per_million,
            "completion_usd_per_million": model.completion_price_per_million,
            "context_length": model.context_length,
            "max_completion_tokens": model.max_completion_tokens,
            "components": dict(model.components),
        }
        for index, model in enumerate(ranked, 1)
    ]
    _write_json(output / "v5-model-endpoint-market.json", planner["market"])
    _write_json(output / "v5-candidate-graph.json", planner["candidate_graph"])
    _write_json(output / "v5-optimization.json", planner["optimization"])
    _write_json(output / "v5-execution-graph.json", planner["optimization"]["execution_graph"])

    benchmark = planning_benchmark(planner)
    write_benchmark(output / "v5-planning-benchmark.json", benchmark)
    graph = ExecutionGraph.from_mapping(planner["optimization"]["execution_graph"])

    if run.dry_run:
        requests = [build_node_payload(node, run.task, []) for node in graph.nodes]
        _write_json(output / "v5-dry-run.json", {
            "version": 5,
            "status": "planned-not-executed",
            "execution_graph": graph.to_dict(),
            "requests": requests,
            "planning_benchmark": benchmark,
            "production_entrypoint_changed": False,
            "v3_runtime_imported": False,
        })
        write_manifest(output)
        print(f"V5 dry-run artifacts written to {output}")
        return 0

    result = execute_v5_graph(graph, run, run.task, output_dir=output, limits=limits)
    _write_json(output / "v5-result.json", result)
    write_manifest(output)
    print(f"V5 execution completed: {output / 'v5-final-report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
