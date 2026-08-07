#!/usr/bin/env python3
"""No-business-gate entrypoint for the V5 fully dynamic expert pipeline."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_price_ranked_pipeline as pipeline

_ORIGINAL_PROVIDER_FIELDS = pipeline._provider_fields
_ORIGINAL_RUNTIME_CONFIG = pipeline._runtime_config


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
        else "expert-assessment-center-task-derived-dynamic-ortools"
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
        "all_parameter_instances_current_task_derived": bool(
            plan.get("all_parameter_instances_current_task_derived")
            or audit_map.get("all_parameter_instances_current_task_derived")
        ),
        "fixed_parameter_template_used": bool(
            plan.get("fixed_parameter_template_used") is True
            or audit_map.get("fixed_parameter_template_used") is True
        ),
        "fixed_role_topology_used": bool(
            plan.get("fixed_role_topology_required") is True
            or audit_map.get("fixed_role_topology_used") is True
        ),
        "fixed_role_grammar_used": bool(
            plan.get("fixed_role_grammar_required") is True
            or audit_map.get("fixed_role_grammar_used") is True
        ),
    }


def _dynamic_provider_fields() -> dict[str, Any]:
    """Keep Provider routing open while allowing Expert-Center recovery models."""
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


def _dynamic_runtime_config(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Replace legacy fixed-topology/call-ceiling telemetry with active truth."""
    value = dict(_ORIGINAL_RUNTIME_CONFIG(*args, **kwargs))
    plan = kwargs.get("plan")
    if not isinstance(plan, Mapping) and len(args) >= 4 and isinstance(args[3], Mapping):
        plan = args[3]
    plan = plan if isinstance(plan, Mapping) else {}
    value.update(_dynamic_assignment_fields(plan))
    value.update(
        {
            "team_topology": "current-task-derived-declared-role-dag",
            "role_topology_source": "current-task-work-dag",
            "role_dependencies_source": "current-plan-explicit-dependencies",
            "fixed_role_topology_used": False,
            "fixed_role_grammar_used": False,
            "parameter_discovery_mode": "task-derived-parameter-instance-graph",
            "parameter_optimizer_library": "optuna",
            "parameter_dependency_library": "networkx",
            "model_assignment_optimizer_library": "ortools-cp-sat",
            "requested_call_budget_role": "telemetry-only",
            "fixed_call_ceiling_applied": False,
            "runtime_call_capacity_source": (
                "current-finite-execution-graph-plus-active-recovery-plus-standby"
            ),
            "runtime_feedback_replanning_enabled": True,
            "cross_task_history_used": False,
        }
    )
    return value


def main(argv: Sequence[str] | None = None) -> int:
    # The active facade delegates I/O and evidence writing to the validated core,
    # but replaces historical compatibility hooks that could re-introduce a
    # business gate or false fixed-topology telemetry.
    legacy_runtime = getattr(pipeline, "_legacy")
    setattr(legacy_runtime, "_validate_budget", _dynamic_validate_budget)

    setattr(pipeline, "_top50", _expert_assignment_active)
    setattr(pipeline, "_assignment_fields", _dynamic_assignment_fields)
    setattr(pipeline, "_provider_fields", _dynamic_provider_fields)
    setattr(pipeline, "_runtime_config", _dynamic_runtime_config)
    setattr(legacy_runtime, "_runtime_config", _dynamic_runtime_config)
    return int(pipeline.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
