#!/usr/bin/env python3
"""End-to-end V5 planning and execution through one explicit runtime."""
from __future__ import annotations

import argparse
import json
import math
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

import model_market as market
from artifact_manifest import write_manifest
from execution_graph import ExecutionGraph, GraphLimits
from resource_matrix import compile_v5_task_resources
from task_resource_artifacts import write_task_resource_artifacts
from v5_benchmark import planning_benchmark, write_benchmark
from v5_endpoint_catalog import fetch_live_endpoint_payloads
from v5_general_task_planning import (
    classify_task as classify_production_task,
    compile_task_semantics as compile_production_task_semantics,
)
from v5_model_company import (
    MINIMUM_CANDIDATES_PER_WORK,
    candidate_company,
)
from v5_planning_diagnostics import (
    build_candidate_generation_failure_report,
    build_infeasibility_report,
)
from v5_runtime import ProductionRuntime, RuntimeConfig


def _load_json(path: str | Path) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile and execute the standalone V5 dynamic expert DAG."
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
    parser.add_argument(
        "--maximum-candidates-per-work",
        type=int,
        default=MINIMUM_CANDIDATES_PER_WORK,
    )
    parser.add_argument("--maximum-total-calls", type=int, default=16)
    parser.add_argument("--maximum-recovery-calls", type=int, default=2)
    parser.add_argument("--cost-anomaly-usd", type=float)
    parser.add_argument(
        "--quality-tolerance-pct",
        type=float,
        default=2.0,
        help="Deprecated compatibility option; ignored by the cost-performance optimizer.",
    )
    parser.add_argument("--solver-timeout-seconds", type=float, default=20.0)
    return parser


def _rank_v5_models(models: Mapping[str, Any], profile: Any, run: Any) -> list[Any]:
    """Build a deduplicated multi-channel pool before CP-SAT optimization."""
    eligible: list[Any] = []
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
        }
        model.score = 0.42 * intelligence + 0.30 * fit + 0.23 * min(1.0, value) + 0.05 * context_ratio
        model.fit_reasons = list(reasons) + [
            "V5多通道候选池：智能、任务匹配、性价比、低价合格和上下文适配分别入围"
        ]
        eligible.append(model)

    if len(eligible) < 2:
        raise market.ExpertTeamError("V5 requires at least two eligible direct models after safety filtering.")

    limit = max(2, int(run.ranking_limit))
    channels = [
        sorted(eligible, key=lambda row: (int(row.components["intelligence_rank"]), row.id)),
        sorted(eligible, key=lambda row: (-float(row.components["task_fit"]), -float(row.score), row.id)),
        sorted(eligible, key=lambda row: (-float(row.components["value_index"]), float(row.blended_price_per_million or math.inf), row.id)),
        sorted(eligible, key=lambda row: (float(row.blended_price_per_million or math.inf), -float(row.components["task_fit"]), row.id)),
        sorted(eligible, key=lambda row: (-float(row.components["context_fit"]), -float(row.score), row.id)),
    ]
    selected: list[Any] = []
    seen: set[str] = set()
    index = 0
    while len(selected) < limit and any(index < len(channel) for channel in channels):
        for channel in channels:
            if index >= len(channel):
                continue
            model = channel[index]
            if model.id in seen:
                continue
            seen.add(model.id)
            selected.append(model)
            if len(selected) >= limit:
                break
        index += 1
    return selected


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _runtime_from_args(args: argparse.Namespace, run: Any) -> ProductionRuntime:
    config = RuntimeConfig(
        total_call_limit=int(args.maximum_total_calls),
        recovery_call_limit=int(args.maximum_recovery_calls),
        cost_anomaly_usd=args.cost_anomaly_usd,
        quality_tier=str(run.quality_tier or "value"),
        tools_allowed=False,
        live_catalog_required=bool(args.require_live_catalog),
        provider_lock_required=True,
        maximum_candidates_per_work=max(2, int(args.maximum_candidates_per_work)),
        solver_timeout_seconds=max(1.0, float(args.solver_timeout_seconds)),
    )
    return ProductionRuntime(config)


