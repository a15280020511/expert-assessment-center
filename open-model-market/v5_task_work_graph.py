"""Current-ticket work graph construction for the production dynamic planner.

The work DAG represents *analysis work*, not every piece of ticket metadata.
Requirements, acceptance clauses and delivery fields are execution constraints;
they must not each create a paid expert node. Evidence rows are structurally
clustered into current-ticket analysis branches before team-size optimization.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from statistics import median
from typing import Any, Mapping, Sequence

import networkx as nx

SCHEMA_VERSION = "current-ticket-work-graph-3-structural-compression"

# These keys describe measurements/protocol metadata rather than the identity of
# the thing being analysed. This is schema semantics, not domain routing.
_MEASUREMENT_KEYS = {
    "metric",
    "measure",
    "value",
    "unit",
    "scope",
    "timestamp",
    "time",
    "date",
    "source",
    "confidence",
    "provenance",
}


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
    raw = (
        graph.get("work_units")
        or graph.get("nodes")
        or task.get("work_units")
        or task.get("subtasks")
    )
    result: list[dict[str, Any]] = []
    for index, value in enumerate(_sequence(raw), 1):
        row = dict(value) if isinstance(value, Mapping) else {"payload": value}
        payload = (
            row.get("payload")
            or row.get("objective")
            or row.get("task")
            or row.get("text")
            or value
        )
        unit_id = str(row.get("unit_id") or row.get("id") or f"work-{index}").strip()
        dependencies = row.get("dependencies") or row.get("depends_on") or []
        result.append(
            {
                "unit_id": unit_id,
                "source_kind": str(
                    row.get("kind") or row.get("type") or "task-unit"
                ).strip()
                or "task-unit",
                "payload": payload,
                "dependencies": [
                    str(item)
                    for item in _sequence(dependencies)
                    if str(item).strip()
                ],
                "dependency_source": "ticket-explicit",
            }
        )
    return result


def _categorical(value: Any) -> str | None:
    if isinstance(value, bool):
        return str(value).casefold()
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value).strip()
    return None


def _evidence_anchor(rows: list[Mapping[str, Any]]) -> tuple[str, dict[str, int]] | None:
    """Find the current-ticket field that best identifies repeated evidence groups.

    The score rewards both repeated support and distinct groups. A per-row unique
    identifier receives no repeated-support score, preventing accidental one-row
    one-expert explosions. Very small evidence sets may still expose unique
    categorical branches when the set itself is smaller than its information
    scale derived from the current evidence text.
    """
    if len(rows) < 2:
        return None
    values_by_key: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for key, raw in row.items():
            key_text = str(key).strip()
            if not key_text or key_text.casefold() in _MEASUREMENT_KEYS:
                continue
            value = _categorical(raw)
            if value is not None:
                values_by_key[key_text].append(value)

    scored: list[tuple[int, int, int, str, dict[str, int]]] = []
    evidence_scale = max(2, math.ceil(math.log2(len(_text(rows)) + 1)))
    for key, values in values_by_key.items():
        counts = Counter(values)
        unique = len(counts)
        if unique < 2:
            continue
        repeated_rows = sum(count for count in counts.values() if count > 1)
        coverage = len(values)
        allow_small_unique = repeated_rows == 0 and len(rows) <= evidence_scale
        if repeated_rows == 0 and not allow_small_unique:
            continue
        repetition_score = repeated_rows if repeated_rows else coverage
        # Maximize supported distinct branches, then coverage. The key name is
        # only a deterministic tie-breaker; no business keyword is interpreted.
        scored.append(
            (
                repetition_score * unique,
                coverage,
                unique,
                key,
                dict(counts),
            )
        )
    if not scored:
        return None
    _, _, _, key, counts = max(scored, key=lambda item: (item[0], item[1], item[2], item[3]))
    return key, counts


def _clustered_evidence_units(
    question: Any,
    evidence: list[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mappings = [dict(row) for row in evidence if isinstance(row, Mapping)]
    non_mappings = [row for row in evidence if not isinstance(row, Mapping)]
    anchor = _evidence_anchor(mappings)

    if anchor is None:
        payload = {
            "objective": question,
            "evidence": evidence,
            "branch_policy": "single-current-ticket-evidence-analysis",
        }
        return [
            {
                "unit_id": "analysis-1",
                "source_kind": "evidence-analysis",
                "payload": payload,
                "dependencies": [],
                "dependency_source": "structural-analysis-unit",
            }
        ], {
            "mode": "single-analysis-branch",
            "anchor_key": None,
            "source_evidence_count": len(evidence),
            "analysis_branch_count": 1,
        }

    anchor_key, counts = anchor
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    shared: list[Any] = list(non_mappings)
    for row in mappings:
        anchor_value = _categorical(row.get(anchor_key))
        if anchor_value is None:
            shared.append(row)
            continue
        groups[anchor_value].append(row)

    # If a chosen field has singleton values alongside repeated groups, those
    # singleton rows are safer as shared context than as paid expert branches.
    repeated_exists = any(count > 1 for count in counts.values())
    if repeated_exists:
        for value in list(groups):
            if len(groups[value]) == 1:
                shared.extend(groups.pop(value))

    if len(groups) < 2:
        payload = {
            "objective": question,
            "evidence": evidence,
            "branch_policy": "single-current-ticket-evidence-analysis",
        }
        return [
            {
                "unit_id": "analysis-1",
                "source_kind": "evidence-analysis",
                "payload": payload,
                "dependencies": [],
                "dependency_source": "structural-analysis-unit",
            }
        ], {
            "mode": "single-analysis-branch",
            "anchor_key": None,
            "source_evidence_count": len(evidence),
            "analysis_branch_count": 1,
        }

    units: list[dict[str, Any]] = []
    for index, (anchor_value, rows) in enumerate(sorted(groups.items()), 1):
        units.append(
            {
                "unit_id": f"analysis-{index}-{_slug(anchor_value, str(index))}",
                "source_kind": "evidence-cluster",
                "payload": {
                    "objective": question,
                    "evidence_anchor": {
                        "key": anchor_key,
                        "value": anchor_value,
                    },
                    "branch_evidence": rows,
                    "shared_evidence": shared,
                    "branch_policy": "independent-current-ticket-evidence-cluster",
                },
                "dependencies": [],
                "dependency_source": "structural-independent-evidence-cluster",
            }
        )
    return units, {
        "mode": "current-ticket-evidence-clusters",
        "anchor_key": anchor_key,
        "source_evidence_count": len(evidence),
        "shared_evidence_count": len(shared),
        "analysis_branch_count": len(units),
        "group_sizes": {
            value: len(rows) for value, rows in sorted(groups.items())
        },
        "business_keyword_routing_used": False,
    }


def _structural_units(packet: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    task = _mapping(packet.get("task"))
    question = (
        task.get("question")
        or packet.get("question")
        or task.get("objective")
        or packet.get("objective")
        or ""
    )
    evidence = _sequence(packet.get("evidence"))
    requirements = _sequence(task.get("requirements"))
    acceptance = _sequence(packet.get("execution_acceptance"))
    deliverables = _deliverables(task)

    if evidence:
        units, compression = _clustered_evidence_units(question, evidence)
    else:
        units = [
            {
                "unit_id": "analysis-1",
                "source_kind": "task-analysis",
                "payload": {"objective": question or packet},
                "dependencies": [],
                "dependency_source": "structural-analysis-unit",
            }
        ]
        compression = {
            "mode": "task-objective-analysis",
            "anchor_key": None,
            "source_evidence_count": 0,
            "analysis_branch_count": 1,
        }

    # Constraints remain visible in graph audit and in the original task sent to
    # every model, but do not masquerade as independent paid analysis work.
    compression.update(
        {
            "requirement_constraint_count": len(requirements),
            "acceptance_constraint_count": len(acceptance),
            "delivery_contract_item_count": len(deliverables),
            "requirements_create_work_units": False,
            "acceptance_create_work_units": False,
            "deliverables_create_work_units": False,
        }
    )
    return units, compression


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
        for score, source, target in sorted(
            candidates, key=lambda item: (-item[0], item[1], item[2])
        )
        if score >= threshold
    ]


def build_current_work_graph(packet: Mapping[str, Any]) -> dict[str, Any]:
    explicit = _explicit_units(packet)
    if explicit:
        units = explicit
        compression = {
            "mode": "ticket-explicit-work-units",
            "explicit_work_unit_count": len(units),
            "structural_compression_applied": False,
        }
    else:
        units, compression = _structural_units(packet)
        compression["structural_compression_applied"] = True

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
                    "payload": "integrate current independent analysis branches into the required delivery",
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
        "source": "current-ticket-analysis-work-dag",
        "work_units": units,
        "dependency_edges": edges,
        "relatedness_edges": relatedness,
        "relatedness_edge_count": len(relatedness),
        "relatedness_is_dependency": False,
        "dependency_policy": "ticket-explicit-or-final-integration-only",
        "structural_compression": compression,
        "work_unit_count": len(units),
        "dependency_edge_count": len(edges),
        "maximum_depth": len(generations),
        "maximum_parallel_width": max(
            (len(row) for row in generations), default=1
        ),
        "maximum_fan_in": max(
            (graph.in_degree(node) for node in graph), default=0
        ),
        "counts_by_kind": counts,
        "entry_unit_ids": sorted(
            node for node in graph if graph.in_degree(node) == 0
        ),
        "sink_unit_ids": sorted(
            node for node in graph if graph.out_degree(node) == 0
        ),
        "semantic_keyword_routing_used": False,
        "domain_hardcoding_used": False,
        "cross_task_history_used": False,
        "finite_acyclic_validated_by_networkx": True,
    }


__all__ = ["SCHEMA_VERSION", "build_current_work_graph"]
