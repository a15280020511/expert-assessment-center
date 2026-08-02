#!/usr/bin/env python3
"""Production V5 pipeline with structured constraints and dynamic optimization."""
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
    TaskConstraints,
    compile_task_constraints,
    dynamic_objective_weights,
)


def _normalized(raw: Mapping[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(value)) for value in raw.values())
    if total <= 0:
        raise ValueError("dynamic weight vector must contain positive mass")
    return {
        key: max(0.0, float(value)) / total
        for key, value in raw.items()
    }


def _task_fit_feature_weights(profile: Any) -> dict[str, float]:
    """Derive model-task-fit feature weights from the current task profile."""
    complexity = max(
        0.0,
        min(7.0, float(getattr(profile, "complexity_score", 0) or 0)),
    )
    high_stakes = 1.0 if bool(getattr(profile, "high_stakes", False)) else 0.0
    chinese = 1.0 if bool(getattr(profile, "chinese", False)) else 0.0
    domains = list(getattr(profile, "domains", []) or [])
    cross_domain = min(2.0, max(0.0, float(len(domains) - 1)))
    return _normalized(
        {
            "primary_domain": 1.0 + complexity / 7.0,
            "secondary_domain": 0.5 + cross_domain / 2.0,
            "reasoning_capability": 0.5 + complexity / 3.5,
            "structured_output": 0.5 + 1.5 * high_stakes,
            "language_fit": 0.25 + 0.75 * chinese,
        }
    )


def _description_fit(model: Any, domain: str, policy: Mapping[str, Any]) -> float:
    descriptions = policy.get("description_terms", {})
    if not isinstance(descriptions, Mapping):
        return 0.0
    terms = descriptions.get(domain, descriptions.get("general", []))
    if not isinstance(terms, list) or not terms:
        return 0.0
    text = " ".join(
        (
            str(getattr(model, "id", "")),
            str(getattr(model, "name", "")),
            str(getattr(model, "description", "")),
        )
    ).casefold()
    matches = sum(1 for term in terms if str(term).casefold() in text)
    return min(1.0, matches / max(1.0, math.sqrt(len(terms))))


