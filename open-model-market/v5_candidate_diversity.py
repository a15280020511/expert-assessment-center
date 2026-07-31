"""Diversity-preserving Pareto pruning for V5 candidate nodes.

A candidate dominated on quality/cost/failure can still be required to satisfy
independent-copy constraints when the dominant candidate uses the same model as
another copy. Therefore the pruner keeps every model's best representative before
filling the remaining group capacity from the Pareto frontier.
"""
from __future__ import annotations

import sys
from typing import Sequence

import v5_capability_calibration
import v5_output_contract_delivery
import v5_planner
import v5_planning_benchmark_policy
import v5_production_hardening
from v5_planner import CandidateNode

_INSTALLED = False


def _order_key(row: CandidateNode) -> tuple[float, float, float, str]:
    return (
        -row.estimated_quality,
        row.estimated_cost,
        row.failure_probability,
        row.candidate_id,
    )


def diversity_preserving_pareto_prune(
    candidates: Sequence[CandidateNode],
    maximum_per_group: int = 12,
) -> list[CandidateNode]:
    """Keep Pareto quality while reserving distinct-model alternatives."""
    limit = max(2, int(maximum_per_group))
    groups: dict[tuple[str, tuple[str, ...]], list[CandidateNode]] = {}
    for candidate in candidates:
        groups.setdefault(
            (candidate.interpretation_id, candidate.coverage_keys), []
        ).append(candidate)

    kept: list[CandidateNode] = []
    for rows in groups.values():
        ordered = sorted(rows, key=_order_key)
        frontier = [
            row
            for row in ordered
            if not any(
                v5_planner._dominates(other, row)
                for other in rows
                if other is not row
            )
        ]

        best_by_model: dict[str, CandidateNode] = {}
        for row in ordered:
            best_by_model.setdefault(row.model, row)

        selected: list[CandidateNode] = []
        selected_ids: set[str] = set()
        for row in sorted(best_by_model.values(), key=_order_key):
            selected.append(row)
            selected_ids.add(row.candidate_id)
            if len(selected) >= limit:
                break

        if len(selected) < limit:
            supplement = sorted(
                [row for row in frontier if row.candidate_id not in selected_ids],
                key=_order_key,
            )
            for row in supplement:
                selected.append(row)
                selected_ids.add(row.candidate_id)
                if len(selected) >= limit:
                    break

        if len(selected) < limit:
            for row in ordered:
                if row.candidate_id in selected_ids:
                    continue
                selected.append(row)
                selected_ids.add(row.candidate_id)
                if len(selected) >= limit:
                    break

        kept.extend(selected)

    kept.sort(
        key=lambda row: (
            row.interpretation_id,
            row.coverage_keys,
            -row.estimated_quality,
            row.estimated_cost,
            row.candidate_id,
        )
    )
    return kept


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        v5_production_hardening.install()
        return
    v5_planner.pareto_prune = diversity_preserving_pareto_prune
    # Candidate qualification, diversity preservation, output-contract delivery,
    # conservative cost control and resilient partial-success synthesis form one
    # production safety unit. No alternate runtime or production entry is changed.
    v5_capability_calibration.install()

    optimizer = sys.modules.get("v5_value_optimizer")
    if optimizer is not None:
        v5_planner.optimize_execution_graph = optimizer.optimize_execution_graph

    v5_planning_benchmark_policy.install()
    v5_output_contract_delivery.install()
    v5_production_hardening.install()
    _INSTALLED = True
