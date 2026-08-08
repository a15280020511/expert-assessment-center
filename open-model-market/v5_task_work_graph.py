"""Current-ticket work graph construction for the production dynamic planner."""
from __future__ import annotations

import hashlib
import json
import math
import re
from statistics import median
from typing import Any, Mapping, Sequence

import networkx as nx

SCHEMA_VERSION = "current-ticket-work-graph-2"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    if isinstance(value, Mapping):
        return [{"key": key, "value": item} for key, item in value.items()]
    if value in (None, ""):
        return []
    return [value]


def _text(value: Any) -> str:
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return "" if value is None else str(value).strip()


def _slug(value: Any, fallback: str = "work") -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-")
    return (text[:48] or fallback).casefold()


def _tokens(value: Any) -> set[str]:
    text = _text(value).casefold()
    return set(re.findall(r"[a-z0-9_]{2,}", text) + re.findall(r"[\u4e00-\u9fff]", text))


def _jaccard(left: Any, right: Any) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _deliverables(task: Mapping[str, Any]) -> list[Any]:
    result: list[Any] = []
    for key in ("required_outputs", "outputs", "deliverables", "required_fields"):
        result.extend(_sequence(task.get(key)))
    return result


def _explicit_units(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    task = _mapping(packet.get("task"))
    graph = _mapping(task.get("work_graph"))
    raw = graph.get("work_units") or graph.get("nodes") or task.get("work_units") or task.get("subtasks")
    result: list[dict[str, Any]] = []
    for index, value in enumerate(_sequence(raw), 1):
        row = dict(value) if isinstance(value, Mapping) else {"payload": value}
        payload = row.get("payload") or row.get("objective") or row.get("task") or row.get("text") or value
        unit_id = str(row.get("unit_id") or row.get("id") or f"work-{index}").strip()
        dependencies = row.get("dependencies") or row.get("depends_on") or []
        result.append(
            {
                "unit_id": unit_id,
                "source_kind": str(row.get("kind") or row.get("type") or "task-unit").strip() or "task-unit",
                "payload": payload,
                "dependencies": [str(item) for item in _sequence(dependencies) if str(item).strip()],
                "dependency_source": "ticket-explicit",
            }
        )
    return result


def _structural_units(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    task = _mapping(packet.get("task"))
    question = task.get("question") or packet.get("question") or task.get("objective") or packet.get("objective") or ""
    sources: list[tuple[str, list[Any]]] = [
        ("objective", _sequence(question)),
        ("evidence", _sequence(packet.get("evidence"))),
        ("requirement", _sequence(task.get("requirements"))),
        ("acceptance", _sequence(packet.get("execution_acceptance"))),
        ("deliverable", _deliverables(task)),
    ]
    result: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    for source_kind, values in sources:
        for value in values:
            if not _text(value):
                continue
            counters[source_kind] = counters.get(source_kind, 0) + 1
            result.append(
                {
                    "unit_id": f"{source_kind}-{counters[source_kind]}",
                    "source_kind": source_kind,
                    "payload": value,
                    "dependencies": [],
                    "dependency_source": "structural-unit-no-inferred-hard-dependency",
                }
            )
    if not result:
        result.append(
            {
                "unit_id": "task-1",
                "source_kind": "task-unit",
                "payload": packet,
                "dependencies": [],
                "dependency_source": "current-ticket-single-unit",
            }
        )
    return result


def _normalize_explicit_dependencies(units: list[dict[str, Any]]) -> None:
    """Keep only ticket-declared dependencies; never promote similarity to order."""
    ids = {str(row["unit_id"]) for row in units}
    for row in units:
        explicit = [
            value
            for value in row.get("dependencies", [])
            if value in ids and value != row["unit_id"]
        ]
        row["dependencies"] = list(dict.fromkeys(explicit))


def _relatedness_edges(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose semantic relatedness as soft metadata, never as a DAG edge."""
    candidates: list[tuple[float, str, str]] = []
    for index, row in enumerate(units):
        for prior in units[:index]:
            score = _jaccard(row.get("payload"), prior.get("payload"))
            if score > 0:
                candidates.append((score, str(prior["unit_id"]), str(row["unit_id"])))
    if not candidates:
        return []
    threshold = median(score for score, _, _ in candidates)
    return [
        {
            "from": source,
            "to": target,
            "score": round(score, 8),
            "relation": "semantic-relatedness-only",
            "blocks_execution": False,
        }
        for score, source, target in sorted(candidates, key=lambda item: (-item[0], item[1], item[2]))
        if score >= threshold
    ]


def build_current_work_graph(packet: Mapping[str, Any]) -> dict[str, Any]:
    units = _explicit_units(packet) or _structural_units(packet)
    seen: set[str] = set()
    for index, row in enumerate(units, 1):
        unit_id = str(row.get("unit_id") or f"work-{index}")
        if unit_id in seen:
            unit_id = f"{unit_id}-{index}"
        row["unit_id"] = unit_id
        seen.add(unit_id)
    _normalize_explicit_dependencies(units)
    relatedness = _relatedness_edges(units)

    graph = nx.DiGraph()
    graph.add_nodes_from(str(row["unit_id"]) for row in units)
    for row in units:
        for parent in row.get("dependencies", []):
            if parent in graph and parent != row["unit_id"]:
                graph.add_edge(str(parent), str(row["unit_id"]))
    if not nx.is_directed_acyclic_graph(graph):
        raise RuntimeError("current-ticket work graph is cyclic")

    if len(graph) > 1:
        sinks = sorted(node for node in graph if graph.out_degree(node) == 0)
        if len(sinks) > 1:
            digest = hashlib.sha256("|".join(sinks).encode("utf-8")).hexdigest()[:12]
            integration_id = f"integration-{digest}"
            units.append(
                {
                    "unit_id": integration_id,
                    "source_kind": "integration",
                    "payload": "integrate current task sink results into the required delivery",
                    "dependencies": sinks,
                    "dependency_source": "current-ticket-multiple-sinks",
                }
            )
            graph.add_node(integration_id)
            graph.add_edges_from((sink, integration_id) for sink in sinks)

    generations = list(nx.topological_generations(graph))
    depth_by_id = {
        str(unit_id): depth
        for depth, generation in enumerate(generations, 1)
        for unit_id in generation
    }
    by_id = {str(row["unit_id"]): row for row in units}
    for unit_id, row in by_id.items():
        rendered = _text(row.get("payload"))
        row["character_count"] = len(rendered)
        row["structural_weight"] = max(1, math.ceil(math.sqrt(len(rendered) + 1)))
        row["depth"] = depth_by_id.get(unit_id, 1)

    counts: dict[str, int] = {}
    for row in units:
        kind = str(row.get("source_kind") or "task-unit")
        counts[kind] = counts.get(kind, 0) + 1
    edges = [{"from": str(a), "to": str(b)} for a, b in graph.edges()]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "current-ticket-content-derived-work-dag",
        "work_units": units,
        "dependency_edges": edges,
        "relatedness_edges": relatedness,
        "relatedness_edge_count": len(relatedness),
        "relatedness_is_dependency": False,
        "dependency_policy": "ticket-explicit-or-final-integration-only",
        "work_unit_count": len(units),
        "dependency_edge_count": len(edges),
        "maximum_depth": len(generations),
        "maximum_parallel_width": max((len(row) for row in generations), default=1),
        "maximum_fan_in": max((graph.in_degree(node) for node in graph), default=0),
        "counts_by_kind": counts,
        "entry_unit_ids": sorted(node for node in graph if graph.in_degree(node) == 0),
        "sink_unit_ids": sorted(node for node in graph if graph.out_degree(node) == 0),
        "semantic_keyword_routing_used": False,
        "domain_hardcoding_used": False,
        "cross_task_history_used": False,
        "finite_acyclic_validated_by_networkx": True,
    }


__all__ = ["SCHEMA_VERSION", "build_current_work_graph"]
