"""Calibrate company breadth to both feasibility and the live search width.

A company-all-different solve needs enough companies for assignment, but retaining
only that minimum can still erase cheaper evidence-backed alternatives before the
cost-performance optimizer runs. This module keeps the same capability scores,
hard labels, rank/confidence gates, context limits, and proxy floor as the global
company calibrator. It only expands the retained company breadth with the current
adaptive candidate-search width, bounded by the current market and the configured
emergency ceiling supplied by the caller.
"""
from __future__ import annotations

from typing import Any, Mapping

import v5_global_company_calibration as base_calibration
import v5_planner
from v5_planner import CandidateNode, V5PlanningError


def generate_calibrated_candidate_graph(
    resource_bundle: Mapping[str, Any],
    market: Mapping[str, Any],
    *,
    maximum_per_group: int = 12,
    candidate_factory: Any | None = None,
    pruner: Any | None = None,
) -> dict[str, Any]:
    """Generate candidates with search-aware, evidence-backed company breadth."""
    candidate_factory = candidate_factory or v5_planner._candidate_for
    pruner = pruner or v5_planner.pareto_prune
    endpoints = [
        endpoint
        for endpoint in market.get("endpoints", [])
        if isinstance(endpoint, Mapping)
    ]
    market_company_count = len(
        base_calibration._distinct_companies(endpoints)
    )
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

        minimum_assignment_company_target = min(
            sum(copies_by_work.values()),
            market_company_count,
        )
        search_company_target = min(
            market_company_count,
            max(
                minimum_assignment_company_target,
                max(2, int(maximum_per_group)),
            ),
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
            selected_ids, audit = (
                base_calibration._eligibility_for_global_assignment(
                    work,
                    endpoints,
                    demand_by_work[work_id],
                    hard_by_work[work_id],
                    copies_by_work[work_id],
                    search_company_target,
                )
            )
            audit = dict(audit)
            audit["minimum_assignment_company_target"] = (
                minimum_assignment_company_target
            )
            audit["search_company_target"] = search_company_target
            audit["interpretation_global_company_target"] = (
                minimum_assignment_company_target
            )
            audit["candidate_breadth_expanded_for_cost_performance"] = (
                search_company_target
                > minimum_assignment_company_target
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
            "global_company_target": minimum_assignment_company_target,
            "search_company_target": search_company_target,
        }
        calibration_by_interpretation[interpretation_id] = {
            "global_company_target": minimum_assignment_company_target,
            "search_company_target": search_company_target,
            "market_company_count": market_company_count,
            "candidate_breadth_expanded_for_cost_performance": (
                search_company_target
                > minimum_assignment_company_target
            ),
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
            "version": 4,
            "policy": (
                "static-demand-threshold-first; rank-backed-proxy-floor-"
                "expanded-for-assignment-and-current-search-breadth"
            ),
            "proxy_capability_floor": (
                base_calibration.local_calibration.MIN_PROXY_CAPABILITY_FLOOR
            ),
            "minimum_general_benchmark_score": (
                base_calibration.local_calibration.MIN_GENERAL_BENCHMARK_SCORE
            ),
            "minimum_benchmark_confidence": (
                base_calibration.local_calibration.MIN_BENCHMARK_CONFIDENCE
            ),
            "static_demand_multiplier": (
                base_calibration.local_calibration.STATIC_DEMAND_MULTIPLIER
            ),
            "model_company_policy": "task-global-all-different",
            "candidate_breadth_policy": (
                "minimum-assignment-slots-plus-current-adaptive-search-width"
            ),
            "capability_scores_modified": False,
            "task_demands_modified": False,
            "hard_labels_modified": False,
            "proxy_floor_lowered": False,
            "catalog_description_proxy_treated_as_measured_benchmark": False,
            "cross_task_history_used": False,
            "interpretations": calibration_by_interpretation,
        },
    }
