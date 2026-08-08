"""Permissive materialization for task-dynamic expert graphs.

Only intrinsic graph validity is enforced. Historical company uniqueness,
approved-call capacity, exact Provider endpoint, price/context qualification and
fixed recovery reserve gates are removed. OpenRouter receives the selected model
identity and chooses the Provider freely. Current-task generated request/resource
ParameterSpecs are carried into every node so request-time binding can prove the
full ParameterDesign -> RuntimeBinding path.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import networkx as nx

from execution_graph import ExecutionGraph, GraphLimits, SelectedEdge, SelectedNode
from v5_task_envelope import work_output_contract


class ProposalValidationError(RuntimeError):
    """Raised only for malformed or non-executable graph structure."""


_REASONING_EFFORTS = {"low", "medium", "high"}


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _work_map(proposal: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(_rows(proposal.get("work_items")), 1):
        work_id = str(row.get("work_id") or f"work-{index}").strip()
        if work_id in result:
            raise ProposalValidationError(f"duplicate work id: {work_id}")
        result[work_id] = row
    if not result:
        raise ProposalValidationError("proposal has no work items")
    return result


def _functions(raw: Mapping[str, Any]) -> tuple[str, ...]:
    values = raw.get("functions")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    return tuple(
        dict.fromkeys(str(value).strip() for value in values if str(value).strip())
    )


def _required_outputs(
    work_map: Mapping[str, Mapping[str, Any]],
    work_ids: Sequence[str],
) -> list[str]:
    result: list[str] = []
    for work_id in work_ids:
        row = work_map.get(work_id, {})
        values = row.get("required_outputs")
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            for value in values:
                text = str(value).strip()
                if text and text not in result:
                    result.append(text)
    return result or ["完整分析结果"]


def _selected_node(
    raw: Mapping[str, Any],
    work_map: Mapping[str, Mapping[str, Any]],
    task: str,
    final_nodes: set[str],
) -> SelectedNode:
    node_id = str(raw.get("node_id") or "").strip()
    model = str(raw.get("model") or "").strip()
    work_ids = tuple(str(value) for value in raw.get("work_ids", []) if str(value))
    if not node_id or not model or not work_ids:
        raise ProposalValidationError("every node needs node_id, model and work_ids")
    unknown = [work_id for work_id in work_ids if work_id not in work_map]
    if unknown:
        raise ProposalValidationError(
            f"node {node_id} references unknown work: {unknown}"
        )
    functions = _functions(raw)
    effort = str(raw.get("reasoning_effort") or "").strip().casefold()
    if effort not in _REASONING_EFFORTS:
        raise ProposalValidationError(
            f"node {node_id} needs a valid current-task-derived reasoning_effort"
        )
    contract = work_output_contract(
        task,
        _required_outputs(work_map, work_ids),
        final_node=node_id in final_nodes,
    )
    inherited_profile = raw.get("parameter_profile")
    inherited_profile = (
        dict(inherited_profile)
        if isinstance(inherited_profile, Mapping)
        else {}
    )
    inherited_profile.update(
        {
            "selection_source": "task-dynamic-ortools",
            "local_token_ceiling_enforced": False,
            "provider_routing_mode": "unrestricted-openrouter",
        }
    )
    return SelectedNode(
        node_id=node_id,
        assigned_work=work_ids,
        professional_capabilities={value: 1.0 for value in functions},
        functions=functions,
        prompt_profile={
            "modules": list(functions),
            "role": str(raw.get("role") or "动态专家"),
            "source": "task-dynamic-expert-plan",
        },
        reasoning_profile={"reasoning_enabled": True, "effort": effort},
        parameter_profile=inherited_profile,
        model=model,
        provider_endpoint=f"{model}@openrouter-auto",
        output_contract=contract,
        estimated_quality=0.0,
        quality_uncertainty=0.0,
        estimated_cost=float(raw.get("estimated_task_cost_usd") or 0.0),
        failure_probability=0.0,
        request_config={},
        independence_group=None,
    )


def _selected_edges(proposal: Mapping[str, Any]) -> tuple[SelectedEdge, ...]:
    return tuple(
        SelectedEdge(
            source=str(row.get("source") or ""),
            target=str(row.get("target") or ""),
            relation_type=str(row.get("relation_type") or "information"),
            payload_type="structured-node-result",
            visibility_policy="declared-edge-only",
        )
        for row in _rows(proposal.get("edges"))
    )


def _stages(
    node_ids: Sequence[str],
    edges: Sequence[SelectedEdge],
) -> tuple[tuple[str, ...], ...]:
    graph = nx.DiGraph()
    graph.add_nodes_from(node_ids)
    graph.add_edges_from((edge.source, edge.target) for edge in edges)
    if not nx.is_directed_acyclic_graph(graph):
        raise ProposalValidationError("expert graph must be acyclic")
    stages: list[tuple[str, ...]] = []
    remaining = set(node_ids)
    completed: set[str] = set()
    while remaining:
        stage = tuple(
            sorted(
                node
                for node in remaining
                if all(parent in completed for parent in graph.predecessors(node))
            )
        )
        if not stage:
            raise ProposalValidationError("expert graph cannot be staged")
        stages.append(stage)
        completed.update(stage)
        remaining.difference_update(stage)
    return tuple(stages)


def _price_telemetry(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve current catalog price signals without turning them into gates."""
    model = str(raw.get("model") or "").strip()
    prompt = max(0.0, _number(raw.get("prompt_usd_per_million"), 0.0))
    completion = max(0.0, _number(raw.get("completion_usd_per_million"), 0.0))
    rank = max(0.0, _number(raw.get("price_rank_usd_per_million"), 0.0))
    explicit_task = raw.get("estimated_task_cost_usd")
    explicit_cost = raw.get("estimated_cost")
    task_cost = max(0.0, _number(explicit_task, -1.0))
    if task_cost < 0:
        task_cost = max(0.0, _number(explicit_cost, 0.0))
    zero_cost = bool(
        raw.get("zero_cost_candidate") is True
        or model.casefold().endswith(":free")
        or (
            (prompt > 0.0 or completion > 0.0 or rank > 0.0)
            and prompt == 0.0
            and completion == 0.0
            and rank == 0.0
        )
    )
    # Runtime recovery ordering needs a monotonic soft cost signal even when a
    # task-specific dollar estimate was not emitted for standby rows.  The
    # per-million price rank is not represented as actual task spend; it is kept
    # separately and used only as a tie-break signal.
    cost_rank_signal = task_cost if task_cost > 0.0 else rank
    if cost_rank_signal <= 0.0 and not zero_cost:
        cost_rank_signal = prompt + completion
    return {
        "estimated_task_cost_usd": task_cost,
        "prompt_usd_per_million": prompt,
        "completion_usd_per_million": completion,
        "price_rank_usd_per_million": rank,
        "cost_rank_signal": max(0.0, cost_rank_signal),
        "zero_cost_candidate": zero_cost,
        "cost_rank_signal_is_execution_gate": False,
    }