def _validated_budget(
    args: argparse.Namespace,
    run: Any,
    runtime: ProductionRuntime,
) -> tuple[int, int, int, float | None]:
    config = runtime.config
    if int(args.maximum_total_calls) != config.total_call_limit:
        raise ValueError("CLI total-call value differs from RuntimeConfig")
    if int(args.maximum_recovery_calls) != config.recovery_call_limit:
        raise ValueError("CLI recovery-call value differs from RuntimeConfig")
    if args.cost_anomaly_usd != config.cost_anomaly_usd:
        raise ValueError("CLI cost anomaly value differs from RuntimeConfig")
    if str(run.quality_tier or "value") != config.quality_tier:
        raise ValueError("RunConfig quality tier differs from RuntimeConfig")

    approved_initial = config.initial_call_limit
    tier_cap = {"budget": 4, "value": 6, "quality": approved_initial}.get(config.quality_tier, 6)
    planning_nodes = max(1, min(approved_initial, tier_cap))
    return (
        config.total_call_limit,
        config.recovery_call_limit,
        planning_nodes,
        config.cost_anomaly_usd,
    )


def _planning_limits(
    *,
    total_calls: int,
    recovery_calls: int,
    planning_nodes: int,
    anomaly_budget: float | None,
    runtime: ProductionRuntime,
) -> GraphLimits:
    """Derive every planning limit from the one immutable runtime configuration."""
    return GraphLimits(
        max_nodes=planning_nodes,
        max_edges=64,
        max_stages=8,
        max_model_calls=total_calls,
        max_retries=recovery_calls,
        max_replacements=recovery_calls,
        max_budget_usd=anomaly_budget,
        cost_risk_multiplier=runtime.config.cost_risk_multiplier,
    )


