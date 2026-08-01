#!/usr/bin/env python3
"""Production V5 pipeline with structured constraints and dynamic preselection."""
from __future__ import annotations

import math
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_pipeline as core
from artifact_manifest import write_manifest
from execution_graph import ExecutionGraph, GraphLimits
from v5_model_company import candidate_company
from v5_task_constraints import (
    compile_task_constraints,
    dynamic_objective_weights,
)


def _rank_v5_models(
    models: Mapping[str, Any],
    profile: Any,
    run: Any,
) -> list[Any]:
    """Select the current-task pool using task-derived normalized weights."""
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
        if core.market._expired(getattr(model, "expiration_date", None)):
            continue
        if int(getattr(model, "context_length", 0) or 0) < int(profile.requested_context):
            continue
        if int(getattr(model, "max_completion_tokens", 0) or 0) <= 0:
            continue
        if getattr(model, "input_modalities", None) and "text" not in model.input_modalities:
            continue
        if getattr(model, "output_modalities", None) and "text" not in model.output_modalities:
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
        if intelligence_rank > int(run.ranking_limit):
            continue
        fit, reasons = core.market._task_fit(model, profile)
        price = float(model.blended_price_per_million or math.inf)
        context_fit = min(
            1.0,
            int(model.context_length) / max(1, int(profile.requested_context) * 4),
        )
        model.components = {
            "intelligence_rank": intelligence_rank,
            "task_fit": max(0.0, min(1.0, float(fit))),
            "price": price,
            "context_fit": context_fit,
        }
        model.fit_reasons = list(reasons)
        eligible.append(model)

    if len(eligible) < 2:
        raise core.market.ExpertTeamError(
            "V5 requires at least two eligible direct models in the admitted intelligence pool."
        )

    best_rank = min(int(row.components["intelligence_rank"]) for row in eligible)
    worst_rank = max(int(row.components["intelligence_rank"]) for row in eligible)
    finite_prices = [
        float(row.components["price"])
        for row in eligible
        if math.isfinite(float(row.components["price"]))
    ]
    min_price = min(finite_prices or [1.0])
    max_price = max(finite_prices or [1.0])
    weights = dynamic_objective_weights(profile, str(getattr(run, "task", "") or ""))

    for model in eligible:
        rank = int(model.components["intelligence_rank"])
        intelligence = (
            1.0
            if worst_rank == best_rank
            else 1.0 - (rank - best_rank) / (worst_rank - best_rank)
        )
        price = float(model.components["price"])
        price_score = (
            1.0
            if max_price == min_price
            else 1.0 - (price - min_price) / (max_price - min_price)
        )
        value = max(
            0.0,
            min(
                1.0,
                (intelligence + float(model.components["task_fit"]))
                / 2.0
                * price_score,
            ),
        )
        model.components.update(
            {
                "intelligence": intelligence,
                "value_index": value,
                "objective_weights": dict(weights),
                "weight_policy": "task-derived-normalized",
            }
        )
        model.score = sum(
            (
                weights["intelligence"] * intelligence,
                weights["task_fit"] * float(model.components["task_fit"]),
                weights["value"] * value,
                weights["context"] * float(model.components["context_fit"]),
            )
        )
        model.fit_reasons.append(
            "V5 dynamic preselection: weights derived from current task complexity, risk, scope and context"
        )

    ranked = sorted(
        eligible,
        key=lambda row: (
            -float(row.score),
            int(row.components["intelligence_rank"]),
            float(row.components["price"]),
            row.id,
        ),
    )
    limit = max(2, min(int(run.ranking_limit), len(ranked)))
    selected: list[Any] = []
    seen_models: set[str] = set()
    seen_companies: set[str] = set()
    for model in ranked:
        company = candidate_company(model)
        if company in seen_companies:
            continue
        selected.append(model)
        seen_models.add(model.id)
        seen_companies.add(company)
        if len(selected) >= limit:
            return selected
    for model in ranked:
        if model.id in seen_models:
            continue
        selected.append(model)
        seen_models.add(model.id)
        if len(selected) >= limit:
            break
    return selected


