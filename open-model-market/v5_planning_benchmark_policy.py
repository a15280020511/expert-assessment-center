"""Planning-surrogate metrics aligned with explicit V5 independence groups."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_benchmark

_INSTALLED = False


def _coverage(candidate: Mapping[str, Any]) -> set[str]:
    return {str(value) for value in candidate.get("coverage_keys", [])}


def explicit_independence_metrics(
    rows: Sequence[Mapping[str, Any]],
    required: set[str],
    copies_by_work: Mapping[str, Any],
) -> dict[str, Any]:
    """Score a plan using the same hard model-independence rule as validation.

    Every required copy must still be selected exactly once. Model reuse is a
    hard violation only when the selected candidates explicitly carry the work
    ID in ``independence_groups``. Provider diversity is not an undeclared hard
    gate; R8 handles concentration through provider rebalancing and recovery.
    """
    covered = set().union(*(_coverage(row) for row in rows)) if rows else set()
    violations: list[str] = []
    independence_audit: list[dict[str, Any]] = []
    for work_id_raw, copies_raw in copies_by_work.items():
        work_id = str(work_id_raw)
        copies = int(copies_raw)
        if copies < 2:
            continue
        selected_by_copy: list[Mapping[str, Any]] = []
        for copy_index in range(copies):
            key = f"{work_id}#{copy_index}"
            matches = [row for row in rows if key in _coverage(row)]
            if len(matches) != 1:
                violations.append(
                    f"{work_id}:copy-{copy_index}-selection-count={len(matches)}"
                )
                continue
            selected_by_copy.append(matches[0])
        distinct_model_required = any(
            work_id in {str(value) for value in row.get("independence_groups", [])}
            for row in selected_by_copy
        )
        if len(selected_by_copy) == copies and distinct_model_required:
            models = [str(row.get("model") or "") for row in selected_by_copy]
            if len(set(models)) != copies:
                violations.append(f"{work_id}:independent-copies-reuse-model")
        independence_audit.append(
            {
                "work_id": work_id,
                "copies": copies,
                "different_model_required": distinct_model_required,
                "different_provider_required": False,
            }
        )
    quality = sum(float(row.get("estimated_quality", 0.0)) for row in rows) / max(
        1, len(rows)
    )
    return {
        "feasible": required <= covered and not violations,
        "coverage_ratio": round(len(required & covered) / max(1, len(required)), 6),
        "estimated_quality": round(quality, 6),
        "estimated_cost_usd": round(
            sum(float(row.get("estimated_cost", 0.0)) for row in rows), 8
        ),
        "node_count": len(rows),
        "hard_constraint_violations": violations,
        "models": sorted({str(row.get("model") or "") for row in rows}),
        "provider_endpoints": sorted(
            {str(row.get("provider_endpoint") or "") for row in rows}
        ),
        "independence_policy": independence_audit,
    }


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    v5_benchmark._metrics = explicit_independence_metrics
    _INSTALLED = True
