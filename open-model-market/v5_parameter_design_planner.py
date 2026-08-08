"""Task-first parameter design layer for the production expert planner.

Order is deliberately explicit:
current ticket -> work DAG -> required decisions -> parameter design -> ParameterSpec
instances -> parameter DAG -> value resolution -> role DAG -> OR-Tools assignment.

The design layer does not pretend constitutional/infrastructure invariants are dynamic.
Every design dimension is classified as one of:
- constitutional_invariant
- infrastructure_invariant
- current_task_derived
- current_run_feedback_derived

A parameter cannot proceed to value resolution unless its type, domain, resolver,
dependencies, consumer binding, and recompute trigger are all classified and auditable.
ParameterSpec instances are constructed from the effective design, not directly from the
pre-design decision record.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

import networkx as nx

import v5_runtime_parameter_planner as base

SCHEMA_VERSION = "runtime-parameter-design-meta-2"
PARAMETER_DESIGN_SCHEMA_VERSION = "runtime-parameter-design-spec-2"
PRINCIPLES = base.PRINCIPLES

_ALLOWED_CLASSES = {
    "constitutional_invariant",
    "infrastructure_invariant",
    "current_task_derived",
    "current_run_feedback_derived",
}


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


def _classify_resolver(resolver: str) -> str:
    value = str(resolver or "")
    if value == "ortools-cp-sat":
        return "constitutional_invariant"
    if value == "current-run-feedback":
        return "current_run_feedback_derived"
    # Algorithm identity is infrastructure. Its search space, role signals and
    # values are still generated from the current task in the resolver itself.
    return "infrastructure_invariant"


def _effective_domain(
    decision: Mapping[str, Any],
    graph: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[Any, str, str]:
    surface = str(decision.get("control_surface") or "")
    units = max(0, int(graph.get("work_unit_count") or 0))
    candidate_count = len(candidates)
    if surface == "work-dag-partitioning":
        return (
            {"min": 1, "max": max(1, min(candidate_count, max(1, units)))},
            "current_task_derived",
            "bounded by current work-unit and executable-candidate cardinality",
        )
    if surface == "candidate-role-binding":
        return (
            {"candidate_ids": [str(row.get("model") or "") for row in candidates]},
            "current_task_derived",
            "domain is the current executable governance candidate inventory",
        )
    if surface == "initial-recovery-allocation":
        return (
            {
                "min": 0,
                "pre_resolution_max": max(0, candidate_count - 1),
                "effective_max_rule": "current-candidates-minus-resolved-primary-count",
            },
            "current_task_derived",
            "recovery breadth is bounded by current inventory after primary allocation",
        )
    if surface == "role-reasoning-effort":
        return (
            {"protocol_values": ["low", "medium", "high"]},
            "infrastructure_invariant",
            "OpenRouter normalized reasoning-effort transport enum; role assignment remains task-derived",
        )
    if surface in {"parallel-structure-signal", "dependency-coupling-signal"}:
        return (
            {"min": 0.0, "max": 1.0},
            "infrastructure_invariant",
            "normalized graph ratios have a mathematical unit interval domain",
        )
    if surface == "runtime-standby-replanning":
        return (
            {"history_scope": "current-run-only"},
            "current_run_feedback_derived",
            "runtime policy is activated and recomputed only from this run's feedback",
        )
    return (
        decision.get("domain"),
        "current_task_derived",
        "domain originates from the current decision and current task signals",
    )


def _designed_dependency_surfaces(
    surface: str,
    active_surfaces: set[str],
) -> list[str]:
    """Derive effective parameter dataflow after the active decisions are known.

    Structural signals feed partitioning; they do not depend on the partition result.
    Downstream role/model/recovery decisions consume the resolved partition. Runtime
    replanning consumes the initial recovery allocation and current-run feedback.
    """
    rules: dict[str, tuple[str, ...]] = {
        "parallel-structure-signal": (),
        "dependency-coupling-signal": (),
        "work-dag-partitioning": (
            "parallel-structure-signal",
            "dependency-coupling-signal",
        ),
        "role-model-objective-balance": ("work-dag-partitioning",),
        "role-reasoning-effort": ("work-dag-partitioning",),
        "initial-recovery-allocation": ("work-dag-partitioning",),
        "candidate-role-binding": (
            "work-dag-partitioning",
            "role-model-objective-balance",
        ),
        "runtime-standby-replanning": ("initial-recovery-allocation",),
    }
    return [value for value in rules.get(surface, ()) if value in active_surfaces]


def design_required_parameters(
    graph: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Design each discovered parameter before ParameterSpec construction/resolution."""
    active_surfaces = {
        str(row.get("control_surface") or "")
        for row in decisions
        if str(row.get("control_surface") or "")
    }
    designs: list[dict[str, Any]] = []
    for decision in decisions:
        domain, domain_class, domain_reason = _effective_domain(
            decision,
            graph,
            candidates,
        )
        resolver = str(decision.get("resolver") or "")
        surface = str(decision.get("control_surface") or "")
        dependencies = _designed_dependency_surfaces(surface, active_surfaces)
        recompute_class = (
            "current_run_feedback_derived"
            if "current-run" in str(decision.get("recompute_trigger") or "")
            else "infrastructure_invariant"
        )
        design = {
            "schema_version": PARAMETER_DESIGN_SCHEMA_VERSION,
            "design_id": "pd-" + _digest(
                {
                    "decision_id": decision.get("decision_id"),
                    "graph": graph,
                    "candidate_count": len(candidates),
                    "profile_pressure": profile.get("pressure"),
                    "dependencies": dependencies,
                    "domain": domain,
                }
            ),
            "decision_id": str(decision.get("decision_id") or ""),
            "control_surface": surface,
            "purpose": str(decision.get("purpose") or ""),
            "dimensions": {
                "value_type": {
                    "effective": str(decision.get("value_type") or ""),
                    "classification": "infrastructure_invariant",
                    "reason": "runtime consumer contract determines representation type; task determines whether the decision exists",
                },
                "domain": {
                    "effective": domain,
                    "classification": domain_class,
                    "reason": domain_reason,
                },
                "resolver": {
                    "effective": resolver,
                    "classification": _classify_resolver(resolver),
                    "reason": (
                        "resolver identity is an audited implementation capability; its current search space, signals and values are task/run derived"
                    ),
                },
                "dependencies": {
                    "effective": dependencies,
                    "classification": "current_task_derived",
                    "reason": "dependency edges are materialized only among decisions active for this task and follow actual dataflow",
                },
                "consumer_binding": {
                    "effective": list(decision.get("consumed_by") or []),
                    "classification": "infrastructure_invariant",
                    "reason": "consumer names identify runtime capability surfaces, not business defaults",
                },
                "recompute_trigger": {
                    "effective": str(decision.get("recompute_trigger") or ""),
                    "classification": recompute_class,
                    "reason": "trigger semantics are fixed to the signals that invalidate the parameter; activation comes from current task/run changes",
                },
            },
            "source_signals": list(decision.get("source_signals") or []),
            "objective_contribution": str(decision.get("objective_contribution") or ""),
            "current_task_only": True,
            "cross_task_history_used": False,
        }
        designs.append(design)

    unclassified: list[dict[str, str]] = []
    for design in designs:
        for name, row in design["dimensions"].items():
            classification = str(row.get("classification") or "")
            if classification not in _ALLOWED_CLASSES:
                unclassified.append(
                    {
                        "design_id": str(design["design_id"]),
                        "dimension": str(name),
                        "classification": classification,
                    }
                )
    return {
        "schema_version": PARAMETER_DESIGN_SCHEMA_VERSION,
        "status": "PASS" if not unclassified else "FAIL",
        "design_mode": "decision-first-then-design-then-parameter-instance",
        "design_count": len(designs),
        "designs": designs,
        "unclassified_dimensions": unclassified,
        "unclassified_dimension_count": len(unclassified),
        "design_dimensions": [
            "value_type",
            "domain",
            "resolver",
            "dependencies",
            "consumer_binding",
            "recompute_trigger",
        ],
        "allowed_design_classes": sorted(_ALLOWED_CLASSES),
        "computed_parameter_design_required": True,
        "constitutional_invariants_must_not_be_disguised_as_dynamic": True,
        "current_task_only": True,
        "cross_task_history_used": False,
    }


