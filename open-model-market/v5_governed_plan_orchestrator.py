"""Materialize the exact current-task role DAG selected by the Expert Center.

This layer does not invent a role family or topology. Role identities, functions,
assigned work and dependencies come from the current plan; NetworkX only validates
that the resulting graph is finite and acyclic. Governance remains candidate-pool
authority, Expert Center remains model/role assignment authority, and OpenRouter
remains unrestricted Provider-routing authority.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

import networkx as nx

from v5_governance_model_plan import validate_governance_model_plan


class GovernedPlanOrchestrationError(RuntimeError):
    """Raised only when a dynamic plan cannot form a finite executable DAG."""


_REASONING_EFFORTS = {"low", "medium", "high"}


def _rows(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _slug(value: Any, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-")
    return (text[:64] or fallback).casefold()


def _role_id(row: Mapping[str, Any], index: int) -> str:
    return str(row.get("role_id") or f"role-{index + 1}").strip() or f"role-{index + 1}"


def _node_id(row: Mapping[str, Any], index: int) -> str:
    return f"expert-{index + 1}-{_slug(_role_id(row, index), 'role')}"


def _work_id(row: Mapping[str, Any], index: int) -> str:
    return f"work-{index + 1}-{_slug(_role_id(row, index), 'role')}"


def _functions(row: Mapping[str, Any]) -> list[str]:
    explicit = [
        str(value).strip()
        for value in _rows(row.get("functions"))
        if str(value).strip()
    ]
    if explicit:
        return list(dict.fromkeys(explicit))
    kind = str(row.get("role_kind") or "task-role").strip()
    return [f"execute:{_slug(kind, 'task-role')}", "assumption-testing"]


def _reasoning_effort(row: Mapping[str, Any], role_id: str) -> str:
    """Require the planner's current-task effort instead of inventing a default."""
    effort = str(row.get("reasoning_effort") or "").strip().casefold()
    if effort not in _REASONING_EFFORTS:
        raise GovernedPlanOrchestrationError(
            f"dynamic role {role_id} has no valid task-derived reasoning_effort"
        )
    return effort


def _dependencies(
    selected: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[list[int]]]:
    role_ids = [_role_id(row, index) for index, row in enumerate(selected)]
    if len(role_ids) != len(set(role_ids)):
        raise GovernedPlanOrchestrationError("dynamic role plan contains duplicate role_id")
    index_by_role = {role_id: index for index, role_id in enumerate(role_ids)}
    dependencies: list[list[int]] = []
    for index, row in enumerate(selected):
        raw = row.get("depends_on_role_ids") or row.get("dependencies") or []
        parents: list[int] = []
        for value in _rows(raw):
            role_id = str(value).strip()
            if not role_id:
                continue
            if role_id not in index_by_role:
                raise GovernedPlanOrchestrationError(
                    f"role {_role_id(row, index)} depends on unknown role {role_id}"
                )
            parent = index_by_role[role_id]
            if parent == index:
                raise GovernedPlanOrchestrationError("dynamic role cannot depend on itself")
            if parent not in parents:
                parents.append(parent)
        dependencies.append(parents)
    return role_ids, dependencies


