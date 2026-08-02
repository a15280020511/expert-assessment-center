#!/usr/bin/env python3
"""End-to-end constitutional V5 planning and execution.

Business choices are derived from the current task/resource matrix and the
current catalog snapshot. Fixed values are platform safety ceilings only.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import replace
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
    DEFAULT_INTELLIGENCE_RANKING_LIMIT,
    candidate_company,
)
from v5_planning_diagnostics import build_infeasibility_report
from v5_recovery_runtime import build_production_runtime
from v5_runtime import ProductionRuntime, RuntimeConfig

ABSOLUTE_CANDIDATES_PER_WORK_CEILING = 64
_DEGRADED_AUTHORIZATION_RE = re.compile(
    r"(?:允许|接受|可以)(?:部分|降级|不完整)(?:结果|交付)|"
    r"(?:partial|degraded|incomplete)\s+(?:result|delivery)\s+"
    r"(?:is\s+)?(?:allowed|acceptable)",
    re.IGNORECASE,
)


def _load_json(path: str | Path) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compile and execute the standalone constitutional V5 expert DAG."
        )
    )
    parser.add_argument("--task", help="Task text. Can also use EXPERT_TASK.")
    parser.add_argument("--config", default=str(market.DEFAULT_CONFIG))
    parser.add_argument("--output-dir", default="v5-artifacts")
    parser.add_argument(
        "--quality-tier",
        choices=["budget", "value", "quality"],
    )
    parser.add_argument(
        "--ranking-limit",
        type=int,
        help=(
            "Emergency catalog search ceiling; adaptive search may stop earlier."
        ),
    )
    parser.add_argument("--max-estimated-cost-usd")
    parser.add_argument("--max-completion-tokens", type=int)
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high"],
    )
    parser.add_argument(
        "--catalog-file",
        help="Deterministic catalog fixture for tests.",
    )
    parser.add_argument("--require-live-catalog", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--endpoint-file",
        help="Deterministic model endpoint fixture keyed by model ID.",
    )
    parser.add_argument(
        "--maximum-candidates-per-work",
        type=int,
        help=(
            "Emergency per-work candidate ceiling; initial breadth is task-derived."
        ),
    )
    parser.add_argument("--maximum-total-calls", type=int, default=16)
    parser.add_argument("--maximum-recovery-calls", type=int, default=2)
    parser.add_argument("--cost-anomaly-usd", type=float)
    parser.add_argument(
        "--quality-tolerance-pct",
        type=float,
        default=2.0,
        help="Compatibility option retained for the optimizer interface.",
    )
    parser.add_argument(
        "--solver-timeout-seconds",
        type=float,
        default=20.0,
    )
    return parser


def _rank_v5_models(
    models: Mapping[str, Any],
    profile: Any,
    run: Any,
) -> list[Any]:
    """Build a deduplicated multi-channel pool before CP-SAT optimization."""
    eligible: list[Any] = []
    for model in models.values():
        model_id = str(getattr(model, "id", ""))
        if (
            not model_id
            or model_id.startswith("openrouter/")
            or ":online" in model_id
            or ":batch" in model_id
        ):
            continue
        if market._expired(getattr(model, "expiration_date", None)):
            continue
        if int(getattr(model, "context_length", 0) or 0) < int(
            profile.requested_context
        ):
            continue
        if int(getattr(model, "max_completion_tokens", 0) or 0) <= 0:
            continue
        if (
            getattr(model, "input_modalities", None)
            and "text" not in model.input_modalities
        ):
            continue
        if (
            getattr(model, "output_modalities", None)
            and "text" not in model.output_modalities
        ):
            continue
        if (
            getattr(model, "prompt_price_per_million", None) is None
            or getattr(model, "completion_price_per_million", None) is None
        ):
            continue
        intelligence_rank = int(
            (getattr(model, "ranks", {}) or {}).get(
                "intelligence-high-to-low",
                10_000,
            )
        )
        fit, reasons = market._task_fit(model, profile)
        price = float(model.blended_price_per_million or math.inf)
        context_ratio = min(
            1.0,
            int(model.context_length)
            / max(1, int(profile.requested_context) * 4),
        )
        intelligence = 1.0 / max(1, intelligence_rank)
        value = intelligence / max(0.25, price)
        model.components = {
            "intelligence_rank": intelligence_rank,
            "task_fit": fit,
            "value_index": value,
            "context_fit": context_ratio,
        }
        model.score = (
            0.42 * intelligence
            + 0.30 * fit
            + 0.23 * min(1.0, value)
            + 0.05 * context_ratio
        )
        model.fit_reasons = list(reasons) + [
            "V5 multi-channel pool: intelligence, task fit, value, price and context"
        ]
        eligible.append(model)

    if len(eligible) < 2:
        raise market.ExpertTeamError(
            "V5 requires at least two eligible direct models after safety filtering."
        )

    limit = max(2, int(run.ranking_limit))
    channels = [
        sorted(
            eligible,
            key=lambda row: (
                int(row.components["intelligence_rank"]),
                row.id,
            ),
        ),
        sorted(
            eligible,
            key=lambda row: (
                -float(row.components["task_fit"]),
                -float(row.score),
                row.id,
            ),
        ),
        sorted(
            eligible,
            key=lambda row: (
                -float(row.components["value_index"]),
                float(row.blended_price_per_million or math.inf),
                row.id,
            ),
        ),
        sorted(
            eligible,
            key=lambda row: (
                float(row.blended_price_per_million or math.inf),
                -float(row.components["task_fit"]),
                row.id,
            ),
        ),
        sorted(
            eligible,
            key=lambda row: (
                -float(row.components["context_fit"]),
                -float(row.score),
                row.id,
            ),
        ),
    ]
    selected: list[Any] = []
    seen: set[str] = set()
    index = 0
    while len(selected) < limit and any(
        index < len(channel) for channel in channels
    ):
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


def _runtime_from_args(
    args: argparse.Namespace,
    run: Any,
) -> ProductionRuntime:
    candidate_ceiling = int(
        args.maximum_candidates_per_work
        or ABSOLUTE_CANDIDATES_PER_WORK_CEILING
    )
    if not 2 <= candidate_ceiling <= ABSOLUTE_CANDIDATES_PER_WORK_CEILING:
        raise ValueError(
            "maximum-candidates-per-work must be between 2 and "
            f"{ABSOLUTE_CANDIDATES_PER_WORK_CEILING}"
        )
    config = RuntimeConfig(
        total_call_limit=int(args.maximum_total_calls),
        recovery_call_limit=int(args.maximum_recovery_calls),
        cost_anomaly_usd=args.cost_anomaly_usd,
        quality_tier=str(run.quality_tier or "value"),
        tools_allowed=False,
        live_catalog_required=bool(args.require_live_catalog),
        provider_lock_required=True,
        maximum_candidates_per_work=candidate_ceiling,
        solver_timeout_seconds=max(
            1.0,
            float(args.solver_timeout_seconds),
        ),
    )
    return build_production_runtime(config)


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
    return (
        config.total_call_limit,
        config.recovery_call_limit,
        config.initial_call_limit,
        config.cost_anomaly_usd,
    )


def _resource_shape(
    resources: Mapping[str, Any],
) -> dict[str, int | bool]:
    interpretations = [
        row
        for row in resources.get("interpretations", [])
        if isinstance(row, Mapping)
    ]
    work_counts = [
        len(
            [
                item
                for item in row.get("atomic_work", [])
                if isinstance(item, Mapping)
            ]
        )
        for row in interpretations
    ]
    synthesis_counts = [
        sum(
            1
            for item in row.get("atomic_work", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("operation_requirements"), Mapping)
            and float(
                item.get("operation_requirements", {}).get("synthesis", 0.0)
                or 0.0
            )
            > 0.0
        )
        for row in interpretations
    ]
    signals = resources.get("task_signals", {})
    signals = signals if isinstance(signals, Mapping) else {}
    structural = signals.get("structural_signals", {})
    structural = structural if isinstance(structural, Mapping) else {}
    return {
        "maximum_atomic_work": max(work_counts or [1]),
        "maximum_synthesis_work": max(synthesis_counts or [0]),
        "interpretation_count": max(1, len(interpretations)),
        "explicit_contract_items": int(
            structural.get("explicit_contract_items", 0) or 0
        ),
        "explicit_output_contract": bool(
            structural.get("explicit_output_contract")
        ),
        "independence_markers": int(
            structural.get("independence_markers", 0) or 0
        ),
    }


def _delivery_limits(
    task: str,
    profile: Any,
    shape: Mapping[str, int | bool],
) -> tuple[float, int, bool, dict[str, Any]]:
    authorized = bool(_DEGRADED_AUTHORIZATION_RE.search(task))
    strict = bool(profile.high_stakes or shape["explicit_output_contract"])
    if strict:
        coverage = 1.0
        allow = False
    elif authorized:
        breadth = max(1, int(shape["maximum_atomic_work"]))
        coverage = max(0.75, 1.0 - 1.0 / (breadth + 1.0))
        allow = True
    else:
        coverage = 1.0
        allow = False
    min_nodes = (
        1
        if int(shape["maximum_atomic_work"]) <= 1
        else min(int(shape["maximum_atomic_work"]), 2)
    )
    return coverage, min_nodes, allow, {
        "user_authorized_degradation": authorized,
        "high_stakes": bool(profile.high_stakes),
        "explicit_output_contract": bool(
            shape["explicit_output_contract"]
        ),
        "policy": "task-risk-authorization-derived",
    }


def _planning_limits(
    *,
    total_calls: int,
    recovery_calls: int,
    planning_nodes: int,
    anomaly_budget: float | None,
    runtime: ProductionRuntime,
    task: str = "",
    profile: Any | None = None,
    resource_shape: Mapping[str, int | bool] | None = None,
) -> GraphLimits:
    shape = resource_shape or {
        "maximum_atomic_work": 1,
        "explicit_output_contract": False,
    }
    fallback_profile = type(
        "Profile",
        (),
        {"high_stakes": False},
    )()
    coverage, min_nodes, allow, _ = _delivery_limits(
        task,
        profile or fallback_profile,
        shape,
    )
    max_nodes = max(
        1,
        min(planning_nodes, total_calls - recovery_calls),
    )
    synthesis_slots = min(
        max(0, max_nodes - 1),
        1 if int(shape.get("maximum_synthesis_work", 0) or 0) > 0 else 0,
    )
    maximum_content_nodes = max(1, max_nodes - synthesis_slots)
    effective_min_nodes = min(int(min_nodes), maximum_content_nodes)
    return GraphLimits(
        max_nodes=max_nodes,
        max_edges=64,
        max_stages=8,
        max_model_calls=total_calls,
        max_retries=recovery_calls,
        max_replacements=recovery_calls,
        max_budget_usd=anomaly_budget,
        min_required_work_coverage=coverage,
        min_successful_content_nodes=effective_min_nodes,
        allow_degraded_success=allow,
        cost_risk_multiplier=runtime.config.cost_risk_multiplier,
    )


def _search_schedule(
    shape: Mapping[str, int | bool],
    runtime: ProductionRuntime,
    ranking_ceiling: int,
) -> list[tuple[int, int]]:
    work = max(1, int(shape["maximum_atomic_work"]))
    contract = max(0, int(shape["explicit_contract_items"]))
    independence = max(0, int(shape["independence_markers"]))
    company_need = min(
        runtime.config.total_call_limit,
        max(2, work, 1 + independence),
    )
    initial_rank = max(
        10,
        4 * company_need,
        3 * work,
        2 * min(contract, 24),
    )
    initial_rank = min(ranking_ceiling, initial_rank)
    initial_per_work = max(
        4,
        company_need + runtime.config.recovery_call_limit,
    )
    initial_per_work = min(
        runtime.config.maximum_candidates_per_work,
        initial_per_work,
    )

    schedule: list[tuple[int, int]] = []
    rank = initial_rank
    per_work = initial_per_work
    while True:
        schedule.append((rank, per_work))
        if (
            rank >= ranking_ceiling
            and per_work >= runtime.config.maximum_candidates_per_work
        ):
            break
        next_rank = min(
            ranking_ceiling,
            max(rank + 1, int(math.ceil(rank * 1.6))),
        )
        next_per_work = min(
            runtime.config.maximum_candidates_per_work,
            max(per_work + 1, int(math.ceil(per_work * 1.5))),
        )
        if (next_rank, next_per_work) == (rank, per_work):
            break
        rank, per_work = next_rank, next_per_work
    return schedule


def _optimization_value(optimization: Mapping[str, Any]) -> float:
    graph = optimization.get("execution_graph", {})
    graph = graph if isinstance(graph, Mapping) else {}
    quality = max(
        0.0,
        float(graph.get("estimated_quality", 0.0) or 0.0),
    )
    cost = max(
        0.0,
        float(graph.get("estimated_total_cost", 0.0) or 0.0),
    )
    return quality / (0.0001 + cost)


def _annotate_market(
    compiled_market: dict[str, Any],
    *,
    ranked: Sequence[Any],
    catalog_source: str,
    endpoint_source: str,
    catalog_snapshot_id: str,
    search_trace: Sequence[Mapping[str, Any]],
) -> None:
    companies = [candidate_company(model) for model in ranked]
    compiled_market.update(
        {
            "catalog_source": catalog_source,
            "endpoint_source": endpoint_source,
            "catalog_snapshot_id": catalog_snapshot_id,
            "candidate_pool_policy": (
                "task-adaptive-multi-channel-deduplicated"
            ),
            "candidate_pool_expansion_policy": (
                "feasibility-and-marginal-value-driven"
            ),
            "model_company_policy": "task-global-all-different",
            "ranked_model_count": len(ranked),
            "ranked_company_count": len(set(companies)),
            "endpoint_fetch_policy": "bounded-current-run-only",
            "search_trace": list(search_trace),
            "cross_task_history_used": False,
        }
    )
    compiled_market["ranked_models"] = [
        {
            "rank": index,
            "model": model.id,
            "model_company": candidate_company(model),
            "official_intelligence_rank": (model.ranks or {}).get(
                "intelligence-high-to-low"
            ),
            "prompt_usd_per_million": model.prompt_price_per_million,
            "completion_usd_per_million": (
                model.completion_price_per_million
            ),
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
    ranking_ceiling = int(
        args.ranking_limit
        or min(
            DEFAULT_INTELLIGENCE_RANKING_LIMIT,
            run.ranking_limit,
        )
    )
    ranking_ceiling = max(
        5,
        min(
            DEFAULT_INTELLIGENCE_RANKING_LIMIT,
            ranking_ceiling,
        ),
    )
    run = replace(run, ranking_limit=ranking_ceiling)
    runtime = runtime or _runtime_from_args(args, run)
    output = Path(run.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (
        total_calls,
        recovery_calls,
        planning_nodes,
        anomaly_budget,
    ) = _validated_budget(args, run, runtime)
    _write_json(output / "v5-runtime-config.json", runtime.describe())

    profile = classify_production_task(run.task, run)
    models, catalog_source = market.fetch_catalog(run)
    all_ranked = _rank_v5_models(models, profile, run)
    resources = compile_v5_task_resources(
        profile,
        run,
        semantic_compiler=compile_production_task_semantics,
    )
    write_task_resource_artifacts(resources, output)
    shape = _resource_shape(resources)
    limits = _planning_limits(
        total_calls=total_calls,
        recovery_calls=recovery_calls,
        planning_nodes=planning_nodes,
        anomaly_budget=anomaly_budget,
        runtime=runtime,
        task=run.task,
        profile=profile,
        resource_shape=shape,
    )
    _, _, _, delivery_decision = _delivery_limits(
        run.task,
        profile,
        shape,
    )

    endpoint_fixture = (
        _load_json(args.endpoint_file) if args.endpoint_file else None
    )
    schedule = _search_schedule(
        shape,
        runtime,
        min(ranking_ceiling, len(all_ranked)),
    )
    trace: list[dict[str, Any]] = []
    incumbent: dict[str, Any] | None = None
    incumbent_value = -math.inf
    final_market: dict[str, Any] | None = None
    final_candidates: dict[str, Any] | None = None
    last_candidates: dict[str, Any] | None = None
    final_snapshot: Any = None
    final_ranked: list[Any] = []
    final_endpoint_source = ""
    last_error: Exception | None = None

    for attempt_index, (rank_width, per_work) in enumerate(schedule, 1):
        ranked = all_ranked[:rank_width]
        if endpoint_fixture is not None:
            endpoint_payloads = endpoint_fixture
            endpoint_source = f"fixture:{args.endpoint_file}"
            allow_synthetic = False
        elif run.dry_run and run.catalog_file:
            endpoint_payloads = {}
            endpoint_source = (
                "synthetic-fixture-derived-from-model-catalog"
            )
            allow_synthetic = True
        else:
            endpoint_payloads = fetch_live_endpoint_payloads(
                ranked,
                run,
                maximum_models=rank_width,
            )
            endpoint_source = (
                "openrouter-live-model-endpoints-bounded-current-run"
            )
            allow_synthetic = False

        snapshot = runtime.build_catalog_snapshot(
            ranked,
            endpoint_payloads,
            catalog_source=catalog_source,
            endpoint_source=endpoint_source,
        )
        try:
            compiled_market = runtime.planner_policy.compile_market(
                ranked,
                resources,
                endpoint_payloads=snapshot.endpoint_payloads,
                ranking_limit=rank_width,
                allow_synthetic_fixture=allow_synthetic,
            )
            candidate_graph = (
                runtime.planner_policy.generate_candidate_graph(
                    resources,
                    compiled_market,
                    maximum_per_group=per_work,
                )
            )
            last_candidates = candidate_graph
            optimization = (
                runtime.planner_policy.optimize_execution_graph(
                    candidate_graph,
                    limits=limits,
                    quality_tolerance_pct=max(
                        0.0,
                        min(
                            20.0,
                            float(args.quality_tolerance_pct),
                        ),
                    ),
                    solver_timeout_seconds=(
                        runtime.config.solver_timeout_seconds
                    ),
                )
            )
            value = _optimization_value(optimization)
            improvement = (
                None
                if incumbent is None
                else (value - incumbent_value)
                / max(abs(incumbent_value), 1e-9)
            )
            trace.append(
                {
                    "attempt": attempt_index,
                    "ranking_width": rank_width,
                    "candidates_per_work": per_work,
                    "status": "feasible",
                    "objective_value_proxy": value,
                    "marginal_improvement_ratio": improvement,
                }
            )
            if incumbent is None or value > incumbent_value:
                incumbent = optimization
                incumbent_value = value
                final_market = compiled_market
                final_candidates = candidate_graph
                final_snapshot = snapshot
                final_ranked = ranked
                final_endpoint_source = endpoint_source
            if improvement is not None and improvement <= 0.01:
                break
        except Exception as exc:  # noqa: BLE001 - search evidence
            last_error = exc
            trace.append(
                {
                    "attempt": attempt_index,
                    "ranking_width": rank_width,
                    "candidates_per_work": per_work,
                    "status": "infeasible",
                    "message": str(exc),
                }
            )

    _write_json(
        output / "v5-adaptive-search.json",
        {
            "policy": "task-shape-feasibility-marginal-value",
            "resource_shape": shape,
            "delivery_decision": delivery_decision,
            "ranking_emergency_ceiling": ranking_ceiling,
            "candidate_emergency_ceiling_per_work": (
                runtime.config.maximum_candidates_per_work
            ),
            "attempts": trace,
            "cross_task_history_used": False,
        },
    )

    if (
        incumbent is None
        or final_market is None
        or final_candidates is None
        or final_snapshot is None
    ):
        report = build_infeasibility_report(
            last_candidates
            or final_candidates
            or {"candidates": [], "interpretations": {}},
            limits,
            message=str(
                last_error
                or "adaptive search exhausted without a feasible graph"
            ),
        )
        report["adaptive_search_trace"] = trace
        _write_json(output / "v5-planning-infeasibility.json", report)
        write_manifest(output)
        raise market.ExpertTeamError(
            f"{report['code']}: {report['message']}"
        )

    _annotate_market(
        final_market,
        ranked=final_ranked,
        catalog_source=catalog_source,
        endpoint_source=final_endpoint_source,
        catalog_snapshot_id=final_snapshot.snapshot_id,
        search_trace=trace,
    )
    _write_json(
        output / "catalog-snapshot.json",
        final_snapshot.to_dict(),
    )
    _write_json(output / "v5-model-endpoint-market.json", final_market)
    _write_json(output / "v5-candidate-graph.json", final_candidates)

    optimization = dict(incumbent)
    optimization["approved_budget"] = {
        "maximum_total_calls": total_calls,
        "maximum_recovery_calls": recovery_calls,
        "maximum_initial_calls": runtime.config.initial_call_limit,
        "planning_node_ceiling": planning_nodes,
        "planning_node_policy": "optimizer-decides-within-call-budget",
        "cost_anomaly_usd": anomaly_budget,
        "quality_tier": run.quality_tier,
        "ranking_emergency_ceiling": ranking_ceiling,
        "selected_ranking_width": len(final_ranked),
        "candidate_emergency_ceiling_per_work": (
            runtime.config.maximum_candidates_per_work
        ),
        "delivery_policy": delivery_decision,
        "model_company_policy": "task-global-all-different",
        "runtime_config_sha256": sha256(
            json.dumps(
                runtime.config.to_dict(),
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest(),
    }
    optimization["catalog_snapshot_id"] = final_snapshot.snapshot_id
    planner = {
        "version": 5,
        "market": final_market,
        "candidate_graph": final_candidates,
        "optimization": optimization,
    }
    _write_json(output / "v5-optimization.json", optimization)
    _write_json(
        output / "v5-execution-graph.json",
        optimization["execution_graph"],
    )

    benchmark = planning_benchmark(planner)
    write_benchmark(output / "v5-planning-benchmark.json", benchmark)
    graph = ExecutionGraph.from_mapping(
        optimization["execution_graph"]
    )

    if run.dry_run:
        requests = [
            runtime.build_node_payload(node, run.task, [])
            for node in graph.nodes
        ]
        _write_json(
            output / "v5-dry-run.json",
            {
                "version": 5,
                "status": "planned-not-executed",
                "execution_graph": graph.to_dict(),
                "requests": requests,
                "planning_benchmark": benchmark,
                "production_entrypoint_changed": False,
                "legacy_runtime_present": False,
                "fallback_policy": "fail-closed-no-alternate-runtime",
                "approved_budget": optimization["approved_budget"],
                "catalog_snapshot_id": final_snapshot.snapshot_id,
                "runtime_version": runtime.describe()["runtime_version"],
                "model_company_policy": "task-global-all-different",
                "adaptive_search_attempts": trace,
                "global_monkey_patching": False,
            },
        )
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
    print(
        f"V5 execution completed: {output / 'v5-final-report.md'}"
    )
    return 0


if __name__ == "__main__":
    from v5_constitutional_pipeline import main as constitutional_main

    raise SystemExit(constitutional_main())