def _dynamic_task_fit(
    model: Any,
    profile: Any,
) -> tuple[float, list[str], dict[str, float], dict[str, float]]:
    """Score task fit without a fixed feature-weight tuple."""
    policy = core.market.load_json(core.market.POLICY_FILE)
    primary = _description_fit(model, profile.primary_domain, policy)
    secondary = _description_fit(model, profile.secondary_domain, policy)
    supported = {
        str(value).casefold()
        for value in (getattr(model, "supported_parameters", []) or [])
    }
    reasoning = float(
        "reasoning" in supported
        or bool(getattr(model, "reasoning", {}) or {})
    )
    structured = float(
        bool(
            supported.intersection(
                {
                    "structured_outputs",
                    "response_format",
                    "json_schema",
                }
            )
        )
    )
    model_text = " ".join(
        (
            str(getattr(model, "id", "")),
            str(getattr(model, "name", "")),
            str(getattr(model, "description", "")),
        )
    ).casefold()
    if bool(getattr(profile, "chinese", False)):
        language = float(
            any(
                marker in model_text
                for marker in ("chinese", "中文", "multilingual", "多语言")
            )
        )
    else:
        language = 1.0

    features = {
        "primary_domain": primary,
        "secondary_domain": secondary,
        "reasoning_capability": reasoning,
        "structured_output": structured,
        "language_fit": language,
    }
    weights = _task_fit_feature_weights(profile)
    score = sum(weights[key] * features[key] for key in weights)
    reasons = [
        f"dynamic-task-fit:{key}={features[key]:.4f},weight={weights[key]:.4f}"
        for key in weights
        if features[key] > 0
    ]
    return max(0.0, min(1.0, score)), reasons, weights, features


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
        if intelligence_rank > int(run.ranking_limit):
            continue
        fit, reasons, feature_weights, feature_values = _dynamic_task_fit(
            model,
            profile,
        )
        price = float(model.blended_price_per_million or math.inf)
        context_fit = min(
            1.0,
            int(model.context_length)
            / max(1, int(profile.requested_context) * 4),
        )
        model.components = {
            "intelligence_rank": intelligence_rank,
            "task_fit": fit,
            "task_fit_feature_weights": feature_weights,
            "task_fit_feature_values": feature_values,
            "price": price,
            "context_fit": context_fit,
        }
        model.fit_reasons = list(reasons)
        eligible.append(model)

    if len(eligible) < 2:
        raise core.market.ExpertTeamError(
            "V5 requires at least two eligible direct models in the admitted "
            "intelligence pool."
        )

    best_rank = min(
        int(row.components["intelligence_rank"])
        for row in eligible
    )
    worst_rank = max(
        int(row.components["intelligence_rank"])
        for row in eligible
    )
    finite_prices = [
        float(row.components["price"])
        for row in eligible
        if math.isfinite(float(row.components["price"]))
    ]
    min_price = min(finite_prices or [1.0])
    max_price = max(finite_prices or [1.0])
    task = str(getattr(run, "task", "") or "")
    weights = dynamic_objective_weights(profile, task)

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
        quality_weight = weights["intelligence"] + weights["task_fit"]
        quality_estimate = (
            weights["intelligence"] * intelligence
            + weights["task_fit"] * float(model.components["task_fit"])
        ) / max(quality_weight, 1e-12)
        price_pressure = weights["value"] / max(
            weights["value"] + quality_weight,
            1e-12,
        )
        value = max(
            0.0,
            min(
                1.0,
                quality_estimate
                * price_score ** (0.5 + price_pressure),
            ),
        )
        model.components.update(
            {
                "intelligence": intelligence,
                "price_score": price_score,
                "value_index": value,
                "value_quality_estimate": quality_estimate,
                "value_price_pressure": price_pressure,
                "objective_weights": dict(weights),
                "weight_policy": "task-derived-normalized",
            }
        )
        model.score = sum(
            (
                weights["intelligence"] * intelligence,
                weights["task_fit"]
                * float(model.components["task_fit"]),
                weights["value"] * value,
                weights["context"]
                * float(model.components["context_fit"]),
            )
        )
        model.fit_reasons.append(
            "V5 dynamic preselection: objective and task-fit feature weights "
            "are derived from the current task"
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
    strict = bool(
        profile.high_stakes
        or shape["explicit_output_contract"]
        or not constraints.external_facts_allowed
    )
    authorized = bool(constraints.allow_degraded_success and not strict)
    breadth = max(1, int(shape["maximum_atomic_work"]))
    complexity = max(0, min(7, int(profile.complexity_score)))
    if authorized:
        structural_coverage = 1.0 - 1.0 / (breadth + 1.0)
        risk_coverage = 0.5 + complexity / 20.0
        coverage = min(0.95, max(0.5, structural_coverage, risk_coverage))
    else:
        coverage = 1.0
    min_nodes = max(
        1,
        min(
            breadth,
            int(math.ceil(math.log2(breadth + 1))),
        ),
    )
    return coverage, min_nodes, authorized, {
        "user_authorized_degradation": constraints.allow_degraded_success,
        "effective_degradation_authorized": authorized,
        "degradation_authorization": constraints.degradation_authorization,
        "high_stakes": bool(profile.high_stakes),
        "explicit_output_contract": bool(shape["explicit_output_contract"]),
        "closed_world": not constraints.external_facts_allowed,
        "required_work_coverage": coverage,
        "minimum_successful_content_nodes": min_nodes,
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
    coverage, min_nodes, allow, _ = _delivery_limits(
        task,
        profile,
        resource_shape,
    )
    max_nodes = max(1, min(planning_nodes, total_calls - recovery_calls))
    synthesis_slots = min(
        max(0, max_nodes - 1),
        1
        if int(resource_shape.get("maximum_synthesis_work", 0) or 0) > 0
        else 0,
    )
    maximum_content_nodes = max(1, max_nodes - synthesis_slots)
    effective_min_nodes = min(int(min_nodes), maximum_content_nodes)
    max_edges = min(64, max_nodes * max(0, max_nodes - 1) // 2)
    breadth = max(1, int(resource_shape["maximum_atomic_work"]))
    max_stages = min(
        8,
        max(1, int(math.ceil(math.log2(breadth + 1))) + 1),
    )
    return GraphLimits(
        max_nodes=max_nodes,
        max_edges=max_edges,
        max_stages=max_stages,
        max_model_calls=total_calls,
        max_retries=recovery_calls,
        max_replacements=recovery_calls,
        max_budget_usd=anomaly_budget,
        min_required_work_coverage=coverage,
        min_successful_content_nodes=effective_min_nodes,
        allow_degraded_success=allow,
        cost_risk_multiplier=runtime.config.cost_risk_multiplier,
    )


def _dynamic_quality_tolerance(
    profile: Any,
    constraints: TaskConstraints,
    shape: Mapping[str, int | bool],
    platform_ceiling: float,
) -> float:
    complexity = max(0.0, min(7.0, float(profile.complexity_score)))
    strictness = (
        2.0 * float(bool(profile.high_stakes))
        + 1.5 * float(not constraints.external_facts_allowed)
        + float(bool(shape["explicit_output_contract"]))
    )
    task_tolerance = max(0.0, 12.0 - 1.25 * complexity - 2.0 * strictness)
    return max(0.0, min(20.0, float(platform_ceiling), task_tolerance))


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
    total_calls, recovery_calls, planning_nodes, anomaly_budget = (
        core._validated_budget(args, run, runtime)
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
    _, requested_minimum_content_nodes, _, delivery_decision = (
        _delivery_limits(
            run.task,
            profile,
            shape,
        )
    )
    reserved_synthesis_slots = min(
        max(0, int(limits.max_nodes) - 1),
        1 if int(shape.get("maximum_synthesis_work", 0) or 0) > 0 else 0,
    )
    maximum_plannable_content_nodes = max(
        1,
        int(limits.max_nodes) - reserved_synthesis_slots,
    )
    delivery_decision = {
        **dict(delivery_decision),
        "requested_minimum_successful_content_nodes": int(
            requested_minimum_content_nodes
        ),
        "reserved_synthesis_slots": reserved_synthesis_slots,
        "maximum_plannable_content_nodes": maximum_plannable_content_nodes,
        "minimum_successful_content_nodes": int(
            limits.min_successful_content_nodes
        ),
        "minimum_node_policy": (
            "task-derived-clamped-to-initial-call-content-capacity"
        ),
    }
    quality_tolerance = _dynamic_quality_tolerance(
        profile,
        constraints,
        shape,
        float(args.quality_tolerance_pct),
    )

    endpoint_fixture = (
        core._load_json(args.endpoint_file)
        if args.endpoint_file
        else None
    )
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
            candidate_graph = runtime.planner_policy.generate_candidate_graph(
                resources,
                compiled_market,
                maximum_per_group=per_work,
            )
            last_candidates = candidate_graph
            optimization = runtime.planner_policy.optimize_execution_graph(
                candidate_graph,
                limits=limits,
                quality_tolerance_pct=quality_tolerance,
                solver_timeout_seconds=(
                    runtime.config.solver_timeout_seconds
                ),
            )
            value = core._optimization_value(optimization)
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
            "dynamic_quality_tolerance_pct": quality_tolerance,
            "quality_tolerance_platform_ceiling_pct": float(
                args.quality_tolerance_pct
            ),
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
        report = core.build_infeasibility_report(
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
        core._write_json(output / "v5-planning-infeasibility.json", report)
        write_manifest(output)
        raise core.market.ExpertTeamError(
            f"{report['code']}: {report['message']}"
        )

    core._annotate_market(
        final_market,
        ranked=final_ranked,
        catalog_source=catalog_source,
        endpoint_source=final_endpoint_source,
        catalog_snapshot_id=final_snapshot.snapshot_id,
        search_trace=trace,
    )
    final_market["preselection_objective_weights"] = (
        dynamic_objective_weights(profile, run.task)
    )
    final_market["task_fit_feature_weights"] = (
        _task_fit_feature_weights(profile)
    )
    final_market["fixed_preselection_weight_tuple_used"] = False
    final_market["fixed_task_fit_weight_tuple_used"] = False
    final_market["preselection_policy"] = (
        "task-derived-normalized-objective"
    )
    final_market["task_fit_policy"] = (
        "task-profile-derived-normalized-features"
    )
    core._write_json(
        output / "catalog-snapshot.json",
        final_snapshot.to_dict(),
    )
    core._write_json(
        output / "v5-model-endpoint-market.json",
        final_market,
    )
    core._write_json(
        output / "v5-candidate-graph.json",
        final_candidates,
    )

    optimization = dict(incumbent)
    optimization["approved_budget"] = {
        "maximum_total_calls": total_calls,
        "maximum_recovery_calls": recovery_calls,
        "maximum_initial_calls": runtime.config.initial_call_limit,
        "planning_node_ceiling": planning_nodes,
        "planning_node_policy": "optimizer-decides-within-call-budget",
        "cost_anomaly_usd": anomaly_budget,
        "quality_tier": run.quality_tier,
        "quality_tier_policy": "compatibility-label-not-fixed-weight-tuple",
        "dynamic_quality_tolerance_pct": quality_tolerance,
        "ranking_emergency_ceiling": ranking_ceiling,
        "selected_ranking_width": len(final_ranked),
        "candidate_emergency_ceiling_per_work": (
            runtime.config.maximum_candidates_per_work
        ),
        "delivery_policy": delivery_decision,
        "model_company_policy": "task-global-all-different",
        "dynamic_preselection_weights": dynamic_objective_weights(
            profile,
            run.task,
        ),
        "dynamic_task_fit_feature_weights": (
            _task_fit_feature_weights(profile)
        ),
        "runtime_config_sha256": sha256(
            core.json.dumps(
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
    core._write_json(output / "v5-optimization.json", optimization)
    core._write_json(
        output / "v5-execution-graph.json",
        optimization["execution_graph"],
    )
    benchmark = core.planning_benchmark(planner)
    core.write_benchmark(
        output / "v5-planning-benchmark.json",
        benchmark,
    )
    graph = ExecutionGraph.from_mapping(
        optimization["execution_graph"]
    )

    if run.dry_run:
        requests = [
            runtime.build_node_payload(node, run.task, [])
            for node in graph.nodes
        ]
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
    print(
        f"V5 execution completed: {output / 'v5-final-report.md'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
