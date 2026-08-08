"""Production candidate optimizer with cost-effectiveness and resource closure.

This is a thin assembly layer over the existing hierarchical planner.  It swaps in
only the current production planning extensions for the duration of one ticket:
first-class request/resource ParameterDesign and the cost-effectiveness-first
OR-Tools objective.  The resulting generated parameter identities are attached to
selected nodes so request-time audits can prove ParameterSpec -> RuntimeBinding.
"""
from __future__ import annotations

from typing import Any, Mapping

import v5_hierarchical_candidate_optimizer as base
from v5_cost_effectiveness_parameter_closure import (
    PRINCIPLES,
    SCHEMA_VERSION as PARAMETER_SCHEMA_VERSION,
    build_runtime_planning_context,
)
from v5_cost_effectiveness_role_assignment import (
    SCHEMA_VERSION as ROLE_ASSIGNMENT_SCHEMA_VERSION,
    solve_runtime_roles,
)


def _resource_profile(receipt: Mapping[str, Any]) -> dict[str, Any]:
    resolved = receipt.get("resolved_parameters")
    resolved = resolved if isinstance(resolved, Mapping) else {}
    ids = resolved.get("request_resource_parameter_ids")
    values = resolved.get("request_resource_parameter_values")
    return {
        "runtime_resource_parameter_ids": dict(ids) if isinstance(ids, Mapping) else {},
        "runtime_resource_parameter_values": dict(values) if isinstance(values, Mapping) else {},
        "cost_effectiveness_priority": True,
        "soft_token_and_cost_efficiency": True,
        "continuous_spatiotemporal_resource_recomputation": True,
        "cross_task_history_used": False,
    }


def _attach_runtime_resource_profile(
    materialized: dict[str, Any],
    receipt: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan_raw = materialized.get("governance_model_plan")
    if not isinstance(plan_raw, Mapping):
        return materialized, receipt
    plan = dict(plan_raw)
    profile = _resource_profile(receipt)
    for key in ("selected_models", "recovery_models"):
        rows = []
        for raw in plan.get(key) or []:
            if not isinstance(raw, Mapping):
                continue
            row = dict(raw)
            parameter_profile = row.get("parameter_profile")
            parameter_profile = (
                dict(parameter_profile)
                if isinstance(parameter_profile, Mapping)
                else {}
            )
            parameter_profile.update(profile)
            row["parameter_profile"] = parameter_profile
            rows.append(row)
        plan[key] = rows

    receipt.update(
        {
            "schema_version": "expert-center-generated-parameter-selection-receipt-3-cost-effectiveness-resource-closure",
            "selection_principles": list(PRINCIPLES),
            "request_resource_parameter_ids": profile["runtime_resource_parameter_ids"],
            "request_resource_parameter_values": profile["runtime_resource_parameter_values"],
            "all_request_resource_controls_first_class_parameters": True,
            "cost_effectiveness_priority": True,
            "soft_token_and_cost_efficiency": True,
            "continuous_spatiotemporal_resource_recomputation": True,
        }
    )
    receipt.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = base.base._sha(receipt)  # noqa: SLF001

    plan.update(
        {
            "selection_principles": list(PRINCIPLES),
            "all_request_resource_controls_first_class_parameters": True,
            "cost_effectiveness_priority": True,
            "soft_token_and_cost_efficiency": True,
            "continuous_spatiotemporal_resource_recomputation": True,
            "expert_center_selection_receipt": receipt,
        }
    )
    plan.pop("plan_sha256", None)
    plan["plan_sha256"] = base.base._sha(plan)  # noqa: SLF001
    result = dict(materialized)
    result["governance_model_plan"] = plan
    return result, receipt


def _materialize(packet: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    original_planner = base.build_runtime_planning_context
    original_solver = base.solve_runtime_roles
    original_principles = base.PRINCIPLES
    original_parameter_schema = base.DYNAMIC_PARAMETER_GRAPH_SCHEMA_VERSION
    original_role_schema = base.RUNTIME_ROLE_ASSIGNMENT_SCHEMA_VERSION
    base.build_runtime_planning_context = build_runtime_planning_context
    base.solve_runtime_roles = solve_runtime_roles
    base.PRINCIPLES = PRINCIPLES
    base.DYNAMIC_PARAMETER_GRAPH_SCHEMA_VERSION = PARAMETER_SCHEMA_VERSION
    base.RUNTIME_ROLE_ASSIGNMENT_SCHEMA_VERSION = ROLE_ASSIGNMENT_SCHEMA_VERSION
    try:
        materialized, receipt = base.materialize_candidate_pool_selection(packet)
    finally:
        base.build_runtime_planning_context = original_planner
        base.solve_runtime_roles = original_solver
        base.PRINCIPLES = original_principles
        base.DYNAMIC_PARAMETER_GRAPH_SCHEMA_VERSION = original_parameter_schema
        base.RUNTIME_ROLE_ASSIGNMENT_SCHEMA_VERSION = original_role_schema
    return _attach_runtime_resource_profile(dict(materialized), dict(receipt))


def materialize_candidate_pool_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _materialize(packet)


def materialize_top50_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _materialize(packet)


__all__ = ["materialize_candidate_pool_selection", "materialize_top50_selection"]