def _delivery_limits(
    task: str,
    profile: Any,
    shape: Mapping[str, int | bool],
) -> tuple[float, int, bool, dict[str, Any]]:
    constraints = compile_task_constraints(task)
    strict = bool(profile.high_stakes or shape["explicit_output_contract"])
    authorized = bool(constraints.allow_degraded_success and not strict)
    if authorized:
        breadth = max(1, int(shape["maximum_atomic_work"]))
        coverage = max(0.75, 1.0 - 1.0 / (breadth + 1.0))
    else:
        coverage = 1.0
    min_nodes = (
        1
        if int(shape["maximum_atomic_work"]) <= 1
        else min(int(shape["maximum_atomic_work"]), 2)
    )
    return coverage, min_nodes, authorized, {
        "user_authorized_degradation": constraints.allow_degraded_success,
        "effective_degradation_authorized": authorized,
        "degradation_authorization": constraints.degradation_authorization,
        "high_stakes": bool(profile.high_stakes),
        "explicit_output_contract": bool(shape["explicit_output_contract"]),
        "task_constraints": constraints.to_dict(),
        "policy": "explicit-deny-overrides-allow-default-deny",
    }


def _planning_limits(
    *,
    total_calls: int,
    recovery_calls: int,
    planning_nodes: int,
    anomaly_budget: float | None,
    runtime: Any,
    task: str,
    profile: Any,
    resource_shape: Mapping[str, int | bool],
) -> GraphLimits:
    coverage, min_nodes, allow, _ = _delivery_limits(task, profile, resource_shape)
    return GraphLimits(
        max_nodes=max(1, min(planning_nodes, total_calls - recovery_calls)),
        max_edges=64,
        max_stages=8,
        max_model_calls=total_calls,
        max_retries=recovery_calls,
        max_replacements=recovery_calls,
        max_budget_usd=anomaly_budget,
        min_required_work_coverage=coverage,
        min_successful_content_nodes=min_nodes,
        allow_degraded_success=allow,
        cost_risk_multiplier=runtime.config.cost_risk_multiplier,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime: Any | None = None,
) -> int:
    args = core.build_parser().parse_args(argv)
    run = core.market.build_run_config(args)
    ranking_ceiling = int(
        args.ranking_limit
        or min(core.DEFAULT_INTELLIGENCE_RANKING_LIMIT, run.ranking_limit)
    )
    ranking_ceiling = max(
        5,
        min(core.DEFAULT_INTELLIGENCE_RANKING_LIMIT, ranking_ceiling),
    )
    run = replace(run, ranking_limit=ranking_ceiling)
    runtime = runtime or core._runtime_from_args(args, run)
    output = Path(run.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    total_calls, recovery_calls, planning_nodes, anomaly_budget = core._validated_budget(
        args, run, runtime
    )
    core._write_json(output / "v5-runtime-config.json", runtime.describe())

    profile = core.classify_production_task(run.task, run)
    constraints = compile_task_constraints(run.task)
    core._write_json(output / "task-constraints.json", constraints.to_dict())
    models, catalog_source = core.market.fetch_catalog(run)
    all_ranked = _rank_v5_models(models, profile, run)
    resources = core.compile_v5_task_resources(
        profile,
        run,
        semantic_compiler=core.compile_production_task_semantics,
    )
    core.write_task_resource_artifacts(resources, output)
    shape = core._resource_shape(resources)
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
    _, _, _, delivery_decision = _delivery_limits(run.task, profile, shape)

    endpoint_fixture = core._load_json(args.endpoint_file) if args.endpoint_file else None
    schedule = core._search_schedule(
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
            endpoint_source = "synthetic-fixture-derived-from-model-catalog"
            allow_synthetic = True
        else:
            endpoint_payloads = core.fetch_live_endpoint_payloads(
                ranked,
                run,
                maximum_models=rank_width,
            )
            endpoint_source = "openrouter-live-model-endpoints-bounded-current-run"
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
            candidate_graph = runtime.planner_policy.generate_candidate_graph(
                resources,
                compiled_market,
                maximum_per_group=per_work,
            )
            last_candidates = candidate_graph
            optimization = runtime.planner_policy.optimize_execution_graph(
                candidate_graph,
                limits=limits,
                quality_tolerance_pct=max(
                    0.0,
                    min(20.0, float(args.quality_tolerance_pct)),
                ),
                solver_timeout_seconds=runtime.config.solver_timeout_seconds,
            )
            value = core._optimization_value(optimization)
            improvement = (
                None
                if incumbent is None
                else (value - incumbent_value) / max(abs(incumbent_value), 1e-9)
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
        except Exception as exc:  # noqa: BLE001
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

    core._write_json(
        output / "v5-adaptive-search.json",
        {
            "policy": "task-shape-feasibility-marginal-value",
            "resource_shape": shape,
            "delivery_decision": delivery_decision,
            "ranking_emergency_ceiling": ranking_ceiling,
            "candidate_emergency_ceiling_per_work": runtime.config.maximum_candidates_per_work,
            "attempts": trace,
            "cross_task_history_used": False,
        },
    )

    if incumbent is None or final_market is None or final_candidates is None or final_snapshot is None:
        report = core.build_infeasibility_report(
            last_candidates or final_candidates or {"candidates": [], "interpretations": {}},
            limits,
            message=str(last_error or "adaptive search exhausted without a feasible graph"),
        )
        report["adaptive_search_trace"] = trace
        core._write_json(output / "v5-planning-infeasibility.json", report)
        write_manifest(output)
        raise core.market.ExpertTeamError(f"{report['code']}: {report['message']}")

    core._annotate_market(
        final_market,
        ranked=final_ranked,
        catalog_source=catalog_source,
        endpoint_source=final_endpoint_source,
        catalog_snapshot_id=final_snapshot.snapshot_id,
        search_trace=trace,
    )
    final_market["preselection_objective_weights"] = dynamic_objective_weights(profile, run.task)
    final_market["fixed_preselection_weight_tuple_used"] = False
    final_market["preselection_policy"] = "task-derived-normalized-objective"
    core._write_json(output / "catalog-snapshot.json", final_snapshot.to_dict())
    core._write_json(output / "v5-model-endpoint-market.json", final_market)
    core._write_json(output / "v5-candidate-graph.json", final_candidates)

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
        "candidate_emergency_ceiling_per_work": runtime.config.maximum_candidates_per_work,
        "delivery_policy": delivery_decision,
        "model_company_policy": "task-global-all-different",
        "dynamic_preselection_weights": dynamic_objective_weights(profile, run.task),
        "runtime_config_sha256": sha256(
            core.json.dumps(runtime.config.to_dict(), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
    }
    optimization["catalog_snapshot_id"] = final_snapshot.snapshot_id
    planner = {
        "version": 5,
        "market": final_market,
        "candidate_graph": final_candidates,
        "optimization": optimization,
    }
    core._write_json(output / "v5-optimization.json", optimization)
    core._write_json(output / "v5-execution-graph.json", optimization["execution_graph"])
    benchmark = core.planning_benchmark(planner)
    core.write_benchmark(output / "v5-planning-benchmark.json", benchmark)
    graph = ExecutionGraph.from_mapping(optimization["execution_graph"])

    if run.dry_run:
        requests = [runtime.build_node_payload(node, run.task, []) for node in graph.nodes]
        core._write_json(
            output / "v5-dry-run.json",
            {
                "version": 5,
                "status": "planned-not-executed",
                "execution_graph": graph.to_dict(),
                "requests": requests,
                "planning_benchmark": benchmark,
                "production_entrypoint_changed": True,
                "legacy_runtime_present": False,
                "fallback_policy": "fail-closed-no-alternate-runtime",
                "approved_budget": optimization["approved_budget"],
                "catalog_snapshot_id": final_snapshot.snapshot_id,
                "runtime_version": runtime.describe()["runtime_version"],
                "model_company_policy": "task-global-all-different",
                "adaptive_search_attempts": trace,
                "global_monkey_patching": False,
                "task_constraints": constraints.to_dict(),
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
    core._write_json(output / "v5-result.json", result)
    write_manifest(output)
    print(f"V5 execution completed: {output / 'v5-final-report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
