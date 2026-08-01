"""Expand capability-qualified candidates for task-global company assignment.

The local capability calibrator historically retained only enough alternatives
for one work package's independent copies.  That is insufficient when the
optimizer imposes an all-different company constraint across every selected
node: several work packages can retain the same two companies and create a
Hall-type assignment conflict despite a broad market.

This layer does not change capability scores, task demands, hard labels, or the
minimum proxy floor.  It computes the current interpretation's required company
slots and retains additional rank-backed, context-compatible companies above the
same proxy floor.  The final CP-SAT solver still decides the node count and the
actual assignment and still fails closed when the evidence-backed market is
insufficient.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_capability_calibration as local_calibration
import v5_planner
from v5_model_company import canonical_model_company
from v5_planner import CandidateNode, V5PlanningError


def _company(endpoint: Mapping[str, Any]) -> str:
    return canonical_model_company(str(endpoint.get("model_id") or ""))


def _distinct_companies(
    endpoints: Sequence[Mapping[str, Any]],
) -> list[str]:
    return sorted(
        {
            _company(endpoint)
            for endpoint in endpoints
            if _company(endpoint) not in {"", "unknown"}
        }
    )


def _best_company_ranking(
    endpoints: Sequence[Mapping[str, Any]],
    hard_labels: set[str],
) -> list[dict[str, Any]]:
    best: dict[str, tuple[float, float, Mapping[str, Any]]] = {}
    for endpoint in endpoints:
        if not local_calibration._rank_backed(endpoint):
            continue
        company = _company(endpoint)
        if company in {"", "unknown"}:
            continue
        row = (
            local_calibration._proxy_score(endpoint, hard_labels),
            float(endpoint.get("benchmark_score", 0.0) or 0.0),
            endpoint,
        )
        previous = best.get(company)
        if previous is None or row[:2] > previous[:2]:
            best[company] = row
    return sorted(
        (
            {
                "company": company,
                "model": str(row[2].get("model_id") or ""),
                "proxy_score": round(row[0], 6),
                "benchmark_score": round(row[1], 6),
            }
            for company, row in best.items()
        ),
        key=lambda row: (
            -float(row["proxy_score"]),
            -float(row["benchmark_score"]),
            str(row["company"]),
            str(row["model"]),
        ),
    )


def _eligibility_for_global_assignment(
    work: Mapping[str, Any],
    endpoints: Sequence[Mapping[str, Any]],
    demand: Mapping[str, float],
    hard_labels: set[str],
    required_copies: int,
    global_company_target: int,
) -> tuple[set[str], dict[str, Any]]:
    context_eligible = [
        endpoint
        for endpoint in endpoints
        if local_calibration._endpoint_fits_context(endpoint, work)
    ]
    independence = work.get("independence_requirements", {})
    different_model_required = bool(
        isinstance(independence, Mapping)
        and independence.get("different_model_required")
    )
    local_target = required_copies if different_model_required else 1
    available_companies = _distinct_companies(context_eligible)
    breadth_target = max(
        local_target,
        min(global_company_target, len(available_companies)),
    )

    static_thresholds = local_calibration._hard_thresholds(
        demand,
        hard_labels,
    )
    static_eligible = [
        endpoint
        for endpoint in context_eligible
        if local_calibration._endpoint_meets(endpoint, static_thresholds)
    ]
    static_companies = _distinct_companies(static_eligible)
    company_ranking = _best_company_ranking(
        context_eligible,
        hard_labels,
    )

    adaptive_floor = local_calibration.MIN_PROXY_CAPABILITY_FLOOR
    if len(company_ranking) >= breadth_target:
        adaptive_floor = max(
            local_calibration.MIN_PROXY_CAPABILITY_FLOOR,
            float(company_ranking[breadth_target - 1]["proxy_score"]),
        )
    adaptive_eligible = [
        endpoint
        for endpoint in context_eligible
        if local_calibration._rank_backed(endpoint)
        and local_calibration._proxy_score(endpoint, hard_labels) + 1e-12
        >= adaptive_floor
    ]

    if len(static_companies) >= breadth_target:
        selected = static_eligible
        status = "static-threshold-global-company-breadth-sufficient"
        calibrated = False
    else:
        selected_by_id: dict[str, Mapping[str, Any]] = {}
        for endpoint in [*static_eligible, *adaptive_eligible]:
            endpoint_id = str(endpoint.get("endpoint_id") or "")
            if endpoint_id:
                selected_by_id[endpoint_id] = endpoint
        selected = list(selected_by_id.values())
        selected_company_count = len(_distinct_companies(selected))
        if selected_company_count >= breadth_target:
            status = "rank-backed-global-company-breadth-calibrated"
            calibrated = True
        else:
            status = "rank-backed-global-company-breadth-insufficient"
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
        "local_distinct_model_target": local_target,
        "interpretation_global_company_target": global_company_target,
        "work_candidate_company_breadth_target": breadth_target,
        "hard_labels": sorted(hard_labels),
        "static_thresholds": {
            label: round(value, 6)
            for label, value in static_thresholds.items()
        },
        "proxy_capability_floor": (
            local_calibration.MIN_PROXY_CAPABILITY_FLOOR
        ),
        "minimum_general_benchmark_score": (
            local_calibration.MIN_GENERAL_BENCHMARK_SCORE
        ),
        "minimum_benchmark_confidence": (
            local_calibration.MIN_BENCHMARK_CONFIDENCE
        ),
        "adaptive_proxy_floor": round(adaptive_floor, 6),
        "context_eligible_endpoint_count": len(context_eligible),
        "context_eligible_company_count": len(available_companies),
        "context_eligible_companies": available_companies,
        "static_eligible_endpoint_count": len(static_eligible),
        "static_eligible_company_count": len(static_companies),
        "static_eligible_companies": static_companies,
        "rank_backed_company_ranking": company_ranking,
        "adaptive_eligible_endpoint_count": len(adaptive_eligible),
        "adaptive_eligible_company_count": len(
            _distinct_companies(adaptive_eligible)
        ),
        "adaptive_eligible_companies": _distinct_companies(
            adaptive_eligible
        ),
        "selected_eligible_endpoint_count": len(selected_ids),
        "selected_eligible_company_count": len(
            _distinct_companies(selected)
        ),
        "selected_eligible_companies": _distinct_companies(selected),
        "calibration_applied": calibrated,
        "calibration_status": status,
        "capability_scores_modified": False,
        "task_demands_modified": False,
        "hard_labels_modified": False,
        "proxy_floor_lowered": False,
        "catalog_description_proxy_treated_as_measured_benchmark": False,
        "rank_and_confidence_required_for_adaptive_calibration": True,
        "cross_task_history_used": False,
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
    """Generate capability-safe candidates with global company breadth."""
    candidate_factory = candidate_factory or v5_planner._candidate_for
    pruner = pruner or v5_planner.pareto_prune
    endpoints = [
        endpoint
        for endpoint in market.get("endpoints", [])
        if isinstance(endpoint, Mapping)
    ]
    market_company_count = len(_distinct_companies(endpoints))
    all_candidates: list[CandidateNode] = []
    interpretation_meta: dict[str, Any] = {}
    calibration_by_interpretation: dict[str, Any] = {}

    for interpretation in resource_bundle.get("task_semantics", {}).get(
        "interpretations",
        [],
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
            for index, stage in enumerate(
                graph.get("execution_stages", [])
            )
            for work_id in stage
        }
        for row_index, row in enumerate(matrix["work_index"]):
            work_id = str(row["work_id"])
            demand_by_work[work_id] = {
                label: float(
                    matrix["task_resource_matrix"][row_index][column]
                )
                for column, label in enumerate(labels)
            }
            hard_by_work[work_id] = {
                label
                for column, label in enumerate(labels)
                if int(
                    matrix["hard_requirement_matrix"][row_index][column]
                )
                == 1
            }
            copies_by_work[work_id] = max(
                1,
                int(row.get("minimum_independent_copies", 1)),
            )

        global_company_target = min(
            sum(copies_by_work.values()),
            market_company_count,
        )
        eligible_endpoint_ids: dict[str, set[str]] = {}
        work_audits: list[dict[str, Any]] = []
        for work_id, work in works.items():
            independence = work.get("independence_requirements", {})
            policy = (
                independence
                if isinstance(independence, Mapping)
                else {}
            )
            independence_policy_by_work[work_id] = {
                "different_model_required": bool(
                    policy.get("different_model_required")
                ),
                "different_provider_required": False,
                "different_provider_preferred": bool(
                    policy.get("different_provider_preferred")
                ),
            }
            selected_ids, audit = _eligibility_for_global_assignment(
                work,
                endpoints,
                demand_by_work[work_id],
                hard_by_work[work_id],
                copies_by_work[work_id],
                global_company_target,
            )
            eligible_endpoint_ids[work_id] = selected_ids
            work_audits.append(audit)

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
                    if (
                        str(endpoint.get("endpoint_id"))
                        not in eligible_endpoint_ids[work_id]
                    ):
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
                if stage_by_work.get(left_id) != stage_by_work.get(
                    right_id
                ):
                    continue
                if works[left_id].get("dependencies", []) != works[
                    right_id
                ].get("dependencies", []):
                    continue
                for endpoint in endpoints:
                    endpoint_id = str(endpoint.get("endpoint_id") or "")
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
            "global_company_target": global_company_target,
        }
        calibration_by_interpretation[interpretation_id] = {
            "global_company_target": global_company_target,
            "market_company_count": market_company_count,
            "work_calibrations": work_audits,
            "calibrated_work_count": sum(
                bool(row.get("calibration_applied"))
                for row in work_audits
            ),
            "still_insufficient_work_count": sum(
                row.get("calibration_status")
                == "rank-backed-global-company-breadth-insufficient"
                for row in work_audits
            ),
        }

    pruned = pruner(
        all_candidates,
        maximum_per_group=maximum_per_group,
    )
    if not pruned:
        raise V5PlanningError(
            "Candidate generation produced no feasible nodes."
        )
    return {
        "version": 5,
        "architecture": (
            "candidate-nodes-and-information-edges-before-joint-solve"
        ),
        "candidates": [row.to_dict() for row in pruned],
        "candidate_count_before_pareto": len(all_candidates),
        "candidate_count_after_pareto": len(pruned),
        "pareto_pruned_count": len(all_candidates) - len(pruned),
        "interpretations": interpretation_meta,
        "hard_capability_calibration": {
            "version": 3,
            "policy": (
                "static-demand-threshold-first; rank-backed-proxy-floor-"
                "expanded-for-task-global-company-assignment"
            ),
            "proxy_capability_floor": (
                local_calibration.MIN_PROXY_CAPABILITY_FLOOR
            ),
            "minimum_general_benchmark_score": (
                local_calibration.MIN_GENERAL_BENCHMARK_SCORE
            ),
            "minimum_benchmark_confidence": (
                local_calibration.MIN_BENCHMARK_CONFIDENCE
            ),
            "static_demand_multiplier": (
                local_calibration.STATIC_DEMAND_MULTIPLIER
            ),
            "model_company_policy": "task-global-all-different",
            "capability_scores_modified": False,
            "task_demands_modified": False,
            "hard_labels_modified": False,
            "proxy_floor_lowered": False,
            "catalog_description_proxy_treated_as_measured_benchmark": False,
            "cross_task_history_used": False,
            "interpretations": calibration_by_interpretation,
        },
    }
