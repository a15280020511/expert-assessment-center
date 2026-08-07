"""Build a task-dynamic expert DAG from the current expert plan.

The orchestrator does not impose a fixed team size, 4+4 layout, company mix,
Top50 membership, fixed role family, exact Provider endpoint, or fixed topology.
It materializes whatever current-task role plan the Expert Center selected while
ensuring the graph is finite and acyclic.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import networkx as nx

from v5_governance_model_plan import validate_governance_model_plan


class GovernedPlanOrchestrationError(RuntimeError):
    """Raised only when a dynamic plan cannot form a finite executable DAG."""


def _role_kind(row: Mapping[str, Any], index: int, count: int) -> str:
    explicit = str(row.get("role_kind") or "").strip().casefold()
    if explicit in {"independent", "review", "synthesis"}:
        return explicit
    if count == 1 or index == count - 1:
        return "synthesis"
    return "independent"


def _functions(kind: str) -> list[str]:
    if kind == "review":
        return ["cross_review", "adversarial_testing", "conflict_resolution"]
    if kind == "synthesis":
        return ["final_synthesis", "decision_integration", "output_contract_completion"]
    return ["independent_analysis", "evidence_assessment", "assumption_testing"]


def _node_id(index: int, kind: str) -> str:
    return f"expert-{index + 1}-{kind}"


def _work_id(index: int, kind: str) -> str:
    return f"work-{index + 1}-{kind}"


def _dependencies(
    kinds: Sequence[str],
    index: int,
) -> list[int]:
    kind = kinds[index]
    if kind == "independent":
        return []
    if kind == "review":
        prior_independents = [i for i in range(index) if kinds[i] == "independent"]
        return prior_independents or list(range(index))
    # Synthesis receives every already-completed perspective. A sole synthesis
    # expert has no dependency and directly completes the task.
    return list(range(index))


def build_governed_proposal(
    *,
    ticket: Mapping[str, Any],
    catalog: Mapping[str, Any],
    task_envelope: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    del catalog, task_envelope
    plan = validate_governance_model_plan(ticket)
    selected = list(plan.get("selected_models") or [])
    recoveries = [dict(row) for row in plan.get("recovery_models") or [] if isinstance(row, Mapping)]
    if not selected:
        raise GovernedPlanOrchestrationError("dynamic expert plan has no selected models")

    kinds = [_role_kind(row, index, len(selected)) for index, row in enumerate(selected)]
    if "synthesis" not in kinds:
        kinds[-1] = "synthesis"

    work_items: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    for index, (row, kind) in enumerate(zip(selected, kinds, strict=True)):
        dependency_indices = _dependencies(kinds, index)
        work_id = _work_id(index, kind)
        node_id = _node_id(index, kind)
        dependencies = [_work_id(parent, kinds[parent]) for parent in dependency_indices]
        work_items.append(
            {
                "work_id": work_id,
                "objective": str(row.get("role") or f"动态{kind}专家处理当前任务"),
                "dependencies": dependencies,
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
                "node_id": node_id,
                "work_ids": [work_id],
                "role": str(row.get("role") or f"动态{kind}专家"),
                "role_kind": kind,
                "functions": _functions(kind),
                "model": str(row.get("model") or ""),
                "reasoning_effort": "high" if kind in {"review", "synthesis"} else "medium",
                "estimated_task_cost_usd": float(row.get("estimated_task_cost_usd") or 0.0),
            }
        )
        edges.extend(
            {
                "source": _node_id(parent, kinds[parent]),
                "target": node_id,
                "relation_type": "review" if kind == "review" else "synthesis",
            }
            for parent in dependency_indices
        )

    graph = nx.DiGraph()
    graph.add_nodes_from(str(row["node_id"]) for row in nodes)
    graph.add_edges_from((row["source"], row["target"]) for row in edges)
    if not nx.is_directed_acyclic_graph(graph):
        raise GovernedPlanOrchestrationError("dynamic expert graph is cyclic")

    final_nodes = [
        str(nodes[index]["node_id"])
        for index, kind in enumerate(kinds)
        if kind == "synthesis" and graph.out_degree(str(nodes[index]["node_id"])) == 0
    ]
    if not final_nodes:
        final_nodes = [str(nodes[-1]["node_id"])]

    proposal = {
        "work_items": work_items,
        "nodes": nodes,
        "edges": edges,
        "final_nodes": final_nodes,
        "recovery_models": recoveries,
    }
    audit = {
        "schema_version": "v5-task-dynamic-plan-materialization-1",
        "status": "PASS",
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center-dynamic-ortools",
        "selected_model_count": len(selected),
        "recovery_model_count": len(recoveries),
        "fixed_team_size_used": False,
        "fixed_four_plus_four_used": False,
        "fixed_role_topology_used": False,
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
