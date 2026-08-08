"""Truthful compatibility fields for current-ticket Expert assignment.

Legacy ``top50`` names remain readable, but they must not decide who actually
selected models. Modern governance transports the full current reasoning pool and
explicitly delegates current-ticket assignment/reranking to the Expert Center.
"""
from __future__ import annotations

from typing import Any, Mapping


def expert_dynamic_assignment_active(plan: Mapping[str, Any]) -> bool:
    authority = str(plan.get("model_assignment_authority") or "").casefold()
    governance_selected = plan.get("selection_performed_by_governance") is True
    delegated = (
        plan.get("expert_center_pool_selection_allowed") is True
        or plan.get("task_adaptive_assignment_required") is True
        or authority.startswith("expert-assessment-center")
    )
    return bool(delegated and not governance_selected)


def assignment_fields(plan: Mapping[str, Any]) -> dict[str, Any]:
    active = expert_dynamic_assignment_active(plan)
    substitution = bool(
        active
        and (
            plan.get("model_substitution_allowed") is not False
            or plan.get("expert_center_reranking_allowed") is True
        )
    )
    authority = (
        "expert-assessment-center-current-ticket-generated-parameter-ortools"
        if active
        else "decision-system-governance"
    )
    return {
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": authority,
        "selection_authority": authority,
        "expert_center_model_selection_allowed": active,
        "expert_center_pool_assignment_performed": active,
        "model_selection_performed_locally": active,
        "candidate_pool_reranking_performed_locally": active,
        "model_reranking_performed_locally": active,
        "model_substitution_allowed": substitution,
        "optimizer_present": active,
        "optimizer_used": active,
        "optimizer": plan.get("optimizer") if active else None,
        "optimizer_optimality_proven": bool(
            (plan.get("optimizer_audit") or {}).get("optimality_proven")
        )
        if isinstance(plan.get("optimizer_audit"), Mapping)
        else False,
        "legacy_top50_flag_controls_assignment": False,
        "full_candidate_pool_assignment_supported": True,
        "fixed_four_plus_four_required": False,
        "fixed_team_size_required": False,
        "fixed_recovery_count_required": False,
    }


def install_pipeline_assignment_truth() -> None:
    """Patch compatibility facade globals before legacy main executes."""
    import v5_price_ranked_pipeline as pipeline

    pipeline._top50 = expert_dynamic_assignment_active
    pipeline._assignment_fields = assignment_fields


__all__ = [
    "assignment_fields",
    "expert_dynamic_assignment_active",
    "install_pipeline_assignment_truth",
]
