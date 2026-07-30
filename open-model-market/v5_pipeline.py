#!/usr/bin/env python3
"""End-to-end V5 planning/execution entrypoint kept separate from V3 production."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import expert_team
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


def build_parser():
    parser = expert_team.build_parser()
    parser.description = "Compile and execute a V5 dynamic expert DAG without changing the V3 production entrypoint."
    parser.add_argument("--endpoint-file", help="Deterministic model endpoint fixture keyed by model ID.")
    parser.add_argument("--maximum-candidates-per-work", type=int, default=12)
    parser.add_argument("--quality-tolerance-pct", type=float, default=2.0)
    parser.add_argument("--solver-timeout-seconds", type=float, default=20.0)
    return parser


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run = expert_team.build_run_config(args)
    output = Path(run.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    profile = expert_team.classify_task(run.task, run)
    models, catalog_source = market.fetch_catalog(run)
    ranked = market.rank_models(models, profile, run)

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
