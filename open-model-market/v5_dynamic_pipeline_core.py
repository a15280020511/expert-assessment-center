#!/usr/bin/env python3
"""No-business-gate entrypoint for the V5 fully dynamic expert pipeline."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_price_ranked_pipeline as pipeline

_ORIGINAL_PROVIDER_FIELDS = pipeline._provider_fields


def _dynamic_validate_budget(args: Any) -> tuple[int, int]:
    """Treat CLI call counts as execution telemetry, not admission thresholds."""
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
    """Recognize current dynamic Expert-Center assignment without a TopN gate."""
    authority = str(plan.get("model_assignment_authority") or "").strip()
    audit = plan.get("optimizer_audit")
    audit_map = audit if isinstance(audit, Mapping) else {}
    optimizer = str(
        plan.get("optimizer") or audit_map.get("optimizer") or ""
    ).strip()
    selected = plan.get("selected_models")
    return bool(
        authority.startswith("expert-assessment-center")
        or (
            optimizer.startswith("ortools-cp-sat")
            and isinstance(selected, list)
            and bool(selected)
        )
    )


def _dynamic_assignment_fields(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Emit telemetry from the actual dynamic plan, not historical gate names."""
    active = _expert_assignment_active(plan)
    audit = plan.get("optimizer_audit")
    audit_map = audit if isinstance(audit, Mapping) else {}
    optimizer = str(
        plan.get("optimizer") or audit_map.get("optimizer") or ""
    ).strip() or None
    declared_authority = str(
        plan.get("model_assignment_authority") or ""
    ).strip()
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
            plan.get("candidate_pool_authority")
            or "decision-system-governance"
        ),
        "model_assignment_authority": assignment_authority,
        "selection_authority": (
            assignment_authority if active else "decision-system-governance"
        ),
        "expert_center_model_selection_allowed": active,
        "expert_center_pool_assignment_performed": active,
        "model_selection_performed_locally": active,
        "candidate_pool_reranking_performed_locally": active,
        "model_reranking_performed_locally": active,
        "model_substitution_allowed": active,
        "optimizer_present": optimizer_present,
        "optimizer_used": bool(active and optimizer_present),
        "optimizer": optimizer,
        "optimizer_optimality_proven": bool(
            audit_map.get("optimality_proven")
        ),
        "optimizer_optimality_required": False,
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "hard_model_eligibility_gates": [],
        "tool_use_forbidden": True,
        "tools_allowed": False,
        "only_hard_model_boundary": "no-tools",
        "all_calculable_planning_parameters_dynamic": bool(
            plan.get("all_calculable_planning_parameters_dynamic")
            or audit_map.get("all_calculable_planning_parameters_dynamic")
        ),
    }


def _dynamic_provider_fields() -> dict[str, Any]:
    """Keep Provider routing open while allowing Expert-Center recovery models.

    OpenRouter may choose/fail over Providers for the *same* model identity.  A
    different model identity may only be selected by the Expert Center's dynamic
    recovery graph.  Historical telemetry used one ``model_substitution`` flag
    for both concepts and incorrectly reported recovery as disabled.
    """
    value = dict(_ORIGINAL_PROVIDER_FIELDS())
    value.update(
        {
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "provider_fallback_allowed": True,
            "unrestricted_provider_fallback_allowed": True,
            "openrouter_selects_provider": True,
            "provider_may_change_model_identity": False,
            "model_substitution_allowed": True,
            "model_substitution_authority": (
                "expert-assessment-center-dynamic-recovery"
            ),
            "tool_use_forbidden": True,
            "tools_allowed": False,
            "only_hard_model_boundary": "no-tools",
        }
    )
    return value


def main(argv: Sequence[str] | None = None) -> int:
    # The active facade delegates to the battle-tested implementation for I/O
    # and evidence writing. Replace historical compatibility hooks so old names
    # cannot silently re-introduce a business gate or false telemetry.
    legacy_runtime = getattr(pipeline, "_legacy")
    setattr(legacy_runtime, "_validate_budget", _dynamic_validate_budget)

    setattr(pipeline, "_top50", _expert_assignment_active)
    setattr(pipeline, "_assignment_fields", _dynamic_assignment_fields)
    setattr(pipeline, "_provider_fields", _dynamic_provider_fields)
    return int(pipeline.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
