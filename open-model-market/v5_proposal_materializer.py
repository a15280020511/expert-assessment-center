"""Deterministically materialize a GPT-authored task and expert graph.

GPT owns task decomposition, roles, functions, expert composition, and recovery
order. This module never classifies tasks, scores capabilities, ranks candidates,
repairs proposals, or invents work. It validates exact contracts only.
"""
from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from typing import Any, Mapping, Sequence

import networkx as nx

from execution_graph import ExecutionGraph, GraphLimits, SelectedEdge, SelectedNode
from execution_graph_validator import derive_execution_stages, validate_execution_graph
from v5_catalog_view import GOVERNANCE_COMPANIES, catalog_index
from v5_model_company import canonical_model_company
from v5_task_envelope import work_output_contract

COST_RISK_MULTIPLIER = 1.18
MAX_WORK_ITEMS = 32
MAX_EDGE_COUNT = 64


class ProposalValidationError(RuntimeError):
    """Raised when a GPT proposal violates deterministic constraints."""


def _work_map(proposal: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = proposal.get("work_items")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_WORK_ITEMS:
        raise ProposalValidationError("proposal work_items are missing or oversized")
    result: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ProposalValidationError(f"work_items[{index}] must be an object")
        work_id = str(row.get("work_id") or "")
        objective = str(row.get("objective") or "").strip()
        dependencies = row.get("dependencies")
        required_outputs = row.get("required_outputs")
        if not work_id or work_id in result:
            raise ProposalValidationError("invalid or duplicate work id")
        if not objective:
            raise ProposalValidationError("every work item needs an objective")
        if not isinstance(dependencies, list) or len(dependencies) > MAX_WORK_ITEMS:
            raise ProposalValidationError("work dependencies must be bounded lists")
        if len(dependencies) != len(set(dependencies)):
            raise ProposalValidationError("work dependencies contain duplicates")
        if not isinstance(required_outputs, list) or not required_outputs:
            raise ProposalValidationError("every work item needs required outputs")
        result[work_id] = row
    known = set(result)
    dag = nx.DiGraph()
    dag.add_nodes_from(known)
    for work_id, row in result.items():
        for dependency in row.get("dependencies", []):
            source = str(dependency)
            if source not in known or source == work_id:
                raise ProposalValidationError("work dependency references are invalid")
            dag.add_edge(source, work_id)
    if not nx.is_directed_acyclic_graph(dag):
        raise ProposalValidationError("work dependency graph must be acyclic")
    return result


def _functions(raw: Mapping[str, Any]) -> tuple[str, ...]:
    values = tuple(
        dict.fromkeys(
            str(value).strip()
            for value in raw.get("functions", [])
            if str(value).strip()
        )
    )
    if not values:
        raise ProposalValidationError("every expert node needs functions")
    return values


def _required_outputs(
    work_map: Mapping[str, Mapping[str, Any]],
    work_ids: Sequence[str],
) -> list[str]:
    values: list[str] = []
    for work_id in work_ids:
        for raw in work_map[work_id].get("required_outputs", []):
            value = str(raw).strip()
            if value and value not in values:
                values.append(value)
    return values


def _estimated_cost(
    endpoint: Mapping[str, Any],
    task_envelope: Mapping[str, Any],
    work_map: Mapping[str, Mapping[str, Any]],
    work_ids: Sequence[str],
    max_output_tokens: int,
) -> float:
    task_characters = max(1, int(task_envelope.get("task_characters") or 1))
    dependency_count = sum(
        len(work_map[work_id].get("dependencies", []))
        for work_id in work_ids
    )
    prompt_tokens = task_characters + 4_096 + 2_048 * dependency_count
    prompt = float(endpoint.get("prompt_price_per_million", 0.0) or 0.0)
    completion = float(
        endpoint.get("completion_price_per_million", 0.0) or 0.0
    )
    cost = (
        prompt_tokens * prompt + int(max_output_tokens) * completion
    ) / 1_000_000
    return round(max(0.0, cost), 8)


def _request_config(
    endpoint: Mapping[str, Any],
    effort: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    supported = {
        str(value).casefold()
        for value in endpoint.get("supported_parameters", [])
    }
    result: dict[str, Any] = {
        "provider": {
            "only": [str(endpoint["provider"])],
            "order": [str(endpoint["provider"])],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
    }
    if "reasoning" in supported:
        result["reasoning"] = {"effort": effort, "exclude": True}
    if "max_completion_tokens" in supported:
        result["max_completion_tokens"] = int(max_output_tokens)
    elif "max_tokens" in supported:
        result["max_tokens"] = int(max_output_tokens)
    return result


def _selected_node(
    raw: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    work_map: Mapping[str, Mapping[str, Any]],
    task: str,
    task_envelope: Mapping[str, Any],
    *,
    final_node: bool,
) -> SelectedNode:
    work_ids = tuple(str(value) for value in raw.get("work_ids", []))
    effort = str(raw.get("reasoning_effort") or "medium")
    max_output = int(raw.get("max_output_tokens") or 0)
    endpoint_maximum = int(endpoint.get("max_completion_tokens") or 0)
    if not 256 <= max_output <= endpoint_maximum:
        raise ProposalValidationError("node output allowance exceeds endpoint")
    required_context = int(task_envelope.get("required_context_tokens") or 0)
    if int(endpoint.get("context_length") or 0) < required_context:
        raise ProposalValidationError("node endpoint lacks required context capacity")
    functions = _functions(raw)
    contract = work_output_contract(
        task,
        _required_outputs(work_map, work_ids),
        final_node=final_node,
    )
    return SelectedNode(
        node_id=str(raw.get("node_id") or ""),
        assigned_work=work_ids,
        professional_capabilities={value: 1.0 for value in functions},
        functions=functions,
        prompt_profile={
            "modules": list(functions),
            "role": str(raw.get("role") or ""),
            "source": "gpt-authored-task-and-expert-graph",
        },
        reasoning_profile={
            "reasoning_enabled": "reasoning" in {
                str(value).casefold()
                for value in endpoint.get("supported_parameters", [])
            },
            "effort": effort,
        },
        parameter_profile={
            "supported_parameters": list(
                endpoint.get("supported_parameters", [])
            ),
            "recommended_output_allowance_tokens": max_output,
            "selection_source": "gpt-direct-no-local-scoring",
        },
        model=str(raw.get("model") or ""),
        provider_endpoint=str(endpoint.get("provider_endpoint") or ""),
        output_contract=contract,
        estimated_quality=0.0,
        quality_uncertainty=0.0,
        estimated_cost=_estimated_cost(
            endpoint,
            task_envelope,
            work_map,
            work_ids,
            max_output,
        ),
        failure_probability=0.0,
        request_config=_request_config(endpoint, effort, max_output),
        independence_group=None,
    )


def _recovery_row(
    raw: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    selected: SelectedNode,
    task_envelope: Mapping[str, Any],
    work_map: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    maximum = int(
        selected.parameter_profile.get(
            "recommended_output_allowance_tokens", 2048
        )
    )
    if maximum > int(endpoint.get("max_completion_tokens") or 0):
        raise ProposalValidationError("recovery output allowance exceeds endpoint")
    if int(endpoint.get("context_length") or 0) < int(
        task_envelope.get("required_context_tokens") or 0
    ):
        raise ProposalValidationError("recovery endpoint lacks required context capacity")
    return {
        "candidate_id": (
            f"recovery:{selected.node_id}:"
            f"{raw.get('model')}@{raw.get('provider')}"
        ),
        "assigned_work": list(selected.assigned_work),
        "professional_capabilities": dict(selected.professional_capabilities),
        "functions": list(selected.functions),
        "prompt_profile": dict(selected.prompt_profile),
        "reasoning_profile": dict(selected.reasoning_profile),
        "parameter_profile": {
            **dict(selected.parameter_profile),
            "supported_parameters": list(
                endpoint.get("supported_parameters", [])
            ),
        },
        "model": str(raw.get("model") or ""),
        "provider_endpoint": str(endpoint.get("provider_endpoint") or ""),
        "provider_slug": str(endpoint.get("provider") or ""),
        "output_contract": dict(selected.output_contract),
        "estimated_quality": 0.0,
        "quality_uncertainty": 0.0,
        "estimated_cost": _estimated_cost(
            endpoint,
            task_envelope,
            work_map,
            selected.assigned_work,
            maximum,
        ),
        "failure_probability": 0.0,
        "request_config": _request_config(
            endpoint,
            str(selected.reasoning_profile.get("effort") or "medium"),
            maximum,
        ),
    }


def _dependency_violations(
    graph: ExecutionGraph,
    work_map: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    node_for_work = {
        work_id: node.node_id
        for node in graph.nodes
        for work_id in node.assigned_work
    }
    edge_pairs = {(edge.source, edge.target) for edge in graph.edges}
    violations: list[str] = []
    for work_id, work in work_map.items():
        target = node_for_work.get(work_id)
        for dependency in work.get("dependencies", []):
            source = node_for_work.get(str(dependency))
            if source and target and source != target and (source, target) not in edge_pairs:
                violations.append(f"missing-dependency-edge:{dependency}->{work_id}")
    return violations


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
    work_map = _work_map(proposal)
    endpoints = catalog_index(catalog)
    raw_nodes = proposal.get("nodes")
    raw_edges = proposal.get("edges")
    raw_final = proposal.get("final_nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ProposalValidationError("proposal nodes are missing")
    if not isinstance(raw_edges, list) or not isinstance(raw_final, list):
        raise ProposalValidationError("proposal edges/final_nodes are invalid")
    if len(raw_edges) > MAX_EDGE_COUNT:
        raise ProposalValidationError("proposal exceeds edge limit")

    maximum_initial = (
        int(approved_total_calls)
        - int(governance_calls_reserved)
        - int(approved_recovery_calls)
    )
    if maximum_initial < 1 or len(raw_nodes) > maximum_initial:
        raise ProposalValidationError("proposal exceeds expert initial-call capacity")

    raw_node_ids = {
        str(row.get("node_id") or "")
        for row in raw_nodes
        if isinstance(row, Mapping)
    }
    final_nodes = tuple(str(value) for value in raw_final)
    if not final_nodes or not set(final_nodes).issubset(raw_node_ids):
        raise ProposalValidationError("final_nodes reference unknown nodes")

    selected: list[SelectedNode] = []
    selected_companies: list[str] = []
    recovery_pool: dict[str, list[dict[str, Any]]] = {}
    recovery_companies: list[str] = []
    covered: list[str] = []
    for raw in raw_nodes:
        if not isinstance(raw, Mapping):
            raise ProposalValidationError("node must be an object")
        work_ids = [str(value) for value in raw.get("work_ids", [])]
        if not work_ids or any(work_id not in work_map for work_id in work_ids):
            raise ProposalValidationError("node references unknown work")
        key = (str(raw.get("model") or ""), str(raw.get("provider") or ""))
        endpoint = endpoints.get(key)
        if endpoint is None:
            raise ProposalValidationError(f"unknown exact endpoint: {key}")
        company = canonical_model_company(key[0])
        if company in GOVERNANCE_COMPANIES:
            raise ProposalValidationError("governance company cannot be an expert")
        selected_companies.append(company)
        node = _selected_node(
            raw,
            endpoint,
            work_map,
            task,
            task_envelope,
            final_node=str(raw.get("node_id") or "") in final_nodes,
        )
        selected.append(node)
        covered.extend(work_ids)

        recovery_rows: list[dict[str, Any]] = []
        for recovery in raw.get("recovery", []):
            if not isinstance(recovery, Mapping):
                raise ProposalValidationError("recovery row must be an object")
            recovery_key = (
                str(recovery.get("model") or ""),
                str(recovery.get("provider") or ""),
            )
            recovery_endpoint = endpoints.get(recovery_key)
            if recovery_endpoint is None:
                raise ProposalValidationError(
                    f"unknown exact recovery endpoint: {recovery_key}"
                )
            recovery_company = canonical_model_company(recovery_key[0])
            if recovery_company in GOVERNANCE_COMPANIES:
                raise ProposalValidationError(
                    "governance company cannot be a recovery expert"
                )
            recovery_companies.append(recovery_company)
            recovery_rows.append(
                _recovery_row(
                    recovery,
                    recovery_endpoint,
                    node,
                    task_envelope,
                    work_map,
                )
            )
        recovery_pool[node.node_id] = recovery_rows

    if any(count != 1 for count in Counter(covered).values()):
        raise ProposalValidationError("each required work must be assigned once")
    if set(covered) != set(work_map):
        raise ProposalValidationError("proposal does not cover exact required work")
    all_companies = selected_companies + recovery_companies
    if len(all_companies) != len(set(all_companies)):
        raise ProposalValidationError(
            "expert and recovery companies must be globally unique"
        )
    if len(recovery_companies) > int(approved_recovery_calls):
        raise ProposalValidationError("recovery proposal exceeds approved reserve")

    edges = tuple(
        SelectedEdge(
            source=str(row.get("source") or ""),
            target=str(row.get("target") or ""),
            relation_type=str(row.get("relation_type") or ""),
            payload_type="structured-node-result",
            visibility_policy="declared-edge-only",
        )
        for row in raw_edges
        if isinstance(row, Mapping)
    )
    provisional = ExecutionGraph(
        nodes=tuple(selected),
        edges=edges,
        execution_stages=(tuple(sorted(raw_node_ids)),),
        entry_nodes=(),
        final_nodes=final_nodes,
        required_work=tuple(work_map),
        estimated_quality=0.0,
        quality_floor=0.0,
        estimated_total_cost=round(
            sum(node.estimated_cost for node in selected), 8
        ),
        metadata={
            "work_items": [dict(row) for row in proposal.get("work_items", [])],
            "recovery_pool": recovery_pool,
            "selection_authority": "gpt-direct",
            "local_task_classification_used": False,
            "local_atomic_work_generation_used": False,
            "local_resource_matrix_used": False,
            "local_scoring_used": False,
            "optimizer_used": False,
            "cp_sat_used": False,
            "pareto_pruning_used": False,
            "heuristic_ranking_used": False,
        },
    )
    stages = derive_execution_stages(provisional)
    incoming = {edge.target for edge in edges}
    graph = ExecutionGraph(
        nodes=tuple(selected),
        edges=edges,
        execution_stages=stages,
        entry_nodes=tuple(sorted(raw_node_ids - incoming)),
        final_nodes=final_nodes,
        required_work=tuple(work_map),
        estimated_quality=0.0,
        quality_floor=0.0,
        estimated_total_cost=provisional.estimated_total_cost,
        metadata=dict(provisional.metadata),
    )
    limits = GraphLimits(
        max_nodes=maximum_initial,
        max_edges=MAX_EDGE_COUNT,
        max_stages=16,
        max_model_calls=maximum_initial,
        max_retries=0,
        max_replacements=int(approved_recovery_calls),
        max_budget_usd=cost_anomaly_usd,
        min_required_work_coverage=1.0,
        min_successful_content_nodes=1,
        allow_degraded_success=False,
        cost_risk_multiplier=COST_RISK_MULTIPLIER,
    )
    issues = list(validate_execution_graph(graph, limits))
    dependency_issues = _dependency_violations(graph, work_map)
    if issues or dependency_issues:
        messages = [f"{issue.code}:{issue.message}" for issue in issues]
        messages.extend(dependency_issues)
        raise ProposalValidationError("; ".join(messages))

    total_risk_cost = graph.estimated_total_cost * COST_RISK_MULTIPLIER
    total_risk_cost += sum(
        float(row.get("estimated_cost", 0.0)) * COST_RISK_MULTIPLIER
        for rows in recovery_pool.values()
        for row in rows
    )
    if (
        cost_anomaly_usd is not None
        and total_risk_cost > float(cost_anomaly_usd) + 1e-12
    ):
        raise ProposalValidationError("proposal exceeds risk-adjusted cost guard")

    audit = {
        "schema_version": "v5-gpt-proposal-materialization-2",
        "status": "PASS",
        "work_item_count": len(work_map),
        "selected_node_count": len(selected),
        "selected_companies": selected_companies,
        "recovery_companies": recovery_companies,
        "maximum_expert_initial_calls": maximum_initial,
        "risk_adjusted_reserved_cost_usd": round(total_risk_cost, 8),
        "local_task_classification_used": False,
        "local_atomic_work_generation_used": False,
        "local_resource_matrix_used": False,
        "local_scoring_used": False,
        "optimizer_used": False,
        "proposal_repaired_by_validator": False,
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
            **limits,
        )
    except Exception as exc:  # noqa: BLE001
        return [str(exc)]
    return []


def claude_internal_review_payload(
    proposal: Mapping[str, Any],
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    task_digest: str,
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    endpoints = catalog_index(catalog)
    work_map = _work_map(proposal)
    work_items = [
        {
            "work_id": work_id,
            "objective": str(row.get("objective") or ""),
            "dependencies": [str(value) for value in row.get("dependencies", [])],
            "required_outputs": [
                str(value) for value in row.get("required_outputs", [])
            ],
        }
        for work_id, row in work_map.items()
    ]
    nodes: list[dict[str, Any]] = []
    for raw in proposal.get("nodes", []):
        if not isinstance(raw, Mapping):
            continue
        model = str(raw.get("model") or "unknown")
        provider = str(raw.get("provider") or "unknown")
        endpoint = endpoints.get((model, provider), {})
        work_ids = [str(value) for value in raw.get("work_ids", [])]
        maximum = int(raw.get("max_output_tokens") or 0)
        estimated = (
            _estimated_cost(
                endpoint,
                task_envelope,
                work_map,
                work_ids,
                maximum,
            )
            if endpoint and all(value in work_map for value in work_ids)
            else 0.0
        )
        nodes.append({
            "node_id": str(raw.get("node_id") or "unknown"),
            "candidate_id": f"{model}@{provider}",
            "work_ids": work_ids,
            "role": str(raw.get("role") or ""),
            "functions": [str(value) for value in raw.get("functions", [])],
            "model": model,
            "company": canonical_model_company(model),
            "provider": provider,
            "estimated_cost_usd": estimated,
            "contract_kind": "gpt-authored-expert-node",
            "recovery_candidate_ids": [
                f"{row.get('model')}@{row.get('provider')}"
                for row in raw.get("recovery", [])
                if isinstance(row, Mapping)
            ],
        })
    edges = [
        {
            "source": str(row.get("source") or "unknown"),
            "target": str(row.get("target") or "unknown"),
        }
        for row in proposal.get("edges", [])
        if isinstance(row, Mapping)
    ]
    return {
        "task_digest": task_digest,
        "proposal_digest": graph_sha256(proposal),
        "approved_total_calls": int(approved_total_calls),
        "governance_calls_reserved": int(governance_calls_reserved),
        "approved_recovery_calls": int(approved_recovery_calls),
        "cost_anomaly_usd": cost_anomaly_usd,
        "work_items": work_items,
        "nodes": nodes,
        "edges": edges,
    }


def graph_sha256(value: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(rendered.encode("utf-8")).hexdigest()