def _annotate_market(
    compiled_market: dict[str, Any],
    *,
    ranked: Sequence[Any],
    catalog_source: str,
    endpoint_source: str,
    catalog_snapshot_id: str,
) -> None:
    companies = [candidate_company(model) for model in ranked]
    compiled_market["catalog_source"] = catalog_source
    compiled_market["endpoint_source"] = endpoint_source
    compiled_market["catalog_snapshot_id"] = catalog_snapshot_id
    compiled_market["candidate_pool_policy"] = "top-150-multi-channel-company-diverse-before-optimizer"
    compiled_market["model_company_policy"] = "task-global-all-different"
    compiled_market["ranked_model_count"] = len(ranked)
    compiled_market["ranked_company_count"] = len(set(companies))
    compiled_market["endpoint_fetch_policy"] = "bounded-concurrent-deterministic-order"
    compiled_market["cross_task_history_used"] = False
    compiled_market["ranked_models"] = [
        {
            "rank": index,
            "model": model.id,
            "model_company": candidate_company(model),
            "official_intelligence_rank": (model.ranks or {}).get("intelligence-high-to-low"),
            "prompt_usd_per_million": model.prompt_price_per_million,
            "completion_usd_per_million": model.completion_price_per_million,
            "context_length": model.context_length,
            "max_completion_tokens": model.max_completion_tokens,
            "components": dict(model.components),
        }
        for index, model in enumerate(ranked, 1)
    ]


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime: ProductionRuntime | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    run = market.build_run_config(args)
    runtime = runtime or _runtime_from_args(args, run)
    output = Path(run.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    total_calls, recovery_calls, planning_nodes, anomaly_budget = _validated_budget(args, run, runtime)
    _write_json(output / "v5-runtime-config.json", runtime.describe())

    profile = classify_production_task(run.task, run)
    models, catalog_source = market.fetch_catalog(run)
    ranked = _rank_v5_models(models, profile, run)
    resources = compile_v5_task_resources(
        profile,
        run,
        semantic_compiler=compile_production_task_semantics,
    )
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
        endpoint_payloads = fetch_live_endpoint_payloads(
            ranked,
            run,
            maximum_models=run.ranking_limit,
        )
        endpoint_source = "openrouter-live-model-endpoints-bounded-concurrent"
        allow_synthetic = False

    snapshot = runtime.build_catalog_snapshot(
        ranked,
        endpoint_payloads,
        catalog_source=catalog_source,
        endpoint_source=endpoint_source,
    )
    _write_json(output / "catalog-snapshot.json", snapshot.to_dict())

    limits = _planning_limits(
        total_calls=total_calls,
        recovery_calls=recovery_calls,
        planning_nodes=planning_nodes,
        anomaly_budget=anomaly_budget,
        runtime=runtime,
    )

    compiled_market = runtime.planner_policy.compile_market(
        ranked,
        resources,
        endpoint_payloads=snapshot.endpoint_payloads,
        ranking_limit=run.ranking_limit,
        allow_synthetic_fixture=allow_synthetic,
    )
    _annotate_market(
        compiled_market,
        ranked=ranked,
        catalog_source=catalog_source,
        endpoint_source=endpoint_source,
        catalog_snapshot_id=snapshot.snapshot_id,
    )
    _write_json(output / "v5-model-endpoint-market.json", compiled_market)

    try:
        candidate_graph = runtime.planner_policy.generate_candidate_graph(
            resources,
            compiled_market,
            maximum_per_group=max(3, min(30, runtime.config.maximum_candidates_per_work)),
        )
    except Exception as exc:
        report = build_candidate_generation_failure_report(
            resources,
            compiled_market,
            message=str(exc),
        )
        _write_json(output / "v5-planning-infeasibility.json", report)
        write_manifest(output)
        raise market.ExpertTeamError(
            f"{report['code']}: {report['message']}"
        ) from exc

    _write_json(output / "v5-candidate-graph.json", candidate_graph)
    try:
        optimization = runtime.planner_policy.optimize_execution_graph(
            candidate_graph,
            limits=limits,
            quality_tolerance_pct=max(0.0, min(20.0, float(args.quality_tolerance_pct))),
            solver_timeout_seconds=runtime.config.solver_timeout_seconds,
        )
    except Exception as exc:
        report = build_infeasibility_report(
            candidate_graph,
            limits,
            message=str(exc),
        )
        _write_json(output / "v5-planning-infeasibility.json", report)
        write_manifest(output)
        raise market.ExpertTeamError(
            f"{report['code']}: {report['message']}"
        ) from exc

    planner = {
        "version": 5,
        "market": compiled_market,
        "candidate_graph": candidate_graph,
        "optimization": optimization,
    }
    planner["optimization"]["approved_budget"] = {
        "maximum_total_calls": total_calls,
        "maximum_recovery_calls": recovery_calls,
        "maximum_initial_calls": runtime.config.initial_call_limit,
        "planning_node_cap": planning_nodes,
        "cost_anomaly_usd": anomaly_budget,
        "quality_tier": run.quality_tier,
        "ranking_limit": run.ranking_limit,
        "maximum_candidates_per_work": runtime.config.maximum_candidates_per_work,
        "model_company_policy": "task-global-all-different",
        "runtime_config_sha256": sha256(
            json.dumps(runtime.config.to_dict(), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
    }
    planner["optimization"]["catalog_snapshot_id"] = snapshot.snapshot_id
    _write_json(output / "v5-optimization.json", planner["optimization"])
    _write_json(output / "v5-execution-graph.json", planner["optimization"]["execution_graph"])

    benchmark = planning_benchmark(planner)
    write_benchmark(output / "v5-planning-benchmark.json", benchmark)
    graph = ExecutionGraph.from_mapping(planner["optimization"]["execution_graph"])

    if run.dry_run:
        requests = [runtime.build_node_payload(node, run.task, []) for node in graph.nodes]
        _write_json(output / "v5-dry-run.json", {
            "version": 5,
            "status": "planned-not-executed",
            "execution_graph": graph.to_dict(),
            "requests": requests,
            "planning_benchmark": benchmark,
            "production_entrypoint_changed": False,
            "legacy_runtime_present": False,
            "fallback_policy": "fail-closed-no-alternate-runtime",
            "approved_budget": planner["optimization"]["approved_budget"],
            "catalog_snapshot_id": snapshot.snapshot_id,
            "runtime_version": runtime.describe()["runtime_version"],
            "model_company_policy": "task-global-all-different",
            "ranking_limit": run.ranking_limit,
            "maximum_candidates_per_work": runtime.config.maximum_candidates_per_work,
            "global_monkey_patching": False,
        })
        write_manifest(output)
        print(f"V5 dry-run artifacts written to {output}")
        return 0

    result = runtime.execute_graph(
        graph,
        run,
        run.task,
        output_dir=output,
        limits=limits,
    )
    _write_json(output / "v5-result.json", result)
    write_manifest(output)
    print(f"V5 execution completed: {output / 'v5-final-report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
