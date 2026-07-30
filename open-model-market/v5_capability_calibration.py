"""Calibrate sparse catalog capability evidence without weakening V5 hard work demands.

OpenRouter model descriptions are incomplete evidence, not exhaustive capability
registries. The original candidate factory converted missing keywords into low
scores and then applied demand-proportional hard thresholds. That could erase all
independent alternatives even for highly ranked general models.

This layer keeps the original static threshold whenever it yields enough distinct
models for the required independent copies. Only when it does not, it falls back
to the existing absolute evidence floor of 0.48. Scores are never inflated, the
floor is never lowered, context/output constraints remain mandatory, and every
calibration decision is recorded in the candidate bundle.
"""
from __future__ import annotations

import sys
from typing import Any, Mapping, Sequence

import v5_planner
from v5_planner import CandidateNode, V5PlanningError

ABSOLUTE_HARD_EVIDENCE_FLOOR = 0.48
STATIC_DEMAND_MULTIPLIER = 0.62
_INSTALLED = False
_ORIGINAL_GENERATOR = v5_planner.generate_candidate_graph


def _hard_thresholds(
    demand: Mapping[str, float],
    hard_labels: set[str],
) -> dict[str, float]:
    return {
        label: max(
            ABSOLUTE_HARD_EVIDENCE_FLOOR,
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


def _distinct_models(endpoints: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted(
        {
            str(endpoint.get("model_id") or "")
            for endpoint in endpoints
            if endpoint.get("model_id")
        }
    )


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
    static_thresholds = _hard_thresholds(demand, hard_labels)
    floor_thresholds = {
        label: ABSOLUTE_HARD_EVIDENCE_FLOOR for label in sorted(hard_labels)
    }
    static_eligible = [
        endpoint
        for endpoint in context_eligible
        if _endpoint_meets(endpoint, static_thresholds)
    ]
    floor_eligible = [
        endpoint
        for endpoint in context_eligible
        if _endpoint_meets(endpoint, floor_thresholds)
    ]
    static_models = _distinct_models(static_eligible)
    floor_models = _distinct_models(floor_eligible)

    if len(static_models) >= required_copies:
        selected = static_eligible
        status = "static-threshold-sufficient"
        calibrated = False
    else:
        # Use the strongest evidence set available at the pre-existing absolute
        # floor. If it is still insufficient, the optimizer remains fail-closed.
        selected = floor_eligible
        calibrated = len(floor_models) >= required_copies
        status = (
            "absolute-floor-calibrated"
            if calibrated
            else "absolute-floor-still-insufficient"
        )

    selected_ids = {
        str(endpoint.get("endpoint_id"))
        for endpoint in selected
        if endpoint.get("endpoint_id")
    }
    audit = {
        "work_id": str(work.get("work_id") or ""),
        "required_independent_copies": required_copies,
        "hard_labels": sorted(hard_labels),
        "static_thresholds": {
            label: round(value, 6) for label, value in static_thresholds.items()
        },
        "absolute_hard_evidence_floor": ABSOLUTE_HARD_EVIDENCE_FLOOR,
        "context_eligible_endpoint_count": len(context_eligible),
        "static_eligible_endpoint_count": len(static_eligible),
        "static_eligible_model_count": len(static_models),
        "static_eligible_models": static_models,
        "floor_eligible_endpoint_count": len(floor_eligible),
        "floor_eligible_model_count": len(floor_models),
        "floor_eligible_models": floor_models,
        "selected_eligible_endpoint_count": len(selected_ids),
        "selected_eligible_model_count": len(_distinct_models(selected)),
        "selected_eligible_models": _distinct_models(selected),
        "calibration_applied": calibrated,
        "calibration_status": status,
        "capability_scores_modified": False,
        "task_demands_modified": False,
        "hard_floor_lowered": False,
    }
    return selected_ids, audit


def generate_calibrated_candidate_graph(
    resource_bundle: Mapping[str, Any],
    market: Mapping[str, Any],
    *,
    maximum_per_group: int = 12,
) -> dict[str, Any]:
    """Generate candidates using market-calibrated hard-capability eligibility."""
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
        # values for quality fitting without reapplying the stale static gate.
        constructor_hard_sets = {work_id: set() for work_id in works}
        for work_id, work in works.items():
            groups = (
                [work_id]
                if bool(
                    work.get("independence_requirements", {}).get(
                        "independent_execution_preferred"
                    )
                )
                else []
            )
            for copy_index in range(copies_by_work[work_id]):
                key = f"{work_id}#{copy_index}"
                for endpoint in endpoints:
                    if str(endpoint.get("endpoint_id")) not in eligible_endpoint_ids[
                        work_id
                    ]:
                        continue
                    candidate = v5_planner._candidate_for(
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
            and not works[work_id]
            .get("independence_requirements", {})
            .get("independent_execution_preferred")
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
                    candidate = v5_planner._candidate_for(
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
            "atomic_edges": list(graph.get("edges", [])),
        }
        calibration_by_interpretation[interpretation_id] = {
            "work_calibrations": work_audits,
            "calibrated_work_count": sum(
                bool(row.get("calibration_applied")) for row in work_audits
            ),
            "still_insufficient_work_count": sum(
                row.get("calibration_status")
                == "absolute-floor-still-insufficient"
                for row in work_audits
            ),
        }

    pruned = v5_planner.pareto_prune(
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
            "version": 1,
            "policy": (
                "static-demand-threshold-first; existing-absolute-0.48-floor-only-"
                "when-static-market-cannot-satisfy-required-independent-copies"
            ),
            "absolute_hard_evidence_floor": ABSOLUTE_HARD_EVIDENCE_FLOOR,
            "static_demand_multiplier": STATIC_DEMAND_MULTIPLIER,
            "capability_scores_modified": False,
            "task_demands_modified": False,
            "hard_floor_lowered": False,
            "interpretations": calibration_by_interpretation,
        },
    }


def install() -> None:
    """Install calibrated candidate generation into all loaded V5 call paths."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    v5_planner.generate_candidate_graph = generate_calibrated_candidate_graph
    optimizer = sys.modules.get("v5_value_optimizer")
    if optimizer is not None:
        setattr(
            optimizer,
            "generate_candidate_graph",
            generate_calibrated_candidate_graph,
        )
