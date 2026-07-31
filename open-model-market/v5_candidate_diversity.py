"""Company-diversity-preserving Pareto pruning for V5 candidate nodes.

A candidate dominated on quality, cost, and failure can still be required by the
task-global model-company uniqueness constraint. The pruner therefore keeps each
company's best representative before filling remaining capacity from the Pareto
frontier. Provider endpoints from the same model company do not count as
independent alternatives.
"""
from __future__ import annotations

from typing import Sequence

import v5_planner
from v5_model_company_policy import row_company
from v5_planner import CandidateNode


def _order_key(row: CandidateNode) -> tuple[float, float, float, str]:
    return (
        -row.estimated_quality,
        row.estimated_cost,
        row.failure_probability,
        row.candidate_id,
    )


def diversity_preserving_pareto_prune(
    candidates: Sequence[CandidateNode],
    maximum_per_group: int = 24,
) -> list[CandidateNode]:
    """Keep Pareto quality while reserving distinct-company alternatives."""
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

        best_by_company: dict[str, CandidateNode] = {}
        for row in ordered:
            best_by_company.setdefault(row_company(row), row)

        selected: list[CandidateNode] = []
        selected_ids: set[str] = set()
        for row in sorted(best_by_company.values(), key=_order_key):
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
    """Deprecated compatibility no-op; formal runtime composes the function directly."""
    return None
