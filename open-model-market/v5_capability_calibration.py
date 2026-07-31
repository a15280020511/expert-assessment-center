"""Calibrate sparse catalog capability proxies without inventing capabilities.

OpenRouter model descriptions are incomplete evidence, not exhaustive capability
registries. The original candidate factory converted missing keywords into low
scores and then applied demand-proportional hard thresholds. That could erase all
independent alternatives even for models that remain strongly ranked in the live
catalog.

This layer keeps the original static threshold whenever it yields enough distinct
models for the work's *explicit* model-independence requirement. When it does not,
it uses a rank-backed adaptive proxy threshold: context-compatible endpoints must
meet minimum benchmark quality and confidence, and the threshold is set by the
Nth distinct model actually required by the work, never below the catalog proxy
baseline. Scores and task demands are never modified. Context/output constraints
remain mandatory, and every calibration decision is recorded in the candidate
bundle.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_planner
from v5_planner import CandidateNode, V5PlanningError

STATIC_DEMAND_MULTIPLIER = 0.62
MIN_PROXY_CAPABILITY_FLOOR = 0.30
MIN_GENERAL_BENCHMARK_SCORE = 0.35
MIN_BENCHMARK_CONFIDENCE = 0.80
_INSTALLED = False
_ORIGINAL_GENERATOR = v5_planner.generate_candidate_graph


def _hard_thresholds(
    demand: Mapping[str, float],
    hard_labels: set[str],
) -> dict[str, float]:
    return {
        label: max(
            MIN_PROXY_CAPABILITY_FLOOR,
            STATIC_DEMAND_MULTIPLIER * float(demand.get(label, 0.0)),
        )
        for label in sorted(hard_labels)
    }


def _endpoint_fits_context(endpoint: Mapping[str, Any], work: Mapping[str, Any]) -> bool:
    context = work.get("context_requirements", {})
    required_context = int(context.get("required_context_tokens", 0) or 0)
    required_output = int(context.get("expected_output_tokens", 0) or 0)
    return bool(
        required_context <= int(endpoint.get("context_length", 0) or 0)
        and required_output <= int(endpoint.get("max_completion_tokens", 0) or 0)
    )


def _endpoint_meets(
    endpoint: Mapping[str, Any],
    thresholds: Mapping[str, float],
) -> bool:
    capabilities = endpoint.get("capability_scores", {})
    if not isinstance(capabilities, Mapping):
        return False
    return all(
        float(capabilities.get(label, 0.0)) + 1e-12 >= float(minimum)
        for label, minimum in thresholds.items()
    )


def _proxy_score(endpoint: Mapping[str, Any], hard_labels: set[str]) -> float:
    if not hard_labels:
        return 1.0
    capabilities = endpoint.get("capability_scores", {})
    if not isinstance(capabilities, Mapping):
        return 0.0
    return min(float(capabilities.get(label, 0.0)) for label in hard_labels)


def _rank_backed(endpoint: Mapping[str, Any]) -> bool:
    return bool(
        float(endpoint.get("benchmark_score", 0.0) or 0.0)
        + 1e-12
        >= MIN_GENERAL_BENCHMARK_SCORE
        and float(endpoint.get("benchmark_confidence", 0.0) or 0.0)
        + 1e-12
        >= MIN_BENCHMARK_CONFIDENCE
    )


def _distinct_models(endpoints: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted(
        {
            str(endpoint.get("model_id") or "")
            for endpoint in endpoints
            if endpoint.get("model_id")
        }
    )


def _adaptive_proxy_eligibility(
    endpoints: Sequence[Mapping[str, Any]],
    hard_labels: set[str],
    required_distinct_models: int,
) -> tuple[list[Mapping[str, Any]], float | None, list[dict[str, Any]]]:
    """Choose a market-observed proxy floor backed by live rank evidence.

    When fewer than the required number of models clear the baseline, retain the
    strongest baseline-qualified candidates for diagnostics and deterministic
    fail-closed solving. Do not erase the evidence set merely because it is
    insufficient.
    """
    eligible = [endpoint for endpoint in endpoints if _rank_backed(endpoint)]
    best_by_model: dict[str, tuple[float, float, Mapping[str, Any]]] = {}
    for endpoint in eligible:
        model = str(endpoint.get("model_id") or "")
        if not model:
            continue
        row = (
            _proxy_score(endpoint, hard_labels),
            float(endpoint.get("benchmark_score", 0.0) or 0.0),
            endpoint,
        )
        previous = best_by_model.get(model)
        if previous is None or row[:2] > previous[:2]:
            best_by_model[model] = row

    ranking = sorted(
        (
            {
                "model": model,
                "proxy_score": round(row[0], 6),
                "benchmark_score": round(row[1], 6),
            }
            for model, row in best_by_model.items()
        ),
        key=lambda row: (
            -float(row["proxy_score"]),
            -float(row["benchmark_score"]),
            str(row["model"]),
        ),
    )
    adaptive_floor = MIN_PROXY_CAPABILITY_FLOOR
    if len(ranking) >= required_distinct_models:
        adaptive_floor = max(
            MIN_PROXY_CAPABILITY_FLOOR,
            float(ranking[required_distinct_models - 1]["proxy_score"]),
        )
    selected = [
        endpoint
        for endpoint in eligible
        if _proxy_score(endpoint, hard_labels) + 1e-12 >= adaptive_floor
    ]
    return selected, adaptive_floor, ranking


def _eligibility_for_work(
    work: Mapping[str, Any],
    endpoints: Sequence[Mapping[str, Any]],
    demand: Mapping[str, float],
    hard_labels: set[str],
    required_copies: int,
) -> tuple[set[str], dict[str, Any]]:
    context_eligible = [
        endpoint for endpoint in endpoints if _endpoint_fits_context(endpoint, work)
    ]
    independence = work.get("independence_requirements", {})
    different_model_required = bool(
        isinstance(independence, Mapping)
        and independence.get("different_model_required")
    )
    required_distinct_models = required_copies if different_model_required else 1

    static_thresholds = _hard_thresholds(demand, hard_labels)
    static_eligible = [
        endpoint
        for endpoint in context_eligible
        if _endpoint_meets(endpoint, static_thresholds)
    ]
    static_models = _distinct_models(static_eligible)

    adaptive_eligible: list[Mapping[str, Any]] = []
    adaptive_floor: float | None = None
    rank_backed_models: list[dict[str, Any]] = []
    if len(static_models) < required_distinct_models:
        adaptive_eligible, adaptive_floor, rank_backed_models = (
            _adaptive_proxy_eligibility(
                context_eligible,
                hard_labels,
                required_distinct_models,
            )
        )

    adaptive_models = _distinct_models(adaptive_eligible)
    if len(static_models) >= required_distinct_models:
        selected = static_eligible
        status = "static-threshold-sufficient"
        calibrated = False
    elif len(adaptive_models) >= required_distinct_models:
        selected = adaptive_eligible
        status = "rank-backed-adaptive-proxy-calibrated"
        calibrated = True
    else:
        selected = adaptive_eligible
        status = "rank-backed-proxy-still-insufficient"
        calibrated = False

    selected_ids = {
        str(endpoint.get("endpoint_id"))
        for endpoint in selected
        if endpoint.get("endpoint_id")
    }
    audit = {
        "work_id": str(work.get("work_id") or ""),
        "required_execution_copies": required_copies,
        "different_model_required": different_model_required,
        "required_distinct_models": required_distinct_models,
        "hard_labels": sorted(hard_labels),
        "static_thresholds": {
            label: round(value, 6) for label, value in static_thresholds.items()
        },
        "proxy_capability_floor": MIN_PROXY_CAPABILITY_FLOOR,
        "minimum_general_benchmark_score": MIN_GENERAL_BENCHMARK_SCORE,
        "minimum_benchmark_confidence": MIN_BENCHMARK_CONFIDENCE,
        "adaptive_proxy_floor": (
            round(adaptive_floor, 6) if adaptive_floor is not None else None
        ),
        "context_eligible_endpoint_count": len(context_eligible),
        "static_eligible_endpoint_count": len(static_eligible),
        "static_eligible_model_count": len(static_models),
        "static_eligible_models": static_models,
        "rank_backed_model_ranking": rank_backed_models,
        "adaptive_eligible_endpoint_count": len(adaptive_eligible),
        "adaptive_eligible_model_count": len(adaptive_models),
        "adaptive_eligible_models": adaptive_models,
        "selected_eligible_endpoint_count": len(selected_ids),
        "selected_eligible_model_count": len(_distinct_models(selected)),
        "selected_eligible_models": _distinct_models(selected),
        "calibration_applied": calibrated,
        "calibration_status": status,
        "capability_scores_modified": False,
        "task_demands_modified": False,
        "catalog_description_proxy_treated_as_measured_benchmark": False,
        "rank_and_confidence_required_for_adaptive_calibration": True,
    }
    return selected_ids, audit


def generate_calibrated_candidate_graph(
    resource_bundle: Mapping[str, Any],
    market: Mapping[str, Any],
    *,
    maximum_per_group: int = 12,
    candidate_factory: Any | None = None,
    pruner: Any | None = None,
) -> dict[str, Any]:
    """Generate candidates with explicitly supplied policy functions."""
    candidate_factory = candidate_factory or v5_planner._candidate_for
    pruner = pruner or v5_planner.pareto_prune
    endpoints = [
        endpoint
        for endpoint in market.get("endpoints", [])
        if isinstance(endpoint, Mapping)
    ]
    all_candidates: list[CandidateNode] = []
    interpretation_meta: dict[str, Any] = {}
    calibration_by_interpretation: dict[str, Any] = {}

    for interpretation in resource_bundle.get("task_semantics", {}).get(
        "interpretations", []
    ):
        interpretation_id = str(interpretation["interpretation_id"])
        matrix = v5_planner._matrix_map(resource_bundle, interpretation_id)
        graph = v5_planner._graph_map(resource_bundle, interpretation_id)
        works = v5_planner._work_map(resource_bundle, interpretation_id)
        labels = list(matrix["capability_labels"])
        demand_by_work: dict[str, dict[str, float]] = {}
        hard_by_work: dict[str, set[str]] = {}
        copies_by_work: dict[str, int] = {}
        independence_policy_by_work: dict[str, dict[str, Any]] = {}
        stage_by_work = {
            work_id: index
            for index, stage in enumerate(graph.get("execution_stages", []))
            for work_id in stage
        }
        for row_index, row in enumerate(matrix["work_index"]):
            work_id = str(row["work_id"])
            demand_by_work[work_id] = {
                label: float(matrix["task_resource_matrix"][row_index][column])
                for column, label in enumerate(labels)
            }
            hard_by_work[work_id] = {
                label
                for column, label in enumerate(labels)
                if int(matrix["hard_requirement_matrix"][row_index][column]) == 1
            }
            copies_by_work[work_id] = max(
                1, int(row.get("minimum_independent_copies", 1))
            )

        eligible_endpoint_ids: dict[str, set[str]] = {}
        work_audits: list[dict[str, Any]] = []
        for work_id, work in works.items():
            independence = work.get("independence_requirements", {})
            policy = independence if isinstance(independence, Mapping) else {}
            independence_policy_by_work[work_id] = {
                "different_model_required": bool(
                    policy.get("different_model_required")
                ),
                "different_provider_required": False,
                "different_provider_preferred": bool(
                    policy.get("different_provider_preferred")
                ),
            }
            selected_ids, audit = _eligibility_for_work(
                work,
                endpoints,
                demand_by_work[work_id],
                hard_by_work[work_id],
                copies_by_work[work_id],
            )
            eligible_endpoint_ids[work_id] = selected_ids
            work_audits.append(audit)

        # Hard eligibility is enforced by endpoint whitelists above. Pass empty
        # hard sets into the original constructor so it retains original demand
        # values for quality fitting without reapplying a stale static gate.
        constructor_hard_sets = {work_id: set() for work_id in works}
        for work_id, work in works.items():
            groups = (
                [work_id]
                if independence_policy_by_work[work_id][
                    "different_model_required"
                ]
                else []
            )
            for copy_index in range(copies_by_work[work_id]):
                key = f"{work_id}#{copy_index}"
                for endpoint in endpoints:
                    if str(endpoint.get("endpoint_id")) not in eligible_endpoint_ids[
                        work_id
                    ]:
                        continue
                    candidate = candidate_factory(
                        interpretation_id,
                        [key],
                        [work],
                        [copy_index],
                        endpoint,
                        demand_by_work,
                        constructor_hard_sets,
                        groups,
                    )
                    if candidate is not None:
                        all_candidates.append(candidate)

        bundle_work = [
            work_id
            for work_id, copies in copies_by_work.items()
            if copies == 1
            and not independence_policy_by_work[work_id][
                "different_model_required"
            ]
        ]
        ordered_bundle_work = sorted(bundle_work)
        for left_index, left_id in enumerate(ordered_bundle_work):
            for right_id in ordered_bundle_work[left_index + 1 :]:
                if stage_by_work.get(left_id) != stage_by_work.get(right_id):
                    continue
                if works[left_id].get("dependencies", []) != works[right_id].get(
                    "dependencies", []
                ):
                    continue
                for endpoint in endpoints:
                    endpoint_id = str(endpoint.get("endpoint_id"))
                    if endpoint_id not in eligible_endpoint_ids[left_id]:
                        continue
                    if endpoint_id not in eligible_endpoint_ids[right_id]:
                        continue
                    candidate = candidate_factory(
                        interpretation_id,
                        [f"{left_id}#0", f"{right_id}#0"],
                        [works[left_id], works[right_id]],
                        [0, 0],
                        endpoint,
                        demand_by_work,
                        constructor_hard_sets,
                        [],
                        bundle_discount=0.84,
                    )
                    if candidate is not None:
                        all_candidates.append(candidate)

        interpretation_meta[interpretation_id] = {
            "metrics": dict(interpretation.get("metrics", {})),
            "work_ids": sorted(works),
            "copies_by_work": copies_by_work,
            "independence_policy_by_work": independence_policy_by_work,
            "atomic_edges": list(graph.get("edges", [])),
        }
        calibration_by_interpretation[interpretation_id] = {
            "work_calibrations": work_audits,
            "calibrated_work_count": sum(
                bool(row.get("calibration_applied")) for row in work_audits
            ),
            "still_insufficient_work_count": sum(
                row.get("calibration_status")
                == "rank-backed-proxy-still-insufficient"
                for row in work_audits
            ),
        }

    pruned = pruner(
        all_candidates, maximum_per_group=maximum_per_group
    )
    if not pruned:
        raise V5PlanningError("Candidate generation produced no feasible nodes.")
    return {
        "version": 5,
        "architecture": "candidate-nodes-and-information-edges-before-joint-solve",
        "candidates": [row.to_dict() for row in pruned],
        "candidate_count_before_pareto": len(all_candidates),
        "candidate_count_after_pareto": len(pruned),
        "pareto_pruned_count": len(all_candidates) - len(pruned),
        "interpretations": interpretation_meta,
        "hard_capability_calibration": {
            "version": 2,
            "policy": (
                "static-demand-threshold-first; rank-backed-adaptive-proxy-floor-"
                "when-static-market-cannot-satisfy-explicit-model-independence"
            ),
            "proxy_capability_floor": MIN_PROXY_CAPABILITY_FLOOR,
            "minimum_general_benchmark_score": MIN_GENERAL_BENCHMARK_SCORE,
            "minimum_benchmark_confidence": MIN_BENCHMARK_CONFIDENCE,
            "static_demand_multiplier": STATIC_DEMAND_MULTIPLIER,
            "capability_scores_modified": False,
            "task_demands_modified": False,
            "catalog_description_proxy_treated_as_measured_benchmark": False,
            "interpretations": calibration_by_interpretation,
        },
    }


def install() -> None:
    """Deprecated compatibility no-op; use PlannerPolicy explicitly."""
    return None
