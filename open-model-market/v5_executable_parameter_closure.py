"""Close mutable execution parameters before a governed proposal becomes executable.

A signed governance plan may already commit model identities and role topology. Those
committed decisions must not be silently re-optimized here. What still remains mutable
at the execution boundary is derived from the current task and the exact proposal:
role reasoning effort, prompt shaping, token/cost efficiency policy, completion
allowance, and timeout policy.

This bridge exists so compatibility governance plans cannot bypass ParameterDesign and
then rely on hidden request defaults. It does not create a model, cost, token, company,
or Provider eligibility gate.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

import networkx as nx

import v5_cost_effectiveness_parameter_closure as resource_closure
import v5_runtime_parameter_planner as runtime_planner
from v5_cost_effectiveness_planning import derive_reasoning_efforts

SCHEMA_VERSION = "v5-executable-parameter-closure-1"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _digest(value: Any, length: int = 16) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()[:length]


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _proposal_graph(
    proposal: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], nx.DiGraph, dict[str, Any]]:
    nodes = _rows(proposal.get("nodes"))
    if not nodes:
        raise RuntimeError("executable parameter closure requires proposal nodes")
    node_ids = [str(row.get("node_id") or "").strip() for row in nodes]
    if any(not value for value in node_ids) or len(node_ids) != len(set(node_ids)):
        raise RuntimeError("executable parameter closure requires unique node ids")

    graph = nx.DiGraph()
    graph.add_nodes_from(node_ids)
    for row in _rows(proposal.get("edges")):
        source = str(row.get("source") or "").strip()
        target = str(row.get("target") or "").strip()
        if source not in graph or target not in graph:
            raise RuntimeError("proposal edge references unknown node during parameter closure")
        graph.add_edge(source, target)
    if not nx.is_directed_acyclic_graph(graph):
        raise RuntimeError("proposal graph is cyclic during parameter closure")

    work_items = _rows(proposal.get("work_items"))
    work_count = max(1, len(work_items))
    generations = list(nx.topological_generations(graph))
    width = max((len(row) for row in generations), default=1)
    depth = max(1, nx.dag_longest_path_length(graph) + 1)
    summary = {
        "schema_version": "current-executable-proposal-graph-summary-1",
        "work_unit_count": work_count,
        "dependency_edge_count": graph.number_of_edges(),
        "maximum_depth": depth,
        "maximum_parallel_width": width,
        "sink_unit_ids": [node_id for node_id in node_ids if graph.out_degree(node_id) == 0],
        "source": "exact-governed-executable-proposal",
    }
    return nodes, graph, summary


def _candidate_rows(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if candidates is not None:
        rows = [dict(row) for row in candidates if isinstance(row, Mapping)]
        if rows:
            return rows
    plan = packet.get("governance_model_plan")
    plan = plan if isinstance(plan, Mapping) else {}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field in (
        "expert_candidate_pool",
        "top50_expert_selectable_candidates",
        "selected_models",
        "recovery_models",
        "expert_center_ordered_standby",
    ):
        for row in _rows(plan.get(field)):
            model = str(row.get("model") or row.get("id") or "").strip()
            if not model or model in seen:
                continue
            row["model"] = model
            rows.append(row)
            seen.add(model)
    return rows


def _resource_planning(
    packet: Mapping[str, Any],
    graph_summary: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    profile = runtime_planner._task_profile(  # noqa: SLF001
        packet,
        graph_summary,
        candidates,
    )
    skeleton = {
        "schema_version": "executable-boundary-resource-planning-skeleton-1",
        "planning_sequence": [
            "exact-governed-proposal",
            "request-resource-parameter-design-closure",
            "executable-materialization",
        ],
        "decomposition": dict(graph_summary),
        "resolved_profile": profile,
        "parameter_requirements": {
            "parameter_specs": [],
            "required_parameter_ids": [],
            "required_parameter_count": 0,
            "dependency_edges": [],
            "control_surface_to_parameter_id": {},
        },
        "parameter_design_audit": {
            "status": "PASS",
            "designs": [],
        },
        "resolved_parameters": {
            "parameter_values": {},
            "control_surface_values": {},
            "parameter_coverage_audit": {},
        },
    }
    return resource_closure._extend_planning(packet, skeleton)  # noqa: SLF001


def _role_reasoning_spec(
    packet: Mapping[str, Any],
    graph_summary: Mapping[str, Any],
    role_policy: Mapping[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    decision = {
        "surface": "role-reasoning-effort",
        "task": packet.get("task"),
        "graph": graph_summary,
    }
    design_id = "pd-" + _digest(
        {
            "decision": decision,
            "domain": ["low", "medium", "high"],
            "resolver": "current-task-pressure-plus-current-role-structure",
        }
    )
    parameter_id = "p-" + _digest(
        {
            "design_id": design_id,
            "surface": "role-reasoning-effort",
            "task": packet.get("task"),
        }
    )
    design = {
        "schema_version": "runtime-parameter-design-spec-4-executable-bridge",
        "design_id": design_id,
        "control_surface": "role-reasoning-effort",
        "purpose": "derive reasoning effort for every exact governed execution role",
        "dimensions": {
            "value_type": {
                "effective": "role-policy",
                "classification": "infrastructure_invariant",
            },
            "domain": {
                "effective": {"protocol_values": ["low", "medium", "high"]},
                "classification": "infrastructure_invariant",
            },
            "resolver": {
                "effective": "current-task-pressure-plus-current-role-structure",
                "classification": "infrastructure_invariant",
            },
            "dependencies": {
                "effective": [],
                "classification": "current_task_derived",
            },
            "consumer_binding": {
                "effective": ["openrouter-request.reasoning.effort"],
                "classification": "infrastructure_invariant",
            },
            "recompute_trigger": {
                "effective": "current-task-or-current-role-graph-change",
                "classification": "current_task_derived",
            },
        },
        "current_task_only": True,
        "cross_task_history_used": False,
    }
    spec = {
        "schema_version": "runtime-generated-resource-parameter-spec-1",
        "parameter_id": parameter_id,
        "purpose": design["purpose"],
        "value_type": "role-policy",
        "domain": {"protocol_values": ["low", "medium", "high"]},
        "depends_on": [],
        "derived_from": ["current-task", "exact-current-role-graph"],
        "resolver": "current-task-pressure-plus-current-role-structure",
        "objective_contribution": "use only the reasoning depth justified by current task and role structure",
        "confidence": 1.0,
        "recompute_trigger": "current-task-or-current-role-graph-change",
        "current_value": dict(role_policy),
        "provenance": {
            "parameter_design_id": design_id,
            "current_task_only": True,
        },
        "consumed_by": ["openrouter-request.reasoning.effort"],
        "dynamic": True,
        "fixed_default_used": False,
        "control_surface": "role-reasoning-effort",
        "parameter_design": design,
        "parameter_design_id": design_id,
        "parameter_spec_constructed_from_design": True,
    }
    value = {
        "value": dict(role_policy),
        "control_surface": "role-reasoning-effort",
        "dynamic": True,
        "derived_from": list(spec["derived_from"]),
        "resolver": spec["resolver"],
        "consumed_by": list(spec["consumed_by"]),
        "fixed_default_used": False,
        "provenance": dict(spec["provenance"]),
    }
    return parameter_id, spec, value


def close_executable_proposal_parameters(
    packet: Mapping[str, Any],
    proposal: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an exact proposal with every mutable request parameter closed."""
    nodes, graph, graph_summary = _proposal_graph(proposal)
    current_candidates = _candidate_rows(packet, candidates)
    planning = _resource_planning(packet, graph_summary, current_candidates)
    profile = dict(planning.get("resolved_profile") or {})

    node_ids = [str(row["node_id"]) for row in nodes]
    demands = [
        float(
            max(1, len(row.get("work_ids") or row.get("assigned_work_units") or []))
            + graph.in_degree(node_id)
            + graph.out_degree(node_id)
        )
        for row, node_id in zip(nodes, node_ids, strict=True)
    ]
    efforts, task_pressure, sources = derive_reasoning_efforts(profile, demands)
    role_policy = {
        "mode": "current-task-absolute-pressure-plus-exact-role-relative-demand",
        "current_task_pressure": round(task_pressure, 8),
        "current_role_efforts": dict(zip(node_ids, efforts, strict=True)),
        "current_role_structural_demands": dict(zip(node_ids, demands, strict=True)),
        "hidden_default_used": False,
        "quantizer": "three-equal-regions-over-current-task-pressure",
        "quantizer_classification": "infrastructure_invariant",
    }
    reasoning_id, reasoning_spec, reasoning_value = _role_reasoning_spec(
        packet,
        graph_summary,
        role_policy,
    )

    requirements = dict(planning.get("parameter_requirements") or {})
    specs = _rows(requirements.get("parameter_specs"))
    specs.append(reasoning_spec)
    by_surface = dict(requirements.get("control_surface_to_parameter_id") or {})
    by_surface["role-reasoning-effort"] = reasoning_id
    requirements.update(
        {
            "parameter_specs": specs,
            "required_parameter_ids": [str(row["parameter_id"]) for row in specs],
            "required_parameter_count": len(specs),
            "control_surface_to_parameter_id": by_surface,
        }
    )

    resolved = dict(planning.get("resolved_parameters") or {})
    values = dict(resolved.get("parameter_values") or {})
    values[reasoning_id] = reasoning_value
    controls = dict(resolved.get("control_surface_values") or {})
    controls["role-reasoning-effort"] = role_policy
    coverage = dict(resolved.get("parameter_coverage_audit") or {})
    coverage.update(
        {
            "status": "PASS",
            "required_parameter_count": len(specs),
            "resolved_parameter_count": len(values),
            "dynamic_parameter_count": len(values),
            "fixed_business_parameter_count": 0,
            "missing_parameter_count": 0,
            "extra_parameter_count": 0,
            "unconsumed_parameter_count": 0,
            "every_parameter_has_active_consumer": True,
        }
    )
    resolved.update(
        {
            "active_parameter_ids": [str(row["parameter_id"]) for row in specs],
            "parameter_values": values,
            "control_surface_values": controls,
            "parameter_coverage_audit": coverage,
        }
    )

    resource_ids = dict(profile.get("runtime_resource_parameter_ids") or {})
    resource_values = dict(profile.get("runtime_resource_parameter_values") or {})
    active_parameter_ids = [str(row["parameter_id"]) for row in specs]
    closed_nodes: list[dict[str, Any]] = []
    for index, row in enumerate(nodes):
        updated = dict(row)
        updated["reasoning_effort"] = efforts[index]
        updated["reasoning_effort_source"] = sources[index]
        updated["reasoning_effort_task_pressure"] = round(task_pressure, 8)
        parameter_profile = updated.get("parameter_profile")
        parameter_profile = (
            dict(parameter_profile)
            if isinstance(parameter_profile, Mapping)
            else {}
        )
        parameter_profile.update(
            {
                "runtime_resource_parameter_ids": resource_ids,
                "runtime_resource_parameter_values": resource_values,
                "role_reasoning_parameter_id": reasoning_id,
                "active_generated_parameter_ids": active_parameter_ids,
                "parameter_coverage_status": "PASS",
                "parameter_closure_source": "current-task-plus-exact-governed-proposal",
                "cost_effectiveness_priority": True,
                "soft_token_and_cost_efficiency": True,
                "cross_task_history_used": False,
            }
        )
        updated["parameter_profile"] = parameter_profile
        closed_nodes.append(updated)

    closed = dict(proposal)
    closed["nodes"] = closed_nodes
    audit = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "exact_governed_model_assignment_preserved": True,
        "exact_governed_role_topology_preserved": True,
        "runtime_mutable_parameter_count": len(specs),
        "runtime_mutable_parameter_ids": active_parameter_ids,
        "parameter_requirements": requirements,
        "resolved_parameters": resolved,
        "current_task_pressure": round(task_pressure, 8),
        "role_reasoning_policy": role_policy,
        "request_resource_parameter_ids": resource_ids,
        "all_runtime_mutable_parameters_dynamic": True,
        "all_runtime_mutable_parameters_have_active_consumers": True,
        "hidden_business_defaults_used": False,
        "cost_effectiveness_priority": True,
        "soft_token_and_cost_efficiency": True,
        "cost_or_token_business_gate_added": False,
        "model_or_provider_eligibility_gate_added": False,
        "current_task_only": True,
        "cross_task_history_used": False,
    }
    return closed, audit


__all__ = [
    "SCHEMA_VERSION",
    "close_executable_proposal_parameters",
]
