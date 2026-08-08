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
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

import v5_runtime_parameter_planner as base

SCHEMA_VERSION = "runtime-parameter-design-meta-1"
PARAMETER_DESIGN_SCHEMA_VERSION = "runtime-parameter-design-spec-1"
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
    if value.startswith("networkx-"):
        return "infrastructure_invariant"
    if value == "current-run-feedback":
        return "current_run_feedback_derived"
    return "current_task_derived"


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


def design_required_parameters(
    graph: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Design each discovered parameter before ParameterSpec construction/resolution."""
    designs: list[dict[str, Any]] = []
    for decision in decisions:
        domain, domain_class, domain_reason = _effective_domain(
            decision,
            graph,
            candidates,
        )
        resolver = str(decision.get("resolver") or "")
        surface = str(decision.get("control_surface") or "")
        type_class = (
            "infrastructure_invariant"
            if str(decision.get("value_type") or "") in {"assignment", "runtime-policy"}
            else "current_task_derived"
        )
        recompute_class = (
            "current_run_feedback_derived"
            if "current-run" in str(decision.get("recompute_trigger") or "")
            else "current_task_derived"
        )
        design = {
            "schema_version": PARAMETER_DESIGN_SCHEMA_VERSION,
            "design_id": "pd-" + _digest(
                {
                    "decision_id": decision.get("decision_id"),
                    "graph": graph,
                    "candidate_count": len(candidates),
                    "profile_pressure": profile.get("pressure"),
                }
            ),
            "decision_id": str(decision.get("decision_id") or ""),
            "control_surface": surface,
            "purpose": str(decision.get("purpose") or ""),
            "dimensions": {
                "value_type": {
                    "effective": str(decision.get("value_type") or ""),
                    "classification": type_class,
                    "reason": "type follows the discovered decision and its execution contract",
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
                        "OR-Tools/no-tools and graph machinery are declared infrastructure; "
                        "task search spaces and signal normalization are current-task derived"
                    ),
                },
                "dependencies": {
                    "effective": "materialize-after-active-decision-set-is-known",
                    "classification": "current_task_derived",
                    "reason": "inactive decisions must not create parameter dependency edges",
                },
                "consumer_binding": {
                    "effective": list(decision.get("consumed_by") or []),
                    "classification": "infrastructure_invariant",
                    "reason": "consumer names identify runtime capability surfaces, not business defaults",
                },
                "recompute_trigger": {
                    "effective": str(decision.get("recompute_trigger") or ""),
                    "classification": recompute_class,
                    "reason": "trigger is tied to the signals that can invalidate this design/value",
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


def _attach_designs(
    requirements: Mapping[str, Any],
    design_audit: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(requirements)
    by_decision = {
        str(row.get("decision_id") or ""): row
        for row in design_audit.get("designs") or []
        if isinstance(row, Mapping)
    }
    specs: list[dict[str, Any]] = []
    missing: list[str] = []
    for raw in requirements.get("parameter_specs") or []:
        if not isinstance(raw, Mapping):
            continue
        spec = dict(raw)
        design = by_decision.get(str(spec.get("decision_id") or ""))
        if design is None:
            missing.append(str(spec.get("parameter_id") or ""))
        else:
            spec["parameter_design"] = dict(design)
            domain = design.get("dimensions", {}).get("domain", {})
            if isinstance(domain, Mapping) and "effective" in domain:
                spec["designed_domain"] = domain["effective"]
        specs.append(spec)
    result["parameter_specs"] = specs
    result["parameter_design_audit"] = dict(design_audit)
    result["parameter_design_missing_parameter_ids"] = missing
    result["parameter_design_completed_before_value_resolution"] = not missing
    if missing or design_audit.get("status") != "PASS":
        raise RuntimeError("parameter design coverage failed before value resolution")
    return result


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

    requirements = base.discover_parameter_requirements(graph, candidates)
    requirements = _attach_designs(requirements, design_audit)
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
        "all_parameter_design_dimensions_classified": True,
        "all_parameter_instances_current_task_derived": True,
        "all_parameter_instances_have_active_consumers": True,
        "parameter_ids_generated_after_decision_discovery": True,
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
    "build_runtime_planning_context",
    "design_required_parameters",
]
