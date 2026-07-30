"""Build and validate V5 atomic-work dependency graphs."""
from __future__ import annotations

from typing import Any, Mapping

import networkx as nx


class AtomicWorkGraphError(ValueError):
    pass


def _work_rows(interpretation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = interpretation.get("atomic_work", [])
    if not isinstance(rows, list) or not rows:
        raise AtomicWorkGraphError("Interpretation must contain at least one atomic work item.")
    return rows


def build_atomic_work_graph(interpretation: Mapping[str, Any]) -> dict[str, Any]:
    rows = _work_rows(interpretation)
    graph = nx.DiGraph()
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        work_id = str(row.get("work_id") or "")
        if not work_id:
            raise AtomicWorkGraphError("Every atomic work item requires a non-empty work_id.")
        if work_id in by_id:
            raise AtomicWorkGraphError(f"Duplicate atomic work id: {work_id}")
        by_id[work_id] = row
        graph.add_node(work_id)

    edges: list[dict[str, str]] = []
    for work_id, row in by_id.items():
        dependencies = row.get("dependencies", [])
        if not isinstance(dependencies, (list, tuple)):
            raise AtomicWorkGraphError(f"dependencies for {work_id} must be a list or tuple.")
        for dependency in dependencies:
            source = str(dependency)
            if source not in by_id:
                raise AtomicWorkGraphError(f"Unknown dependency {source!r} referenced by {work_id!r}.")
            if source == work_id:
                raise AtomicWorkGraphError(f"Atomic work {work_id!r} cannot depend on itself.")
            graph.add_edge(source, work_id)
            edges.append({"source": source, "target": work_id, "relation_type": "dependency"})

    if not nx.is_directed_acyclic_graph(graph):
        cycle = nx.find_cycle(graph, orientation="original")
        raise AtomicWorkGraphError(f"Atomic work graph contains a cycle: {cycle}")

    stages = [tuple(sorted(generation)) for generation in nx.topological_generations(graph)]
    roots = tuple(sorted(node for node in graph.nodes if graph.in_degree(node) == 0))
    leaves = tuple(sorted(node for node in graph.nodes if graph.out_degree(node) == 0))
    critical_path = tuple(nx.dag_longest_path(graph)) if graph.nodes else ()
    edge_keys = {(row["source"], row["target"], row["relation_type"]) for row in edges}
    if len(edge_keys) != len(edges):
        raise AtomicWorkGraphError("Atomic work graph contains duplicate dependency edges.")

    return {
        "version": 5,
        "interpretation_id": str(interpretation.get("interpretation_id") or ""),
        "strategy": str(interpretation.get("strategy") or ""),
        "nodes": [dict(row) for row in rows],
        "edges": sorted(edges, key=lambda row: (row["source"], row["target"])),
        "execution_stages": [list(stage) for stage in stages],
        "root_work": list(roots),
        "leaf_work": list(leaves),
        "critical_path": list(critical_path),
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "is_dag": True,
    }


def compile_atomic_work_graphs(compilation: Mapping[str, Any]) -> dict[str, Any]:
    interpretations = compilation.get("interpretations", [])
    if not isinstance(interpretations, list) or not interpretations:
        raise AtomicWorkGraphError("Semantic compilation contains no task interpretations.")
    graphs = [build_atomic_work_graph(row) for row in interpretations]
    return {
        "version": 5,
        "task_digest": compilation.get("task_digest"),
        "graphs": graphs,
        "all_graphs_are_dag": all(bool(row["is_dag"]) for row in graphs),
        "interpretation_count": len(graphs),
    }