def _recovery_row(raw: Mapping[str, Any], node: SelectedNode) -> dict[str, Any]:
    model = str(raw.get("model") or "").strip()
    raw_profile = raw.get("parameter_profile")
    raw_profile = dict(raw_profile) if isinstance(raw_profile, Mapping) else {}
    parameter_profile = {**dict(node.parameter_profile), **raw_profile}
    parameter_profile["shared_recovery_pool"] = True
    pricing = _price_telemetry(raw)
    return {
        "candidate_id": f"recovery:{node.node_id}:{model}",
        "assigned_work": list(node.assigned_work),
        "professional_capabilities": dict(node.professional_capabilities),
        "functions": list(node.functions),
        "prompt_profile": dict(node.prompt_profile),
        "reasoning_profile": dict(node.reasoning_profile),
        "parameter_profile": parameter_profile,
        "model": model,
        "provider_endpoint": f"{model}@openrouter-auto",
        "provider_slug": "openrouter-auto",
        "output_contract": dict(node.output_contract),
        "estimated_quality": float(raw.get("estimated_quality") or 0.0),
        "quality_uncertainty": float(raw.get("quality_uncertainty") or 0.0),
        "estimated_cost": float(pricing["estimated_task_cost_usd"]),
        **pricing,
        "failure_probability": float(raw.get("failure_probability") or 0.0),
        "request_config": {},
    }


