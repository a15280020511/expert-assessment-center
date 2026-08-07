"""Task-derived dynamic candidate optimizer.

Pre-execution planning order:
current ticket -> structural execution-transport compatibility -> task-derived work DAG
-> parameter-instance discovery -> parameter dependency graph -> conditional Optuna
resolution -> role DAG -> structural role demand -> OR-Tools model-role assignment.

Current-run standby promotion is a separate execution/replanning phase. No fixed role
or metric-role grammar participates. No business model gate is introduced; no-tools
remains the sole hard model boundary, while exact identity and executable transport
remain structural protocol invariants.
"""
from __future__ import annotations

from typing import Any, Mapping

import v5_top50_pool_optimizer as base
from v5_dynamic_parameter_graph import (
    SCHEMA_VERSION as DYNAMIC_PARAMETER_GRAPH_SCHEMA_VERSION,
    build_dynamic_planning_context,
)
from v5_dynamic_role_assignment import solve_dynamic_roles
from v5_execution_transport import filter_executable_candidates


class HierarchicalOptimizationError(RuntimeError):
    """Raised only when the current ticket cannot form a finite executable plan."""


def _materialize(packet: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    source = packet.get("governance_model_plan")
    if not isinstance(source, Mapping):
        raise HierarchicalOptimizationError("governance_model_plan is missing")
    source_plan = dict(source)
    try:
        governance_candidates = base._candidate_rows(source_plan)  # noqa: SLF001
        candidates, transport_audit = filter_executable_candidates(governance_candidates)
        if not candidates:
            raise HierarchicalOptimizationError(
                "governance candidate inventory contains no route compatible with "
                "the active synchronous execution transport"
            )
        planning = build_dynamic_planning_context(packet, candidates)
        profile = dict(planning["resolved_profile"])
        roles = [dict(row) for row in planning["role_plan"]]
        # Historical metric-role adapters must never reach active assignment.
        for role in roles:
            role.pop("metric_role_id", None)
        recovery_count = int(planning["recovery_count"])
        selected, recoveries, solver_audit = solve_dynamic_roles(
            candidates,
            profile,
            roles,
            recovery_count,
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, HierarchicalOptimizationError):
            raise
        raise HierarchicalOptimizationError(str(exc)) from exc

    selected_ids = {str(row["model"]) for row in selected}
    recovery_ids = {str(row["model"]) for row in recoveries}
    standby = [
        dict(row)
        for row in candidates
        if str(row["model"]) not in selected_ids | recovery_ids
    ]

    parameter_requirements = dict(planning["parameter_requirements"])
    resolved_parameters = dict(planning["resolved_parameters"])
    decomposition = dict(planning["decomposition"])
    parameter_coverage = dict(resolved_parameters.get("parameter_coverage_audit") or {})
    if parameter_coverage.get("status") != "PASS":
        raise HierarchicalOptimizationError("dynamic parameter coverage audit did not pass")

    planning_sequence = [
        str(value)
        for value in planning.get("planning_sequence") or []
        if str(value) != "runtime-feedback-replanning"
    ]
    planning_sequence.insert(0, "structural-execution-transport-compatibility")
    if planning_sequence[-1] != "ortools-model-assignment":
        raise HierarchicalOptimizationError(
            "pre-execution planning must terminate at OR-Tools model assignment"
        )
    runtime_replanning = {
        "enabled": bool(standby),
        "stage": "runtime-feedback-replanning",
        "trigger_source": "current-run-failure-and-quality-feedback",
        "promotion_depth_fixed": False,
        "cross_task_history_used": False,
    }

    audit = {
        **solver_audit,
        "schema_version": "v5-task-derived-dynamic-expert-composition-current-role-transport",
        "dynamic_parameter_graph_schema_version": DYNAMIC_PARAMETER_GRAPH_SCHEMA_VERSION,
        "planning_sequence": planning_sequence,
        "execution_transport_compatibility": transport_audit,
        "runtime_replanning": runtime_replanning,
        "task_decomposition": decomposition,
        "parameter_requirements": parameter_requirements,
        "resolved_parameters": resolved_parameters,
        "parameter_coverage_audit": parameter_coverage,
        "task_demand_profile": profile,
        "governance_candidate_count": transport_audit["governance_candidate_count"],
        "executable_candidate_count": transport_audit["executable_candidate_count"],
        "structurally_excluded_route_count": transport_audit[
            "structurally_excluded_route_count"
        ],
        "primary_expert_count": len(selected),
        "recovery_count": len(recoveries),
        "role_plan": roles,
        "selection_principles": list(base.PRINCIPLES),
        "task_adaptive_scoring_schema_version": base.TASK_SCORING_SCHEMA_VERSION,
        "role_metric_mode": "current-generated-role-structural-signals",
        "metric_role_adapter_used": False,
        "fixed_metric_role_grammar_used": False,
        "recovery_resilience": {
            "recovery_count": len(recoveries),
            "selection_source": "heaviest-current-generated-role",
            "free_route_soft_penalty": 0,
            "primary_company_overlap_soft_penalty": 0,
            "recovery_company_concentration_soft_penalty": 0,
            "free_models_forbidden": False,
            "company_diversity_hard_constraint": False,
            "provider_diversity_hard_constraint": False,
            "capacity_hard_constraint": False,
            "cross_task_failure_history_used": False,
        },
        "task_decomposition_completed": True,
        "parameter_requirement_discovery_completed": True,
        "parameter_dependency_graph_completed": True,
        "parameter_values_resolved_before_team_composition": True,
        "team_and_roles_derived_after_parameter_resolution": True,
        "role_scoring_derived_from_current_role_structure": True,
        "model_assignment_executed_after_parameter_resolution": True,
        "runtime_feedback_replanning_separate_from_planning": True,
        "all_calculable_planning_parameters_dynamic": True,
        "all_parameter_instances_current_task_derived": True,
        "fixed_parameter_template_used": False,
        "fixed_parameter_values_used": False,
        "fixed_role_grammar_used": False,
        "hard_model_eligibility_gates": [],
        "only_hard_model_boundary": "no-tools",
        "structural_execution_transport_boundary": True,
        "business_model_gate_used": False,
        "tool_use_forbidden": True,
        "fixed_team_size_used": False,
        "fixed_four_plus_four_used": False,
        "fixed_role_topology_used": False,
        "company_uniqueness_constraint_used": False,
        "top50_membership_constraint_used": False,
        "budget_constraint_used": False,
        "price_gate_used": False,
        "flagship_gate_used": False,
        "free_first_gate_used": False,
        "canary_gate_used": False,
        "provider_constraint_used": False,
        "capacity_gate_used": False,
        "optimizer_optimality_required": False,
        "semantic_keyword_routing_used": False,
        "provider_routing_mode": "unrestricted-openrouter",
        "same_model_identity_reuse_prevented_for_graph_integrity": True,
    }

    plan = dict(source_plan)
    plan.update(
        {
            "selected_models": selected,
            "recovery_models": recoveries,
            "expert_count": len(selected),
            "recovery_count": len(recoveries),
            "expert_center_ordered_standby": standby,
            "expert_center_ordered_standby_count": len(standby),
            "expert_center_pool_selection_completed": True,
            "expert_center_top50_optimization_completed": True,
            "expert_center_dynamic_composition_completed": True,
            "expert_center_hierarchical_planning_completed": True,
            "task_decomposition_completed": True,
            "parameter_requirement_discovery_completed": True,
            "parameter_dependency_graph_completed": True,
            "parameter_values_resolved_before_model_assignment": True,
            "role_scoring_derived_from_current_role_structure": True,
            "planning_sequence": planning_sequence,
            "execution_transport_compatibility": transport_audit,
            "runtime_replanning": runtime_replanning,
            "task_decomposition": decomposition,
            "dynamic_parameter_requirements": parameter_requirements,
            "dynamic_parameter_values": resolved_parameters,
            "parameter_coverage_audit": parameter_coverage,
            "selected_from_top20_reasoning_pool_only": False,
            "selected_from_top50_reasoning_pool_only": False,
            "selected_from_governance_candidate_pool": True,
            "candidate_pool_authority": "decision-system-governance",
            "governance_candidate_count": transport_audit["governance_candidate_count"],
            "expert_center_executable_candidate_count": transport_audit[
                "executable_candidate_count"
            ],
            "expert_center_structurally_excluded_route_count": transport_audit[
                "structurally_excluded_route_count"
            ],
            "model_assignment_authority": (
                "expert-assessment-center-task-derived-dynamic-ortools"
            ),
            "optimizer": str(audit["optimizer"]),
            "optimizer_audit": audit,
            "task_adaptive_scoring_completed": True,
            "task_adaptive_scoring_schema_version": base.TASK_SCORING_SCHEMA_VERSION,
            "task_demand_profile": profile,
            "selection_principles": list(base.PRINCIPLES),
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "model_substitution_allowed": True,
            "fixed_team_size_required": False,
            "fixed_role_topology_required": False,
            "fixed_role_grammar_required": False,
            "fixed_metric_role_grammar_required": False,
            "metric_role_adapter_used": False,
            "company_deduplication_required": False,
            "free_first_required": False,
            "canary_required_before_execution": False,
            "optimizer_optimality_required": False,
            "price_filter_required": False,
            "flagship_filter_required": False,
            "intelligence_rank_required": False,
            "provider_endpoint_qualification_required": False,
            "zdr_provider_qualification_required": False,
            "tool_use_forbidden": True,
            "tools_allowed": False,
            "only_hard_model_boundary": "no-tools",
            "structural_execution_transport_boundary": True,
            "all_calculable_planning_parameters_dynamic": True,
            "all_parameter_instances_current_task_derived": True,
            "selection_policy": (
                "governance reasoning-popularity candidates -> retain routes executable "
                "by the active synchronous transport -> derive current-ticket finite work "
                "DAG -> discover effective parameter instances -> build parameter DAG -> "
                "resolve current values with NetworkX/Optuna -> derive role DAG -> derive "
                "each role's demand from current structural signals -> OR-Tools model-role "
                "assignment -> current-run feedback standby promotion; unrestricted "
                "OpenRouter Provider routing; no business eligibility gates; hard model "
                "boundary=no-tools; exact identity/executable transport are structural"
            ),
        }
    )
    plan.pop("plan_sha256", None)
    selection_basis_sha256 = base._sha(plan)  # noqa: SLF001

    receipt = {
        "schema_version": "expert-center-task-derived-dynamic-selection-receipt-current-role-transport",
        "selection_basis_sha256": selection_basis_sha256,
        "planning_sequence": planning_sequence,
        "execution_transport_compatibility": transport_audit,
        "runtime_replanning": runtime_replanning,
        "task_decomposition": decomposition,
        "parameter_requirements": parameter_requirements,
        "resolved_parameters": resolved_parameters,
        "parameter_coverage_audit": parameter_coverage,
        "selected_models": [row["model"] for row in selected],
        "recovery_models": [row["model"] for row in recoveries],
        "primary_expert_count": len(selected),
        "recovery_count": len(recoveries),
        "standby_count": len(standby),
        "optimizer_audit": audit,
        "role_metric_mode": "current-generated-role-structural-signals",
        "metric_role_adapter_used": False,
        "fixed_metric_role_grammar_used": False,
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "free_first_required": False,
        "tool_use_forbidden": True,
        "tools_allowed": False,
        "only_hard_model_boundary": "no-tools",
        "structural_execution_transport_boundary": True,
        "model_calls": 0,
    }
    receipt["receipt_sha256"] = base._sha(receipt)  # noqa: SLF001
    plan["expert_center_selection_receipt"] = receipt
    plan["plan_sha256"] = base._sha(plan)  # noqa: SLF001

    materialized = dict(packet)
    materialized["governance_model_plan"] = plan
    return materialized, receipt


def materialize_candidate_pool_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _materialize(packet)


def materialize_top50_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compatibility alias; there is no Top50 admission limit."""
    return _materialize(packet)


__all__ = [
    "HierarchicalOptimizationError",
    "materialize_candidate_pool_selection",
    "materialize_top50_selection",
]
