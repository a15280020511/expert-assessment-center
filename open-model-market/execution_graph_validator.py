"""Deterministic safety and structural validation for V5 execution graphs."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

import networkx as nx

from execution_graph import ExecutionGraph, GraphLimits, ValidationIssue
from v5_model_company import candidate_company

_FORBIDDEN_REQUEST_KEYS = {
    "tools",
    "tool_choice",
    "plugins",
    "web_search",
    "web_search_options",
    "file_search",
    "browser",
    "code_interpreter",
    "models",
}
_FORBIDDEN_MODEL_TERMS = ("openrouter/auto", ":online", ":batch")
_ALLOWED_RELATIONS = {
    "dependency",
    "review",
    "adversarial",
    "supplement",
    "correction",
    "comparison",
    "synthesis",
    "adjudication",
    "formatting",
}


class ExecutionGraphValidationError(ValueError):
    """Raised when a V5 graph violates one or more fixed invariants."""

    def __init__(self, issues: Iterable[ValidationIssue]):
        self.issues = tuple(issues)
        joined = "; ".join(
            f"{row.code}: {row.message}" for row in self.issues
        )
        super().__init__(joined or "Execution graph validation failed")


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _walk_keys(
    value: Any,
    path: str = "request_config",
) -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            yield key_text.casefold(), child_path
            yield from _walk_keys(child, child_path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{path}[{index}]")


def _stage_index(
    graph: ExecutionGraph,
) -> tuple[dict[str, int], list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    index: dict[str, int] = {}
    for stage_number, stage in enumerate(graph.execution_stages):
        for node_id in stage:
            if node_id in index:
                issues.append(
                    ValidationIssue(
                        "duplicate_stage_node",
                        f"Node {node_id!r} appears in more than one execution stage.",
                        "execution_stages",
                    )
                )
            index[node_id] = stage_number
    return index, issues


def derive_execution_stages(
    graph: ExecutionGraph,
) -> tuple[tuple[str, ...], ...]:
    """Return deterministic topological generations for a valid DAG."""
    dag = nx.DiGraph()
    dag.add_nodes_from(node.node_id for node in graph.nodes)
    dag.add_edges_from((edge.source, edge.target) for edge in graph.edges)
    if not nx.is_directed_acyclic_graph(dag):
        raise ExecutionGraphValidationError(
            [
                ValidationIssue(
                    "cycle",
                    "Execution graph is not a DAG.",
                    "edges",
                )
            ]
        )
    return tuple(
        tuple(sorted(generation))
        for generation in nx.topological_generations(dag)
    )


def validate_execution_graph(
    graph: ExecutionGraph,
    limits: GraphLimits | None = None,
    *,
    raise_on_error: bool = False,
) -> tuple[ValidationIssue, ...]:
    """Validate fixed V5 safety rules without changing or repairing the graph."""
    limits = limits or GraphLimits()
    issues: list[ValidationIssue] = []
    node_ids = [node.node_id for node in graph.nodes]
    node_set = set(node_ids)

    if not graph.nodes:
        issues.append(
            ValidationIssue(
                "empty_graph",
                "At least one node is required.",
                "nodes",
            )
        )
    for node_id, count in Counter(node_ids).items():
        if not node_id:
            issues.append(
                ValidationIssue(
                    "empty_node_id",
                    "Node IDs must be non-empty.",
                    "nodes",
                )
            )
        elif count > 1:
            issues.append(
                ValidationIssue(
                    "duplicate_node_id",
                    f"Duplicate node ID {node_id!r}.",
                    "nodes",
                )
            )

    if len(graph.nodes) > limits.max_nodes:
        issues.append(
            ValidationIssue(
                "node_limit",
                f"Node count exceeds {limits.max_nodes}.",
                "nodes",
            )
        )
    if len(graph.edges) > limits.max_edges:
        issues.append(
            ValidationIssue(
                "edge_limit",
                f"Edge count exceeds {limits.max_edges}.",
                "edges",
            )
        )
    if len(graph.nodes) > limits.max_model_calls:
        issues.append(
            ValidationIssue(
                "call_limit",
                f"Planned model calls exceed {limits.max_model_calls}.",
                "nodes",
            )
        )
    if len(graph.execution_stages) > limits.max_stages:
        issues.append(
            ValidationIssue(
                "stage_limit",
                f"Execution stages exceed {limits.max_stages}.",
                "execution_stages",
            )
        )

    if not all(
        _finite(x)
        for x in (
            graph.estimated_quality,
            graph.quality_floor,
            graph.estimated_total_cost,
        )
    ):
        issues.append(
            ValidationIssue(
                "non_finite_graph_score",
                "Graph scores and cost must be finite.",
            )
        )
    if graph.estimated_quality < 0 or graph.quality_floor < 0:
        issues.append(
            ValidationIssue(
                "negative_quality",
                "Quality values cannot be negative.",
            )
        )
    if graph.quality_floor > graph.estimated_quality + 1e-12:
        issues.append(
            ValidationIssue(
                "quality_floor",
                "Quality floor exceeds estimated quality.",
            )
        )
    if graph.estimated_total_cost < 0:
        issues.append(
            ValidationIssue(
                "negative_cost",
                "Estimated total cost cannot be negative.",
            )
        )
    if (
        limits.max_budget_usd is not None
        and graph.estimated_total_cost > limits.max_budget_usd + 1e-12
    ):
        issues.append(
            ValidationIssue(
                "budget_limit",
                (
                    "Estimated cost exceeds hard budget "
                    f"{limits.max_budget_usd:.6f} USD."
                ),
                "estimated_total_cost",
            )
        )

    covered_work: set[str] = set()
    independence_groups: dict[str, list[Any]] = defaultdict(list)
    companies_by_name: dict[str, list[str]] = defaultdict(list)
    for index, node in enumerate(graph.nodes):
        path = f"nodes[{index}]"
        covered_work.update(node.assigned_work)
        companies_by_name[candidate_company(node)].append(node.node_id)
        if not node.assigned_work:
            issues.append(
                ValidationIssue(
                    "unassigned_node",
                    "Every node must own work.",
                    path,
                )
            )
        if not node.functions:
            issues.append(
                ValidationIssue(
                    "missing_function",
                    "Every node needs at least one function.",
                    path,
                )
            )
        if not node.model or not node.provider_endpoint:
            issues.append(
                ValidationIssue(
                    "missing_endpoint",
                    "Model and provider endpoint are required.",
                    path,
                )
            )
        model_folded = node.model.casefold()
        if any(term in model_folded for term in _FORBIDDEN_MODEL_TERMS):
            issues.append(
                ValidationIssue(
                    "router_model",
                    f"Forbidden routed model {node.model!r}.",
                    f"{path}.model",
                )
            )
        for key, key_path in _walk_keys(
            node.request_config,
            f"{path}.request_config",
        ):
            if key in _FORBIDDEN_REQUEST_KEYS:
                issues.append(
                    ValidationIssue(
                        "tool_field",
                        f"Forbidden request field {key!r}.",
                        key_path,
                    )
                )
        for name, value in (
            ("estimated_quality", node.estimated_quality),
            ("quality_uncertainty", node.quality_uncertainty),
            ("estimated_cost", node.estimated_cost),
            ("failure_probability", node.failure_probability),
        ):
            if not _finite(value):
                issues.append(
                    ValidationIssue(
                        "non_finite_node_score",
                        f"{name} must be finite.",
                        f"{path}.{name}",
                    )
                )
        if node.estimated_quality < 0 or node.estimated_quality > 1:
            issues.append(
                ValidationIssue(
                    "node_quality_range",
                    "Estimated quality must be in [0, 1].",
                    path,
                )
            )
        if node.quality_uncertainty < 0 or node.quality_uncertainty > 1:
            issues.append(
                ValidationIssue(
                    "uncertainty_range",
                    "Quality uncertainty must be in [0, 1].",
                    path,
                )
            )
        if node.failure_probability < 0 or node.failure_probability > 1:
            issues.append(
                ValidationIssue(
                    "failure_range",
                    "Failure probability must be in [0, 1].",
                    path,
                )
            )
        if node.estimated_cost < 0:
            issues.append(
                ValidationIssue(
                    "node_negative_cost",
                    "Node cost cannot be negative.",
                    path,
                )
            )
        if node.independence_group:
            independence_groups[node.independence_group].append(node)

    for company, company_node_ids in sorted(companies_by_name.items()):
        if len(company_node_ids) > 1:
            issues.append(
                ValidationIssue(
                    "model_company_reuse",
                    (
                        f"Model company {company!r} is reused by nodes "
                        f"{sorted(company_node_ids)}."
                    ),
                    "nodes",
                )
            )

    missing_work = sorted(set(graph.required_work) - covered_work)
    if missing_work:
        issues.append(
            ValidationIssue(
                "work_coverage",
                f"Uncovered required work: {missing_work}.",
                "required_work",
            )
        )

    dag = nx.DiGraph()
    dag.add_nodes_from(node_set)
    edge_pairs: set[tuple[str, str, str, str]] = set()
    for index, edge in enumerate(graph.edges):
        path = f"edges[{index}]"
        if edge.source not in node_set or edge.target not in node_set:
            issues.append(
                ValidationIssue(
                    "unknown_edge_node",
                    "Edge references an unknown node.",
                    path,
                )
            )
            continue
        if edge.source == edge.target:
            issues.append(
                ValidationIssue(
                    "self_edge",
                    "Self edges are forbidden.",
                    path,
                )
            )
        if edge.relation_type not in _ALLOWED_RELATIONS:
            issues.append(
                ValidationIssue(
                    "relation_type",
                    f"Unsupported relation {edge.relation_type!r}.",
                    path,
                )
            )
        identity = (
            edge.source,
            edge.target,
            edge.relation_type,
            edge.payload_type,
        )
        if identity in edge_pairs:
            issues.append(
                ValidationIssue(
                    "duplicate_edge",
                    f"Duplicate edge {identity!r}.",
                    path,
                )
            )
        edge_pairs.add(identity)
        dag.add_edge(edge.source, edge.target)

    if not nx.is_directed_acyclic_graph(dag):
        issues.append(
            ValidationIssue(
                "cycle",
                "Execution graph must be a directed acyclic graph.",
                "edges",
            )
        )

    stage_index, stage_issues = _stage_index(graph)
    issues.extend(stage_issues)
    if set(stage_index) != node_set:
        missing = sorted(node_set - set(stage_index))
        unknown = sorted(set(stage_index) - node_set)
        issues.append(
            ValidationIssue(
                "stage_coverage",
                (
                    "Stage membership mismatch; "
                    f"missing={missing}, unknown={unknown}."
                ),
                "execution_stages",
            )
        )
    for edge in graph.edges:
        if edge.source in stage_index and edge.target in stage_index:
            if stage_index[edge.target] <= stage_index[edge.source]:
                issues.append(
                    ValidationIssue(
                        "stage_order",
                        (
                            f"Edge {edge.source}->{edge.target} does not "
                            "advance execution stage."
                        ),
                        "execution_stages",
                    )
                )

    expected_entries = {
        node for node in node_set if dag.in_degree(node) == 0
    }
    expected_finals = {
        node for node in node_set if dag.out_degree(node) == 0
    }
    if set(graph.entry_nodes) != expected_entries:
        issues.append(
            ValidationIssue(
                "entry_nodes",
                "Entry nodes do not match graph indegree.",
                "entry_nodes",
            )
        )
    if set(graph.final_nodes) != expected_finals:
        issues.append(
            ValidationIssue(
                "final_nodes",
                "Final nodes do not match graph outdegree.",
                "final_nodes",
            )
        )

    for group_name, members in independence_groups.items():
        if len(members) < 2:
            continue
        ids = {node.node_id for node in members}
        models = [node.model for node in members]
        if len(models) != len(set(models)):
            issues.append(
                ValidationIssue(
                    "independent_same_model",
                    f"Independence group {group_name!r} reuses a model.",
                    "nodes",
                )
            )
        for edge in graph.edges:
            if edge.source in ids and edge.target in ids:
                issues.append(
                    ValidationIssue(
                        "independent_visibility",
                        (
                            f"Independent nodes {edge.source!r} and "
                            f"{edge.target!r} cannot exchange results."
                        ),
                        "edges",
                    )
                )

    node_cost = sum(node.estimated_cost for node in graph.nodes)
    if abs(node_cost - graph.estimated_total_cost) > max(
        1e-9,
        node_cost * 1e-6,
    ):
        issues.append(
            ValidationIssue(
                "cost_reconciliation",
                (
                    f"Graph cost {graph.estimated_total_cost:.12f} does not "
                    f"reconcile with node cost {node_cost:.12f}."
                ),
                "estimated_total_cost",
            )
        )

    result = tuple(issues)
    if raise_on_error and result:
        raise ExecutionGraphValidationError(result)
    return result