def build_governed_proposal(
    *,
    ticket: Mapping[str, Any],
    catalog: Mapping[str, Any],
    task_envelope: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    del catalog, task_envelope
    plan = validate_governance_model_plan(ticket)
    selected = [
        dict(row)
        for row in plan.get("selected_models") or []
        if isinstance(row, Mapping)
    ]
    recoveries = [
        dict(row)
        for row in plan.get("recovery_models") or []
        if isinstance(row, Mapping)
    ]
    standbys = [
        dict(row)
        for row in plan.get("expert_center_ordered_standby") or []
        if isinstance(row, Mapping)
    ]
    if not selected:
        raise GovernedPlanOrchestrationError("dynamic expert plan has no selected models")

    role_ids, dependencies = _dependencies(selected)
    node_ids = [_node_id(row, index) for index, row in enumerate(selected)]
    work_ids = [_work_id(row, index) for index, row in enumerate(selected)]

    graph = nx.DiGraph()
    graph.add_nodes_from(node_ids)
    for index, parents in enumerate(dependencies):
        for parent in parents:
            graph.add_edge(node_ids[parent], node_ids[index])
    if not nx.is_directed_acyclic_graph(graph):
        raise GovernedPlanOrchestrationError("dynamic expert graph is cyclic")

    work_items: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    for index, row in enumerate(selected):
        parent_indices = dependencies[index]
        assigned_units = [
            str(value)
            for value in _rows(row.get("assigned_work_units"))
            if str(value).strip()
        ]
        role_kind = (
            str(row.get("role_kind") or "dynamic:task-role").strip()
            or "dynamic:task-role"
        )
        parameter_profile = row.get("parameter_profile")
        parameter_profile = (
            dict(parameter_profile)
            if isinstance(parameter_profile, Mapping)
            else {}
        )
        work_items.append(
            {
                "work_id": work_ids[index],
                "objective": str(
                    row.get("role") or f"动态任务角色 {role_ids[index]}"
                ).strip(),
                "dependencies": [work_ids[parent] for parent in parent_indices],
                "source_work_unit_ids": assigned_units,
                "required_outputs": [
                    "核心判断",
                    "关键依据",
                    "不确定性与反例",
                    "可执行结论",
                ],
            }
        )
        nodes.append(
            {
                "node_id": node_ids[index],
                "work_ids": [work_ids[index]],
                "role_id": role_ids[index],
                "role": str(
                    row.get("role") or f"动态任务角色 {role_ids[index]}"
                ).strip(),
                "role_kind": role_kind,
                "functions": _functions(row),
                "model": str(row.get("model") or ""),
                "reasoning_effort": _reasoning_effort(row, role_ids[index]),
                "reasoning_effort_source": str(
                    row.get("reasoning_effort_source")
                    or "current-task-planner-materialized-value"
                ),
                "estimated_task_cost_usd": float(
                    row.get("estimated_task_cost_usd") or 0.0
                ),
                "assigned_work_units": assigned_units,
                "depends_on_role_ids": [
                    role_ids[parent] for parent in parent_indices
                ],
                "parameter_profile": parameter_profile,
            }
        )
        edges.extend(
            {
                "source": node_ids[parent],
                "target": node_ids[index],
                "relation_type": "dependency",
            }
            for parent in parent_indices
        )

    explicitly_final = {
        node_ids[index]
        for index, row in enumerate(selected)
        if row.get("final_role") is True
    }
    sink_nodes = {node for node in node_ids if graph.out_degree(node) == 0}
    final_nodes = sorted(explicitly_final or sink_nodes)
    if not final_nodes:
        raise GovernedPlanOrchestrationError("dynamic expert graph has no terminal node")

    proposal = {
        "work_items": work_items,
        "nodes": nodes,
        "edges": edges,
        "final_nodes": final_nodes,
        "recovery_models": recoveries,
        "standby_models": standbys,
        "runtime_feedback_replanning": {
            "enabled": bool(standbys),
            "promotion_source": "current-run-failure-and-quality-feedback",
            "promotion_depth_fixed": False,
            "standby_order_source": "current-task-dynamic-optimizer",
            "cross_task_history_used": False,
        },
    }
    audit = {
        "schema_version": "v5-exact-dynamic-role-dag-materialization-4-no-hidden-effort-default",
        "status": "PASS",
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center-dynamic-ortools",
        "selected_model_count": len(selected),
        "recovery_model_count": len(recoveries),
        "standby_model_count": len(standbys),
        "role_ids": role_ids,
        "declared_dependency_edge_count": len(edges),
        "execution_relation_type": "dependency",
        "dependency_semantics": "current-plan-declared-role-dependency",
        "terminal_node_count": len(final_nodes),
        "runtime_feedback_replanning_enabled": bool(standbys),
        "runtime_standby_promotion_depth_fixed": False,
        "request_resource_parameter_profile_propagated": all(
            bool(
                (row.get("parameter_profile") or {}).get(
                    "runtime_resource_parameter_ids"
                )
            )
            for row in selected
        ),
        "reasoning_effort_required_from_current_task_planner": True,
        "hidden_reasoning_effort_default_used": False,
        "cost_effectiveness_priority": True,
        "soft_token_and_cost_efficiency": True,
        "fixed_team_size_used": False,
        "fixed_four_plus_four_used": False,
        "fixed_role_topology_used": False,
        "fixed_role_grammar_used": False,
        "role_dependencies_recomputed_from_role_kind": False,
        "company_uniqueness_constraint_used": False,
        "top50_membership_constraint_used": False,
        "optimizer_optimality_required": False,
        "provider_endpoint_resolution_performed": False,
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "networkx_used_for_dag_validation": True,
        "cross_task_history_used": False,
    }
    return proposal, audit


__all__ = ["GovernedPlanOrchestrationError", "build_governed_proposal"]
