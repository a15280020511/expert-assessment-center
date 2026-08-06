"""Top-50 assignment evidence facade for the governed DAG orchestrator."""
from __future__ import annotations

from typing import Any, Mapping

import v5_governed_plan_orchestrator_legacy as _legacy
from v5_governance_model_plan import validate_governance_model_plan

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

GovernedPlanOrchestrationError = _legacy.GovernedPlanOrchestrationError


def build_governed_proposal(
    *,
    ticket: Mapping[str, Any],
    catalog: Mapping[str, Any],
    task_envelope: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    proposal, audit = _legacy.build_governed_proposal(
        ticket=ticket,
        catalog=catalog,
        task_envelope=task_envelope,
    )
    plan = validate_governance_model_plan(ticket)
    top50 = plan.get("selected_from_top50_reasoning_pool_only") is True
    value = dict(audit)
    value.update(
        {
            "candidate_pool_authority": "decision-system-governance",
            "model_assignment_authority": (
                "expert-assessment-center-ortools" if top50 else "decision-system-governance"
            ),
            "model_selection_performed_locally": top50,
            "candidate_pool_reranking_performed_locally": False,
            "model_substitution_performed_locally": False,
            "optimizer_used": top50,
            "optimizer": plan.get("optimizer") if top50 else None,
            "optimizer_optimality_proven": bool(
                plan.get("optimizer_audit", {}).get("optimality_proven")
            ) if top50 else False,
            "provider_resolution_policy": (
                "fixed governance/expert-assigned model -> cheapest qualified primary "
                "endpoint -> all same-model qualified endpoints retained as audited whitelist"
            ),
        }
    )
    return proposal, value


__all__ = [
    "GovernedPlanOrchestrationError",
    "build_governed_proposal",
]
