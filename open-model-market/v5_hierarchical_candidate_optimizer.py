"""Current-ticket generated-parameter candidate optimizer.

Pre-execution order:
current ticket -> constitutional no-tools route boundary -> current work DAG ->
required decision discovery -> generated ParameterSpec identities -> parameter DAG ->
current-signal/Optuna resolution -> role DAG -> current-signal model scoring ->
OR-Tools model-role assignment.

Runtime standby promotion is a separate current-run replanning phase. Stable control
surface names describe infrastructure capabilities only; business parameter identities
and values are generated after the current decisions are known.
"""
from __future__ import annotations

from typing import Any, Mapping

import v5_top50_pool_optimizer as base
from v5_no_tools_policy import forbidden_model_route
from v5_runtime_parameter_planner import (
    PRINCIPLES,
    SCHEMA_VERSION as DYNAMIC_PARAMETER_GRAPH_SCHEMA_VERSION,
    build_runtime_planning_context,
)
from v5_runtime_role_assignment import (
    SCHEMA_VERSION as RUNTIME_ROLE_ASSIGNMENT_SCHEMA_VERSION,
    solve_runtime_roles,
)


class HierarchicalOptimizationError(RuntimeError):
    """Raised only when the current ticket cannot form a finite executable plan."""


def _partition_no_tools_routes(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Apply the existing constitutional route boundary before optimization."""
    executable: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for row in rows:
        model = str(row.get("model") or "").strip()
        route = forbidden_model_route({"model": model})
        if route:
            rejected.append(
                {
                    "model": model,
                    "reason": "constitutional-no-tools-route-incompatible",
                }
            )
            continue
        executable.append(dict(row))
    return executable, rejected


def _materialize(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = packet.get("governance_model_plan")
    if not isinstance(source, Mapping):
        raise HierarchicalOptimizationError("governance_model_plan is missing")
    source_plan = dict(source)
    try:
        governance_candidates = base._candidate_rows(source_plan)  # noqa: SLF001
        candidates, no_tools_rejected = _partition_no_tools_routes(
            governance_candidates
        )
        if not candidates:
            raise HierarchicalOptimizationError(
                "no candidate survives the constitutional no-tools route boundary"
            )
        planning = build_runtime_planning_context(packet, candidates)
        profile = dict(planning["resolved_profile"])
        roles = [dict(row) for row in planning["role_plan"]]
        recovery_count = int(planning["recovery_count"])
        selected, recoveries, solver_audit = solve_runtime_roles(
            candidates,
            profile,
            roles,
            recovery_count,
        )
    except Exception as exc:  # noqa: BLE001 - normalize planner failure boundary
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
    constitutional_candidate_boundary = {
        "policy": "same-no-tools-route-policy-as-request-boundary",
        "governance_candidate_count": len(governance_candidates),
        "executable_candidate_count": len(candidates),
        "rejected_candidate_count": len(no_tools_rejected),
        "rejected_candidates": no_tools_rejected,
        "business_eligibility_gate": False,
        "price_gate": False,
        "company_gate": False,
        "provider_gate": False,
        "popularity_gate": False,
        "only_hard_model_boundary": "no-tools",
    }

    parameter_requirements = dict(planning["parameter_requirements"])
    resolved_parameters = dict(planning["resolved_parameters"])
    decomposition = dict(planning["decomposition"])
    parameter_coverage = dict(
        resolved_parameters.get("parameter_coverage_audit") or {}
    )
    if parameter_coverage.get("status") != "PASS":
        raise HierarchicalOptimizationError(
            "generated parameter coverage audit did not pass"
        )

    planning_sequence = [
        str(value)
        for value in planning.get("planning_sequence") or []
        if str(value) != "runtime-feedback-replanning"
    ]
    if not planning_sequence or planning_sequence[-1] != "ortools-model-assignment":
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
        "schema_version": "current-ticket-generated-expert-composition-1",
        "dynamic_parameter_graph_schema_version": (
            DYNAMIC_PARAMETER_GRAPH_SCHEMA_VERSION
        ),
        "runtime_role_assignment_schema_version": (
            RUNTIME_ROLE_ASSIGNMENT_SCHEMA_VERSION
        ),
        "planning_sequence": planning_sequence,
        "runtime_replanning": runtime_replanning,
        "constitutional_candidate_boundary": constitutional_candidate_boundary,
        "task_decomposition": decomposition,
        "parameter_requirements": parameter_requirements,
        "resolved_parameters": resolved_parameters,
        "parameter_coverage_audit": parameter_coverage,
        "task_demand_profile": profile,
        "primary_expert_count": len(selected),
        "recovery_count": len(recoveries),
        "role_plan": roles,
        "selection_principles": list(PRINCIPLES),
        "task_adaptive_scoring_schema_version": (
            RUNTIME_ROLE_ASSIGNMENT_SCHEMA_VERSION
        ),
        "role_metric_mode": "current-role-current-task-normalized-signals",
        "metric_role_adapter_used": False,
        "fixed_metric_role_grammar_used": False,
        "fixed_business_weight_coefficients_used": False,
        "fixed_business_solver_time_used": False,
        "recovery_resilience": {
            "recovery_count": len(recoveries),
            "selection_source": "heaviest-current-role-normalized-objective",
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
        "required_decision_discovery_completed": True,
        "parameter_ids_generated_after_decision_discovery": True,
        "parameter_dependency_graph_completed": True,
        "parameter_values_resolved_before_team_composition": True,
        "team_and_roles_derived_after_parameter_resolution": True,
        "role_scoring_derived_from_current_role_structure": True,
        "model_assignment_executed_after_parameter_resolution": True,
        "runtime_feedback_replanning_separate_from_planning": True,
        "all_calculable_planning_parameters_dynamic": True,
        "all_parameter_instances_current_task_derived": True,
        "fixed_parameter_template_used": False,
        "fixed_business_parameter_catalog_used": False,
        "legacy_business_parameter_names_used_as_parameter_ids": False,
        "fixed_parameter_values_used": False,
        "fixed_role_grammar_used": False,
        "hard_model_eligibility_gates": [],
        "constitutional_no_tools_route_prefilter_applied": True,
        "constitutional_no_tools_route_rejected_count": len(no_tools_rejected),
        "only_hard_model_boundary": "no-tools",
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
            "required_decision_discovery_completed": True,
            "parameter_ids_generated_after_decision_discovery": True,
            "parameter_dependency_graph_completed": True,
            "parameter_values_resolved_before_model_assignment": True,
            "role_scoring_derived_from_current_role_structure": True,
            "constitutional_candidate_boundary": constitutional_candidate_boundary,
            "planning_sequence": planning_sequence,
            "runtime_replanning": runtime_replanning,
            "task_decomposition": decomposition,
            "dynamic_parameter_requirements": parameter_requirements,
            "dynamic_parameter_values": resolved_parameters,
            "parameter_coverage_audit": parameter_coverage,
            "selected_from_top20_reasoning_pool_only": False,
            "selected_from_top50_reasoning_pool_only": False,
            "selected_from_governance_candidate_pool": True,
            "candidate_pool_authority": "decision-system-governance",
            "model_assignment_authority": (
                "expert-assessment-center-current-ticket-generated-parameter-ortools"
            ),
            "optimizer": str(audit["optimizer"]),
            "optimizer_audit": audit,
            "task_adaptive_scoring_completed": True,
            "task_adaptive_scoring_schema_version": (
                RUNTIME_ROLE_ASSIGNMENT_SCHEMA_VERSION
            ),
            "task_demand_profile": profile,
            "selection_principles": list(PRINCIPLES),
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "model_substitution_allowed": True,
            "fixed_team_size_required": False,
            "fixed_role_topology_required": False,
            "fixed_role_grammar_required": False,
            "fixed_metric_role_grammar_required": False,
            "metric_role_adapter_used": False,
            "fixed_business_weight_coefficients_used": False,
            "fixed_business_parameter_catalog_used": False,
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
            "all_calculable_planning_parameters_dynamic": True,
            "all_parameter_instances_current_task_derived": True,
            "selection_policy": (
                "governance candidates -> constitutional no-tools route boundary -> "
                "current-ticket work DAG -> discover required decisions -> generate "
                "opaque current ParameterSpec identities -> generated parameter DAG -> "
                "resolve current values with graph signals and conditional Optuna -> "
                "derive arbitrary current role DAG and empirical reasoning effort -> "
                "normalize current role/task scoring strengths without fixed business "
                "coefficients -> OR-Tools assignment -> current-run standby replanning"
            ),
        }
    )
    plan.pop("plan_sha256", None)
    selection_basis_sha256 = base._sha(plan)  # noqa: SLF001

    receipt = {
        "schema_version": "expert-center-generated-parameter-selection-receipt-1",
        "selection_basis_sha256": selection_basis_sha256,
        "planning_sequence": planning_sequence,
        "runtime_replanning": runtime_replanning,
        "constitutional_candidate_boundary": constitutional_candidate_boundary,
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
        "role_metric_mode": "current-role-current-task-normalized-signals",
        "metric_role_adapter_used": False,
        "fixed_metric_role_grammar_used": False,
        "fixed_business_weight_coefficients_used": False,
        "parameter_ids_generated_after_decision_discovery": True,
        "constitutional_no_tools_route_prefilter_applied": True,
        "constitutional_no_tools_route_rejected_count": len(no_tools_rejected),
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "free_first_required": False,
        "tool_use_forbidden": True,
        "tools_allowed": False,
        "only_hard_model_boundary": "no-tools",
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
