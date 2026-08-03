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
from v5_claude_red_team_policy import CLAUDE_RED_TEAM_MAX_TASK_CHARS
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


def _proposal_collections(
    proposal: Mapping[str, Any],
    *,
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
) -> tuple[list[Any], list[Any], tuple[str, ...], set[str], int]:
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
    return raw_nodes, raw_edges, final_nodes, raw_node_ids, maximum_initial


def _exact_endpoint(
    endpoints: Mapping[tuple[str, str], Mapping[str, Any]],
    raw: Mapping[str, Any],
    *,
    recovery: bool = False,
) -> tuple[tuple[str, str], Mapping[str, Any], str]:
    key = (str(raw.get("model") or ""), str(raw.get("provider") or ""))
    endpoint = endpoints.get(key)
    label = "recovery endpoint" if recovery else "endpoint"
    if endpoint is None:
        raise ProposalValidationError(f"unknown exact {label}: {key}")
    company = canonical_model_company(key[0])
    if company in GOVERNANCE_COMPANIES:
        role = "recovery expert" if recovery else "expert"
        raise ProposalValidationError(f"governance company cannot be a {role}")
    return key, endpoint, company


def _materialize_recoveries(
    raw: Mapping[str, Any],
    selected: SelectedNode,
    endpoints: Mapping[tuple[str, str], Mapping[str, Any]],
    task_envelope: Mapping[str, Any],
    work_map: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    companies: list[str] = []
    for recovery in raw.get("recovery", []):
        if not isinstance(recovery, Mapping):
            raise ProposalValidationError("recovery row must be an object")
        _, endpoint, company = _exact_endpoint(endpoints, recovery, recovery=True)
        companies.append(company)
        rows.append(
            _recovery_row(
                recovery,
                endpoint,
                selected,
                task_envelope,
                work_map,
            )
        )
    return rows, companies


def _materialize_nodes(
    raw_nodes: Sequence[Any],
    final_nodes: tuple[str, ...],
    endpoints: Mapping[tuple[str, str], Mapping[str, Any]],
    work_map: Mapping[str, Mapping[str, Any]],
    task: str,
    task_envelope: Mapping[str, Any],
) -> tuple[list[SelectedNode], list[str], dict[str, list[dict[str, Any]]], list[str], list[str]]:
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
        _, endpoint, company = _exact_endpoint(endpoints, raw)
        node = _selected_node(
            raw,
            endpoint,
            work_map,
            task,
            task_envelope,
            final_node=str(raw.get("node_id") or "") in final_nodes,
        )
        recovery_rows, companies = _materialize_recoveries(
            raw,
            node,
            endpoints,
            task_envelope,
            work_map,
        )
        selected.append(node)
        selected_companies.append(company)
        covered.extend(work_ids)
        recovery_companies.extend(companies)
        recovery_pool[node.node_id] = recovery_rows
    return selected, selected_companies, recovery_pool, recovery_companies, covered


def _validate_materialized_assignment(
    covered: Sequence[str],
    work_map: Mapping[str, Mapping[str, Any]],
    selected_companies: Sequence[str],
    recovery_companies: Sequence[str],
    approved_recovery_calls: int,
) -> None:
    if any(count != 1 for count in Counter(covered).values()):
        raise ProposalValidationError("each required work must be assigned once")
    if set(covered) != set(work_map):
        raise ProposalValidationError("proposal does not cover exact required work")
    all_companies = list(selected_companies) + list(recovery_companies)
    if len(all_companies) != len(set(all_companies)):
        raise ProposalValidationError("expert and recovery companies must be globally unique")
    if len(recovery_companies) > int(approved_recovery_calls):
        raise ProposalValidationError("recovery proposal exceeds approved reserve")


def _selected_edges(raw_edges: Sequence[Any]) -> tuple[SelectedEdge, ...]:
    return tuple(
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


def _graph_metadata(
    proposal: Mapping[str, Any],
    recovery_pool: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    return {
        "work_items": [dict(row) for row in proposal.get("work_items", [])],
        "recovery_pool": dict(recovery_pool),
        "selection_authority": "gpt-direct",
        "local_task_classification_used": False,
        "local_atomic_work_generation_used": False,
        "local_resource_matrix_used": False,
        "local_scoring_used": False,
        "optimizer_used": False,
        "cp_sat_used": False,
        "pareto_pruning_used": False,
        "heuristic_ranking_used": False,
    }


def _materialized_graph(
    proposal: Mapping[str, Any],
    selected: Sequence[SelectedNode],
    edges: tuple[SelectedEdge, ...],
    raw_node_ids: set[str],
    final_nodes: tuple[str, ...],
    work_map: Mapping[str, Mapping[str, Any]],
    recovery_pool: Mapping[str, list[dict[str, Any]]],
) -> ExecutionGraph:
    cost = round(sum(node.estimated_cost for node in selected), 8)
    provisional = ExecutionGraph(
        nodes=tuple(selected),
        edges=edges,
        execution_stages=(tuple(sorted(raw_node_ids)),),
        entry_nodes=(),
        final_nodes=final_nodes,
        required_work=tuple(work_map),
        estimated_quality=0.0,
        quality_floor=0.0,
        estimated_total_cost=cost,
        metadata=_graph_metadata(proposal, recovery_pool),
    )
    incoming = {edge.target for edge in edges}
    return ExecutionGraph(
        nodes=tuple(selected),
        edges=edges,
        execution_stages=derive_execution_stages(provisional),
        entry_nodes=tuple(sorted(raw_node_ids - incoming)),
        final_nodes=final_nodes,
        required_work=tuple(work_map),
        estimated_quality=0.0,
        quality_floor=0.0,
        estimated_total_cost=cost,
        metadata=dict(provisional.metadata),
    )


def _proposal_limits(
    maximum_initial: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> GraphLimits:
    return GraphLimits(
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


def _validate_materialized_graph(
    graph: ExecutionGraph,
    limits: GraphLimits,
    work_map: Mapping[str, Mapping[str, Any]],
) -> None:
    issues = list(validate_execution_graph(graph, limits))
    dependency_issues = _dependency_violations(graph, work_map)
    if issues or dependency_issues:
        messages = [f"{issue.code}:{issue.message}" for issue in issues]
        messages.extend(dependency_issues)
        raise ProposalValidationError("; ".join(messages))


def _risk_adjusted_cost(
    graph: ExecutionGraph,
    recovery_pool: Mapping[str, list[dict[str, Any]]],
    cost_anomaly_usd: float | None,
) -> float:
    total = graph.estimated_total_cost * COST_RISK_MULTIPLIER
    total += sum(
        float(row.get("estimated_cost", 0.0)) * COST_RISK_MULTIPLIER
        for rows in recovery_pool.values()
        for row in rows
    )
    if cost_anomaly_usd is not None and total > float(cost_anomaly_usd) + 1e-12:
        raise ProposalValidationError("proposal exceeds risk-adjusted cost guard")
    return total


def _materialization_audit(
    work_map: Mapping[str, Mapping[str, Any]],
    selected: Sequence[SelectedNode],
    selected_companies: Sequence[str],
    recovery_companies: Sequence[str],
    maximum_initial: int,
    total_risk_cost: float,
) -> dict[str, Any]:
    return {
        "schema_version": "v5-gpt-proposal-materialization-2",
        "status": "PASS",
        "work_item_count": len(work_map),
        "selected_node_count": len(selected),
        "selected_companies": list(selected_companies),
        "recovery_companies": list(recovery_companies),
        "maximum_expert_initial_calls": maximum_initial,
        "risk_adjusted_reserved_cost_usd": round(total_risk_cost, 8),
        "local_task_classification_used": False,
        "local_atomic_work_generation_used": False,
        "local_resource_matrix_used": False,
        "local_scoring_used": False,
        "optimizer_used": False,
        "proposal_repaired_by_validator": False,
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
    work_map = _work_map(proposal)
    endpoints = catalog_index(catalog)
    raw_nodes, raw_edges, final_nodes, node_ids, maximum_initial = _proposal_collections(
        proposal,
        approved_total_calls=approved_total_calls,
        governance_calls_reserved=governance_calls_reserved,
        approved_recovery_calls=approved_recovery_calls,
    )
    selected, selected_companies, recovery_pool, recovery_companies, covered = _materialize_nodes(
        raw_nodes,
        final_nodes,
        endpoints,
        work_map,
        task,
        task_envelope,
    )
    _validate_materialized_assignment(
        covered,
        work_map,
        selected_companies,
        recovery_companies,
        approved_recovery_calls,
    )
    graph = _materialized_graph(
        proposal,
        selected,
        _selected_edges(raw_edges),
        node_ids,
        final_nodes,
        work_map,
        recovery_pool,
    )
    limits = _proposal_limits(
        maximum_initial,
        approved_recovery_calls,
        cost_anomaly_usd,
    )
    _validate_materialized_graph(graph, limits, work_map)
    risk_cost = _risk_adjusted_cost(graph, recovery_pool, cost_anomaly_usd)
    return graph, limits, _materialization_audit(
        work_map,
        selected,
        selected_companies,
        recovery_companies,
        maximum_initial,
        risk_cost,
    )


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


def _bounded_task_excerpt(task: str) -> tuple[str, bool]:
    text = str(task or "")
    maximum = CLAUDE_RED_TEAM_MAX_TASK_CHARS
    if len(text) <= maximum:
        return text, False
    marker = "\n[...task excerpt truncated...]\n"
    retained = maximum - len(marker)
    if retained <= 1:
        raise ProposalValidationError("Claude task excerpt limit is invalid")
    head = retained // 2
    tail = retained - head
    excerpt = text[:head] + marker + text[-tail:]
    if len(excerpt) != maximum:
        raise ProposalValidationError("Claude task excerpt bound is inconsistent")
    return excerpt, True


def _claude_work_items(
    work_map: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "work_id": work_id,
            "objective": str(row.get("objective") or ""),
            "dependencies": [str(value) for value in row.get("dependencies", [])],
            "required_outputs": [str(value) for value in row.get("required_outputs", [])],
        }
        for work_id, row in work_map.items()
    ]


def _claude_recovery_rows(
    raw: Mapping[str, Any],
    endpoints: Mapping[tuple[str, str], Mapping[str, Any]],
    task_envelope: Mapping[str, Any],
    work_map: Mapping[str, Mapping[str, Any]],
    work_ids: list[str],
    maximum: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for recovery in raw.get("recovery", []):
        if not isinstance(recovery, Mapping):
            continue
        model = str(recovery.get("model") or "unknown")
        provider = str(recovery.get("provider") or "unknown")
        endpoint = endpoints.get((model, provider), {})
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
        rows.append(
            {
                "candidate_id": f"{model}@{provider}",
                "model": model,
                "company": canonical_model_company(model),
                "provider": provider,
                "estimated_cost_usd": estimated,
            }
        )
    return rows


def _claude_node_row(
    raw: Mapping[str, Any],
    endpoints: Mapping[tuple[str, str], Mapping[str, Any]],
    task_envelope: Mapping[str, Any],
    work_map: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    model = str(raw.get("model") or "unknown")
    provider = str(raw.get("provider") or "unknown")
    endpoint = endpoints.get((model, provider), {})
    work_ids = [str(value) for value in raw.get("work_ids", [])]
    maximum = int(raw.get("max_output_tokens") or 0)
    estimated = (
        _estimated_cost(endpoint, task_envelope, work_map, work_ids, maximum)
        if endpoint and all(value in work_map for value in work_ids)
        else 0.0
    )
    return {
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
        "recovery_candidates": _claude_recovery_rows(
            raw,
            endpoints,
            task_envelope,
            work_map,
            work_ids,
            maximum,
        ),
    }


def _claude_nodes(
    proposal: Mapping[str, Any],
    endpoints: Mapping[tuple[str, str], Mapping[str, Any]],
    task_envelope: Mapping[str, Any],
    work_map: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        _claude_node_row(raw, endpoints, task_envelope, work_map)
        for raw in proposal.get("nodes", [])
        if isinstance(raw, Mapping)
    ]


def _claude_edges(proposal: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "source": str(row.get("source") or "unknown"),
            "target": str(row.get("target") or "unknown"),
            "relation_type": str(row.get("relation_type") or "unknown"),
        }
        for row in proposal.get("edges", [])
        if isinstance(row, Mapping)
    ]


def claude_unified_review_payload(
    proposal: Mapping[str, Any],
    task: str,
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    task_digest: str,
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    """Build one bounded Claude input for selection and information review."""
    endpoints = catalog_index(catalog)
    work_map = _work_map(proposal)
    excerpt, truncated = _bounded_task_excerpt(task)
    return {
        "task_digest": task_digest,
        "proposal_digest": graph_sha256(proposal),
        "approved_total_calls": int(approved_total_calls),
        "governance_calls_reserved": int(governance_calls_reserved),
        "approved_recovery_calls": int(approved_recovery_calls),
        "cost_anomaly_usd": cost_anomaly_usd,
        "task_excerpt": excerpt,
        "task_characters": len(str(task or "")),
        "task_truncated": truncated,
        "task_constraints": dict(task_envelope.get("task_constraints") or {}),
        "explicit_delivery_contract": dict(
            task_envelope.get("explicit_delivery_contract") or {}
        ),
        "work_items": _claude_work_items(work_map),
        "nodes": _claude_nodes(proposal, endpoints, task_envelope, work_map),
        "edges": _claude_edges(proposal),
        "final_nodes": [
            str(value) for value in proposal.get("final_nodes", [])
        ],
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