def _standby_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    model = str(raw.get("model") or "").strip()
    pricing = _price_telemetry(raw)
    return {
        "candidate_id": f"standby:{model}",
        "model": model,
        "provider_endpoint": f"{model}@openrouter-auto",
        "provider_slug": "openrouter-auto",
        "estimated_quality": float(raw.get("estimated_quality") or 0.0),
        "quality_uncertainty": float(raw.get("quality_uncertainty") or 0.0),
        "estimated_cost": float(pricing["estimated_task_cost_usd"]),
        **pricing,
        "failure_probability": float(raw.get("failure_probability") or 0.0),
        "request_config": {},
        "runtime_promotable": True,
        "promotion_source": "current-task-ordered-standby",
    }


def materialize_proposal(
    proposal: Mapping[str, Any],
    task: str,
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> tuple[ExecutionGraph, GraphLimits, dict[str, Any]]:
    del (
        task_envelope,
        catalog,
        approved_total_calls,
        governance_calls_reserved,
        approved_recovery_calls,
    )
    work_map = _work_map(proposal)
    raw_nodes = _rows(proposal.get("nodes"))
    if not raw_nodes:
        raise ProposalValidationError("proposal has no expert nodes")
    final_nodes = {
        str(value) for value in proposal.get("final_nodes", []) if str(value)
    }
    if not final_nodes:
        final_nodes = {str(raw_nodes[-1].get("node_id") or "")}
    nodes = tuple(
        _selected_node(row, work_map, task, final_nodes)
        for row in raw_nodes
    )
    node_ids = [node.node_id for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ProposalValidationError("duplicate expert node id")
    if not final_nodes.issubset(set(node_ids)):
        raise ProposalValidationError("final_nodes reference unknown experts")

    edges = _selected_edges(proposal)
    if any(
        edge.source not in node_ids or edge.target not in node_ids
        for edge in edges
    ):
        raise ProposalValidationError("edge references unknown expert")
    stages = _stages(node_ids, edges)
    incoming = {edge.target for edge in edges}

    recovery_models = _rows(proposal.get("recovery_models"))
    if not recovery_models:
        recovery_models = _rows(proposal.get("recovery"))
    recovery_pool = {
        node.node_id: [
            _recovery_row(raw, node)
            for raw in recovery_models
            if str(raw.get("model") or "").strip()
        ]
        for node in nodes
    }
    standby_inventory = [
        _standby_row(raw)
        for raw in _rows(proposal.get("standby_models"))
        if str(raw.get("model") or "").strip()
    ]

    feedback = proposal.get("runtime_feedback_replanning")
    feedback = dict(feedback) if isinstance(feedback, Mapping) else {}
    feedback.update(
        {
            "enabled": bool(standby_inventory),
            "standby_inventory_count": len(standby_inventory),
            "promotion_depth_fixed": False,
            "promotion_trigger": (
                "current-run-failure-or-quality-gate-feedback"
            ),
            "cross_task_history_used": False,
        }
    )

    graph = ExecutionGraph(
        nodes=nodes,
        edges=edges,
        execution_stages=stages,
        entry_nodes=tuple(
            node_id for node_id in node_ids if node_id not in incoming
        ),
        final_nodes=tuple(
            node_id for node_id in node_ids if node_id in final_nodes
        ),
        required_work=tuple(work_map),
        estimated_quality=0.0,
        quality_floor=0.0,
        estimated_total_cost=round(
            sum(node.estimated_cost for node in nodes),
            8,
        ),
        metadata={
            "work_items": [dict(row) for row in work_map.values()],
            "recovery_pool": recovery_pool,
            "standby_inventory": standby_inventory,
            "runtime_feedback_replanning": feedback,
            "selection_authority": "expert-assessment-center-dynamic",
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "company_uniqueness_constraint_used": False,
            "approved_call_budget_used_as_gate": False,
            "exact_provider_endpoint_used_as_gate": False,
            "fixed_topology_used": False,
            "request_resource_parameter_profile_bound": all(
                bool(
                    node.parameter_profile.get(
                        "runtime_resource_parameter_ids"
                    )
                )
                for node in nodes
            ),
            "hidden_reasoning_effort_default_used": False,
            "cost_effectiveness_priority": True,
            "soft_token_and_cost_efficiency": True,
            "standby_price_telemetry_preserved": True,
            "standby_zero_cost_candidate_count": sum(
                1 for row in standby_inventory if row.get("zero_cost_candidate") is True
            ),
        },
    )
    recovery_count = max(
        (len(rows) for rows in recovery_pool.values()),
        default=0,
    )
    finite_runtime_candidates = recovery_count + len(standby_inventory)
    limits = GraphLimits(
        max_nodes=max(1, len(nodes)),
        max_edges=max(1, len(edges)),
        max_stages=max(1, len(stages)),
        max_model_calls=max(1, len(nodes) + finite_runtime_candidates),
        max_retries=finite_runtime_candidates,
        max_replacements=finite_runtime_candidates,
        max_budget_usd=None,
        min_required_work_coverage=0.0,
        min_successful_content_nodes=0,
        allow_degraded_success=True,
        max_provider_share=1.0,
        max_provider_failures=max(
            1,
            len(nodes) + finite_runtime_candidates,
        ),
        max_output_allowance_tokens=None,
    )
    audit = {
        "schema_version": "v5-task-dynamic-materialization-5-preserve-standby-price-telemetry",
        "status": "PASS",
        "selected_node_count": len(nodes),
        "recovery_candidate_count": recovery_count,
        "standby_candidate_count": len(standby_inventory),
        "runtime_feedback_replanning_enabled": bool(standby_inventory),
        "runtime_standby_promotion_depth_fixed": False,
        "request_resource_parameter_profile_bound": all(
            bool(
                node.parameter_profile.get(
                    "runtime_resource_parameter_ids"
                )
            )
            for node in nodes
        ),
        "hidden_reasoning_effort_default_used": False,
        "cost_effectiveness_priority": True,
        "soft_token_and_cost_efficiency": True,
        "company_uniqueness_gate": False,
        "call_budget_gate": False,
        "provider_endpoint_gate": False,
        "cost_gate": False,
        "token_gate": False,
        "provider_routing_mode": "unrestricted-openrouter",
        "cost_advisory_usd": cost_anomaly_usd,
        "standby_price_telemetry_preserved": True,
        "standby_zero_cost_candidate_count": sum(
            1 for row in standby_inventory if row.get("zero_cost_candidate") is True
        ),
    }
    return graph, limits, audit


def deterministic_violations(
    proposal: Mapping[str, Any],
    task: str,
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    **limits: Any,
) -> list[str]:
    try:
        materialize_proposal(
            proposal,
            task,
            task_envelope,
            catalog,
            approved_total_calls=int(
                limits.get("approved_total_calls") or 1
            ),
            governance_calls_reserved=int(
                limits.get("governance_calls_reserved") or 0
            ),
            approved_recovery_calls=int(
                limits.get("approved_recovery_calls") or 0
            ),
            cost_anomaly_usd=limits.get("cost_anomaly_usd"),
        )
    except Exception as exc:  # noqa: BLE001
        return [str(exc)]
    return []


__all__ = [
    "ProposalValidationError",
    "deterministic_violations",
    "materialize_proposal",
]
