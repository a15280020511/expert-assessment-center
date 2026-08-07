#!/usr/bin/env python3
"""No-business-gate entrypoint for the V5 dynamic expert pipeline."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_price_ranked_pipeline as pipeline


def _dynamic_validate_budget(args: Any) -> tuple[int, int]:
    """Treat CLI call counts as graph sizing telemetry, not admission thresholds."""
    total = int(args.maximum_total_calls)
    recovery = int(args.maximum_recovery_calls)
    if total < 1:
        total = 1
    if recovery < 0:
        recovery = 0
    if recovery >= total:
        recovery = max(0, total - 1)
    return total, recovery


def _expert_assignment_active(plan: Mapping[str, Any]) -> bool:
    """Recognize current dynamic Expert-Center assignment without a Top50 gate."""
    authority = str(plan.get("model_assignment_authority") or "").strip()
    audit = plan.get("optimizer_audit")
    audit_map = audit if isinstance(audit, Mapping) else {}
    optimizer = str(plan.get("optimizer") or audit_map.get("optimizer") or "").strip()
    selected = plan.get("selected_models")
    return bool(
        authority.startswith("expert-assessment-center")
        or plan.get("selected_from_top50_reasoning_pool_only") is True
        or (
            optimizer == "ortools-cp-sat"
            and isinstance(selected, list)
            and bool(selected)
        )
    )


def _dynamic_assignment_fields(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Emit model-assignment telemetry from the actual dynamic plan metadata."""
    active = _expert_assignment_active(plan)
    audit = plan.get("optimizer_audit")
    audit_map = audit if isinstance(audit, Mapping) else {}
    optimizer = str(plan.get("optimizer") or audit_map.get("optimizer") or "").strip() or None
    declared_authority = str(plan.get("model_assignment_authority") or "").strip()
    assignment_authority = (
        declared_authority
        if active and declared_authority.startswith("expert-assessment-center")
        else "expert-assessment-center-dynamic-ortools"
        if active
        else "decision-system-governance"
    )
    optimizer_present = bool(optimizer or audit_map)
    return {
        "candidate_pool_authority": str(
            plan.get("candidate_pool_authority") or "decision-system-governance"
        ),
        "model_assignment_authority": assignment_authority,
        "selection_authority": assignment_authority if active else "decision-system-governance",
        "expert_center_model_selection_allowed": active,
        "expert_center_pool_assignment_performed": active,
        "model_selection_performed_locally": active,
        "candidate_pool_reranking_performed_locally": False,
        "model_reranking_performed_locally": False,
        "model_substitution_allowed": False,
        "optimizer_present": optimizer_present,
        "optimizer_used": bool(active and optimizer_present),
        "optimizer": optimizer,
        "optimizer_optimality_proven": bool(audit_map.get("optimality_proven")),
    }


def main(argv: Sequence[str] | None = None) -> int:
    # The active facade delegates to the legacy implementation for I/O and
    # evidence writing. Replace only historical business-gate compatibility
    # hooks; graph/materializer/runtime limits remain task-dynamic.
    legacy_runtime = getattr(pipeline, "_legacy")
    setattr(legacy_runtime, "_validate_budget", _dynamic_validate_budget)

    # v5_price_ranked_pipeline historically used the Top50 marker as a proxy for
    # "Expert Center performed model assignment". Dynamic v9 plans deliberately
    # set Top50-only=false, so patch that compatibility hook to read the actual
    # assignment authority / optimizer audit instead. This changes telemetry,
    # not admission or routing behavior.
    setattr(pipeline, "_top50", _expert_assignment_active)
    setattr(pipeline, "_assignment_fields", _dynamic_assignment_fields)
    return int(pipeline.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
