"""Deterministically materialize GPT-selected experts into an execution graph.

GPT chooses the composition. This module performs no ranking, scoring, pruning,
optimization, or repair; it only validates exact catalog/resource contracts.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from hashlib import sha256
from typing import Any, Mapping, Sequence

from execution_graph import ExecutionGraph, GraphLimits, SelectedEdge, SelectedNode
from execution_graph_validator import derive_execution_stages, validate_execution_graph
from v5_catalog_view import GOVERNANCE_COMPANIES, catalog_index
from v5_model_company import canonical_model_company

COST_RISK_MULTIPLIER = 1.18


class ProposalValidationError(RuntimeError):
    """Raised when a GPT proposal violates deterministic constraints."""


def compact_resources_for_gpt(resources: Mapping[str, Any]) -> dict[str, Any]:
    interpretations: list[dict[str, Any]] = []
    for raw in resources.get("interpretations", []):
        if not isinstance(raw, Mapping):
            continue
        works: list[dict[str, Any]] = []
        for work in raw.get("atomic_work", []):
            if not isinstance(work, Mapping):
                continue
            works.append({
                "work_id": work.get("work_id"),
                "objective": work.get("objective"),
                "importance": work.get("importance"),
                "error_cost": work.get("error_cost"),
                "verifiability": work.get("verifiability"),
                "domain_requirements": work.get("domain_requirements", {}),
                "operation_requirements": work.get("operation_requirements", {}),
                "reasoning_requirements": work.get("reasoning_requirements", {}),
                "context_requirements": work.get("context_requirements", {}),
                "output_contract": work.get("output_contract", {}),
                "independence_requirements": work.get(
                    "independence_requirements", {}
                ),
                "dependencies": list(work.get("dependencies", [])),
            })
        interpretations.append({
            "interpretation_id": raw.get("interpretation_id"),
            "strategy": raw.get("strategy"),
            "atomic_work": works,
        })
    return {
        "task_digest": resources.get("task_semantics", {}).get("task_digest"),
        "interpretations": interpretations,
        "selection_instruction": (
            "GPT chooses directly; local scoring, solver, Pareto pruning and "
            "heuristic ranking are forbidden."
        ),
    }


def _interpretation(
    resources: Mapping[str, Any], interpretation_id: str
) -> Mapping[str, Any]:
    rows = [
        row
        for row in resources.get("interpretations", [])
        if isinstance(row, Mapping)
        and str(row.get("interpretation_id") or "") == interpretation_id
    ]
    if len(rows) != 1:
        raise ProposalValidationError("proposal interpretation_id is unknown")
    return rows[0]


def _work_map(interpretation: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for work in interpretation.get("atomic_work", []):
        if not isinstance(work, Mapping):
            continue
        work_id = str(work.get("work_id") or "")
        if not work_id or work_id in result:
            raise ProposalValidationError("invalid or duplicate atomic work id")
        result[work_id] = work
    if not result:
        raise ProposalValidationError("selected interpretation has no work")
    return result


def _matrix(
    resources: Mapping[str, Any], interpretation_id: str
) -> Mapping[str, Any]:
    matrices = resources.get("resource_matrices", {}).get("matrices", [])
    rows = [
        row
        for row in matrices
        if isinstance(row, Mapping)
        and str(row.get("interpretation_id") or "") == interpretation_id
    ]
    return rows[0] if len(rows) == 1 else {}


def _capabilities(
    matrix: Mapping[str, Any], work_ids: Sequence[str]
) -> dict[str, float]:
    labels = [str(value) for value in matrix.get("capability_labels", [])]
    work_index = [
        row for row in matrix.get("work_index", []) if isinstance(row, Mapping)
    ]
    rows = matrix.get("task_resource_matrix", [])
    positions = {
        str(row.get("work_id") or ""): index
        for index, row in enumerate(work_index)
    }
    result: dict[str, float] = {}
    for work_id in work_ids:
        position = positions.get(work_id)
        if position is None or position >= len(rows):
            continue
        values = rows[position] if isinstance(rows[position], list) else []
        for index, label in enumerate(labels):
            if index >= len(values):
                continue
            try:
                value = float(values[index])
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0:
                result[label] = max(result.get(label, 0.0), value)
    return result or {"general_analysis": 1.0}


def _functions(
    work_map: Mapping[str, Mapping[str, Any]], work_ids: Sequence[str]
) -> tuple[str, ...]:
    values = {
        str(operation)
        for work_id in work_ids
        for operation, weight in dict(
            work_map[work_id].get("operation_requirements", {})
        ).items()
        if float(weight or 0.0) > 0
    }
    return tuple(sorted(values or {"analysis"}))


def _merge_contracts(
    work_map: Mapping[str, Mapping[str, Any]], work_ids: Sequence[str]
) -> dict[str, Any]:
    contracts = [
        dict(work_map[work_id].get("output_contract", {}))
        for work_id in work_ids
    ]
    if len(contracts) == 1:
        return contracts[0]
    required: list[str] = []
    for contract in contracts:
        for value in contract.get("required_fields", []):
            field = str(value)
            if field and field not in required:
                required.append(field)
    return {
        "required_fields": required,
        "machine_readable_required": any(
            bool(row.get("machine_readable_required")) for row in contracts
        ),
        "must_separate_fact_assumption_inference": any(
            bool(row.get("must_separate_fact_assumption_inference"))
            for row in contracts
        ),
        "combined_from_exact_work_contracts": True,
    }


def _estimated_cost(
    endpoint: Mapping[str, Any],
    work_map: Mapping[str, Mapping[str, Any]],
    work_ids: Sequence[str],
    max_output_tokens: int,
) -> float:
    prompt_tokens = 0
    for work_id in work_ids:
        context = work_map[work_id].get("context_requirements", {})
        prompt_tokens += sum(
            max(0, int(context.get(key, 0) or 0))
            for key in (
                "system_prompt_tokens",
                "original_task_tokens",
                "visible_upstream_tokens",
            )
        )
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
    matrix: Mapping[str, Any],
) -> SelectedNode:
    work_ids = tuple(str(value) for value in raw.get("work_ids", []))
    effort = str(raw.get("reasoning_effort") or "medium")
    max_output = int(raw.get("max_output_tokens") or 0)
    if not 256 <= max_output <= int(endpoint.get("max_completion_tokens") or 0):
        raise ProposalValidationError("node output allowance exceeds endpoint")
    functions = _functions(work_map, work_ids)
    return SelectedNode(
        node_id=str(raw.get("node_id") or ""),
        assigned_work=work_ids,
        professional_capabilities=_capabilities(matrix, work_ids),
        functions=functions,
        prompt_profile={
            "modules": list(functions),
            "role": str(raw.get("role") or ""),
            "source": "gpt-direct-proposal",
        },
        reasoning_profile={
            "reasoning_enabled": "reasoning"
            in {
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
        output_contract=_merge_contracts(work_map, work_ids),
        estimated_quality=0.5,
        quality_uncertainty=0.5,
        estimated_cost=_estimated_cost(
            endpoint, work_map, work_ids, max_output
        ),
        failure_probability=0.0,
        request_config=_request_config(endpoint, effort, max_output),
        independence_group=None,
    )


def _recovery_row(
    raw: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    selected: SelectedNode,
    work_map: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    max_output = int(
        selected.parameter_profile.get(
            "recommended_output_allowance_tokens", 2048
        )
    )
    if max_output > int(endpoint.get("max_completion_tokens") or 0):
        raise ProposalValidationError("recovery output allowance exceeds endpoint")
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
        "estimated_quality": selected.estimated_quality,
        "quality_uncertainty": selected.quality_uncertainty,
        "estimated_cost": _estimated_cost(
            endpoint, work_map, selected.assigned_work, max_output
        ),
        "failure_probability": 0.0,
        "request_config": _request_config(
            endpoint,
            str(selected.reasoning_profile.get("effort") or "medium"),
            max_output,
        ),
    }


def _dependency_violations(
    graph: ExecutionGraph, work_map: Mapping[str, Mapping[str, Any]]
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
    resources: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> tuple[ExecutionGraph, GraphLimits, dict[str, Any]]:
    interpretation_id = str(proposal.get("interpretation_id") or "")
    interpretation = _interpretation(resources, interpretation_id)
    work_map = _work_map(interpretation)
    matrix = _matrix(resources, interpretation_id)
    endpoints = catalog_index(catalog)
    raw_nodes = proposal.get("nodes")
    raw_edges = proposal.get("edges")
    raw_final = proposal.get("final_nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ProposalValidationError("proposal nodes are missing")
    if not isinstance(raw_edges, list) or not isinstance(raw_final, list):
        raise ProposalValidationError("proposal edges/final_nodes are invalid")

    maximum_initial = (
        int(approved_total_calls)
        - int(governance_calls_reserved)
        - int(approved_recovery_calls)
    )
    if maximum_initial < 1 or len(raw_nodes) > maximum_initial:
        raise ProposalValidationError("proposal exceeds expert initial-call capacity")

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
        node = _selected_node(raw, endpoint, work_map, matrix)
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
                    recovery, recovery_endpoint, node, work_map
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
    node_ids = {node.node_id for node in selected}
    final_nodes = tuple(str(value) for value in raw_final)
    if not final_nodes or not set(final_nodes).issubset(node_ids):
        raise ProposalValidationError("final_nodes reference unknown nodes")

    provisional = ExecutionGraph(
        nodes=tuple(selected),
        edges=edges,
        execution_stages=(tuple(sorted(node_ids)),),
        entry_nodes=(),
        final_nodes=final_nodes,
        required_work=tuple(work_map),
        estimated_quality=0.5,
        quality_floor=0.0,
        estimated_total_cost=round(
            sum(node.estimated_cost for node in selected), 8
        ),
        metadata={
            "interpretation_id": interpretation_id,
            "recovery_pool": recovery_pool,
            "selection_authority": "gpt-direct",
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
        **{
            **provisional.to_dict(),
            "execution_stages": [list(stage) for stage in stages],
            "entry_nodes": sorted(node_ids - incoming),
        }
    )
    limits = GraphLimits(
        max_nodes=maximum_initial,
        max_edges=64,
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
        "schema_version": "v5-gpt-proposal-materialization-1",
        "status": "PASS",
        "interpretation_id": interpretation_id,
        "selected_node_count": len(selected),
        "selected_companies": selected_companies,
        "recovery_companies": recovery_companies,
        "maximum_expert_initial_calls": maximum_initial,
        "risk_adjusted_reserved_cost_usd": round(total_risk_cost, 8),
        "local_scoring_used": False,
        "optimizer_used": False,
        "cp_sat_used": False,
        "pareto_pruning_used": False,
        "heuristic_ranking_used": False,
        "proposal_repaired_by_validator": False,
    }
    return graph, limits, audit


def deterministic_violations(
    proposal: Mapping[str, Any],
    resources: Mapping[str, Any],
    catalog: Mapping[str, Any],
    **limits: Any,
) -> list[str]:
    try:
        materialize_proposal(proposal, resources, catalog, **limits)
    except Exception as exc:  # noqa: BLE001
        return [str(exc)]
    return []


def claude_internal_review_payload(
    proposal: Mapping[str, Any],
    resources: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    task_digest: str,
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    endpoints = catalog_index(catalog)
    interpretation_id = str(proposal.get("interpretation_id") or "")
    try:
        work_map = _work_map(_interpretation(resources, interpretation_id))
    except ProposalValidationError:
        work_map = {}
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
            _estimated_cost(endpoint, work_map, work_ids, maximum)
            if endpoint and all(value in work_map for value in work_ids)
            else 0.0
        )
        nodes.append({
            "node_id": str(raw.get("node_id") or "unknown"),
            "candidate_id": f"{model}@{provider}",
            "work_ids": work_ids,
            "model": model,
            "company": canonical_model_company(model),
            "provider": provider,
            "estimated_cost_usd": estimated,
            "contract_kind": "gpt-proposed-expert-node",
            "recovery_candidate_ids": [
                f"{row.get('model')}@{row.get('provider')}"
                for row in raw.get("recovery", [])
                if isinstance(row, Mapping)
            ],
        })
    required_work = sorted(work_map) or ["unknown-work"]
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
        "required_work": required_work,
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