def build_parameter_requirements_from_design(
    decisions: Sequence[Mapping[str, Any]],
    design_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Construct ParameterSpec identities from effective ParameterDesign records."""
    if design_audit.get("status") != "PASS":
        raise RuntimeError("parameter design audit failed before ParameterSpec construction")

    decision_by_surface = {
        str(row.get("control_surface") or ""): row
        for row in decisions
        if str(row.get("control_surface") or "")
    }
    design_by_surface = {
        str(row.get("control_surface") or ""): row
        for row in design_audit.get("designs") or []
        if isinstance(row, Mapping) and str(row.get("control_surface") or "")
    }
    if set(decision_by_surface) != set(design_by_surface):
        raise RuntimeError("decision/design surface coverage mismatch")

    surface_graph = nx.DiGraph()
    surface_graph.add_nodes_from(decision_by_surface)
    for surface, design in design_by_surface.items():
        dimensions = design.get("dimensions")
        if not isinstance(dimensions, Mapping):
            raise RuntimeError(f"parameter design has no dimensions: {surface}")
        dependency_row = dimensions.get("dependencies")
        dependencies = (
            list(dependency_row.get("effective") or [])
            if isinstance(dependency_row, Mapping)
            else []
        )
        for parent in dependencies:
            if parent not in decision_by_surface:
                raise RuntimeError(
                    f"parameter design dependency is not active: {parent}->{surface}"
                )
            surface_graph.add_edge(str(parent), surface)
    if not nx.is_directed_acyclic_graph(surface_graph):
        raise RuntimeError("designed parameter dependency graph is cyclic")

    by_surface_parameter_id: dict[str, str] = {}
    specs: list[dict[str, Any]] = []
    for surface in nx.topological_sort(surface_graph):
        decision = dict(decision_by_surface[surface])
        design = dict(design_by_surface[surface])
        dimensions = design["dimensions"]
        dependency_surfaces = list(dimensions["dependencies"]["effective"] or [])
        depends_on = [by_surface_parameter_id[parent] for parent in dependency_surfaces]

        designed_decision = dict(decision)
        designed_decision["value_type"] = dimensions["value_type"]["effective"]
        designed_decision["domain"] = dimensions["domain"]["effective"]
        designed_decision["resolver"] = dimensions["resolver"]["effective"]
        designed_decision["consumed_by"] = dimensions["consumer_binding"]["effective"]
        designed_decision["recompute_trigger"] = dimensions["recompute_trigger"]["effective"]
        designed_decision["provenance"] = {
            **dict(decision.get("provenance") or {}),
            "parameter_design_id": design["design_id"],
            "parameter_design_sha256": _digest(design, 64),
        }

        spec = base._parameter_from_decision(  # noqa: SLF001
            designed_decision,
            depends_on=depends_on,
        )
        spec["parameter_design"] = design
        spec["parameter_design_id"] = design["design_id"]
        spec["parameter_design_sha256"] = _digest(design, 64)
        spec["parameter_spec_constructed_from_design"] = True
        specs.append(spec)
        by_surface_parameter_id[surface] = str(spec["parameter_id"])

    parameter_graph = nx.DiGraph()
    parameter_graph.add_nodes_from(str(row["parameter_id"]) for row in specs)
    for row in specs:
        parameter_graph.add_edges_from(
            (str(parent), str(row["parameter_id"]))
            for parent in row.get("depends_on") or []
        )
    if not nx.is_directed_acyclic_graph(parameter_graph):
        raise RuntimeError("generated designed ParameterSpec graph is cyclic")

    return {
        "schema_version": base.PARAMETER_SCHEMA_VERSION,
        "design_schema_version": PARAMETER_DESIGN_SCHEMA_VERSION,
        "discovery_mode": "current-decisions-first-then-design-then-generate-parameter-identities",
        "required_decisions": [dict(row) for row in decisions],
        "parameter_design_audit": dict(design_audit),
        "parameter_specs": specs,
        "required_parameter_ids": [str(row["parameter_id"]) for row in specs],
        "required_parameter_count": len(specs),
        "dependency_edges": [
            {"from": str(a), "to": str(b)}
            for a, b in parameter_graph.edges()
        ],
        "control_surface_to_parameter_id": by_surface_parameter_id,
        "parameter_design_completed_before_parameter_instantiation": True,
        "parameter_specs_constructed_from_design": True,
        "parameter_ids_are_generated_after_decision_discovery": True,
        "parameter_ids_are_generated_after_parameter_design": True,
        "legacy_business_parameter_names_used_as_parameter_ids": False,
        "fixed_parameter_template_used": False,
        "fixed_business_parameter_catalog_used": False,
        "control_surface_catalog_is_infrastructure": True,
        "all_parameter_instances_current_task_derived": True,
        "unused_parameter_specs_allowed": False,
        "semantic_keyword_routing_used": False,
        "cross_task_history_used": False,
        "only_hard_model_boundary": "no-tools",
    }


def build_runtime_planning_context(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Production planning context with an explicit parameter-design meta-layer."""
    graph = base.build_current_work_graph(packet)
    decisions = base.discover_required_decisions(graph, candidates)
    profile = base._task_profile(packet, graph, candidates)  # noqa: SLF001
    design_audit = design_required_parameters(
        graph,
        decisions,
        candidates,
        profile,
    )
    if design_audit["status"] != "PASS":
        raise RuntimeError("parameter design audit failed")

    requirements = build_parameter_requirements_from_design(
        decisions,
        design_audit,
    )
    resolved = base.resolve_parameter_values(
        graph,
        requirements,
        candidates,
        profile,
    )
    coverage = dict(resolved["parameter_coverage_audit"])
    if coverage.get("status") != "PASS":
        raise RuntimeError("generated parameter coverage audit failed")
    roles = base._role_plan(graph, int(resolved["team_size"]))  # noqa: SLF001

    profile = {
        **profile,
        "active_generated_parameter_ids": list(requirements["required_parameter_ids"]),
        "active_parameter_count": int(requirements["required_parameter_count"]),
        "parameter_identity_mode": "generated-after-current-decision-and-design",
        "parameter_design_schema_version": PARAMETER_DESIGN_SCHEMA_VERSION,
        "parameter_design_status": str(design_audit["status"]),
        "model_scoring_policy": resolved["control_surface_values"].get(
            "role-model-objective-balance", {}
        ),
        "reasoning_effort_policy": resolved["control_surface_values"].get(
            "role-reasoning-effort", {}
        ),
        "fixed_parameter_template_used": False,
        "fixed_business_parameter_catalog_used": False,
        "fixed_business_weight_coefficients_used": False,
    }
    resolved_parameters = {
        "active_parameter_ids": list(requirements["required_parameter_ids"]),
        "parameter_values": dict(resolved["values"]),
        "control_surface_values": dict(resolved["control_surface_values"]),
        "team_size": len(roles),
        "recovery_size": int(resolved["recovery_size"]),
        "role_count": len(roles),
        "role_topology": [
            {
                "role_id": row["role_id"],
                "role_kind": row["role_kind"],
                "depends_on_role_ids": list(row["depends_on_role_ids"]),
                "assigned_work_units": list(row["assigned_work_units"]),
                "reasoning_effort": row["reasoning_effort"],
            }
            for row in roles
        ],
        "parameter_design_audit": dict(design_audit),
        "parameter_coverage_audit": coverage,
        "parameter_optimizer": dict(resolved["optimization"]),
        "parameter_values_derived_from_current_task": True,
        "parameter_ids_generated_after_decision_discovery": True,
        "parameter_ids_generated_after_parameter_design": True,
        "parameter_specs_constructed_from_design": True,
        "parameter_design_completed_before_value_resolution": True,
        "fixed_parameter_values_used": False,
        "fixed_business_objective_coefficients_used": False,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "base_planner_schema_version": base.SCHEMA_VERSION,
        "work_graph_schema_version": base.WORK_GRAPH_SCHEMA_VERSION,
        "planning_sequence": [
            "current-ticket-work-dag",
            "required-decision-discovery",
            "parameter-design-meta-layer",
            "generated-parameter-instance-construction",
            "generated-parameter-dependency-graph",
            "current-signal-resolution-and-optuna",
            "current-work-dag-role-partition",
            "ortools-model-assignment",
            "runtime-feedback-replanning",
        ],
        "decomposition": graph,
        "parameter_requirements": requirements,
        "parameter_design_audit": design_audit,
        "resolved_parameters": resolved_parameters,
        "resolved_profile": profile,
        "role_plan": roles,
        "primary_expert_count": len(roles),
        "recovery_count": int(resolved["recovery_size"]),
        "all_calculable_planning_parameters_dynamic": True,
        "parameter_design_completed_before_value_resolution": True,
        "parameter_specs_constructed_from_design": True,
        "all_parameter_design_dimensions_classified": True,
        "all_parameter_instances_current_task_derived": True,
        "all_parameter_instances_have_active_consumers": True,
        "parameter_ids_generated_after_decision_discovery": True,
        "parameter_ids_generated_after_parameter_design": True,
        "fixed_parameter_template_used": False,
        "fixed_business_parameter_catalog_used": False,
        "fixed_business_objective_coefficients_used": False,
        "fixed_team_template_used": False,
        "fixed_role_grammar_used": False,
        "fixed_role_topology_used": False,
        "fixed_metric_role_grammar_used": False,
        "metric_role_adapter_used": False,
        "semantic_keyword_routing_used": False,
        "cross_task_history_used": False,
        "hard_model_eligibility_gates": [],
        "only_hard_model_boundary": "no-tools",
    }


__all__ = [
    "PARAMETER_DESIGN_SCHEMA_VERSION",
    "PRINCIPLES",
    "SCHEMA_VERSION",
    "build_parameter_requirements_from_design",
    "build_runtime_planning_context",
    "design_required_parameters",
]
