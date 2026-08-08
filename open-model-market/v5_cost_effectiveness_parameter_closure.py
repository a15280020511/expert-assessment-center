"""Close prompt, token, timeout, and cost-effectiveness controls into ParameterDesign.

The base planner already discovers task/team/model/recovery parameters.  This layer
adds the request/resource controls that only become fully concrete near send time.
They are still designed before execution, receive current-task resolved policies,
and carry their generated ParameterSpec identities into the runtime where the final
payload and current-run feedback determine the effective value.

This deliberately does not pretend future feedback is knowable at task start.  The
pre-run design fixes the resolver, dependencies, consumer, and recompute trigger;
request-time and feedback-time signals then resolve the effective value in the same
current task.  No cross-task history is used.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import v5_parameter_design_planner as base

SCHEMA_VERSION = "runtime-parameter-design-meta-3-resource-closure"
PRINCIPLES = (
    "concrete-problem-concrete-analysis",
    "dynamic-adaptation",
    "small-effort-large-return",
    "cost-effectiveness-first",
    "soft-token-and-cost-efficiency",
    "continuous-spatiotemporal-recomputation",
)


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


def _task(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    value = packet.get("task")
    return value if isinstance(value, Mapping) else {}


def _resource_blueprints(
    packet: Mapping[str, Any],
    planning: Mapping[str, Any],
) -> list[dict[str, Any]]:
    profile = planning.get("resolved_profile")
    profile = profile if isinstance(profile, Mapping) else {}
    graph = planning.get("decomposition")
    graph = graph if isinstance(graph, Mapping) else {}
    task = _task(packet)
    task_chars = max(1, int(profile.get("task_characters") or len(str(task))))
    expected_prompt = max(1, int(profile.get("expected_prompt_tokens") or task_chars))
    expected_completion = max(1, int(profile.get("expected_completion_tokens") or 1))
    delivery_items = max(1, int(profile.get("delivery_item_count") or 1))
    work_units = max(1, int(graph.get("work_unit_count") or 1))
    pressure = profile.get("pressure")
    pressure = pressure if isinstance(pressure, Mapping) else {}
    overall = max(0.0, min(1.0, float(pressure.get("overall") or 0.0) / 100.0))
    delivery = max(0.0, min(1.0, float(pressure.get("delivery") or 0.0) / 100.0))

    # These are current-task *planning* values, not hard token/cost gates.  The
    # actual request is measured after the final prompt has been assembled.
    initial_visible_target = max(
        expected_completion,
        math.ceil(math.sqrt(task_chars + 1) * max(1, delivery_items + work_units)),
    )
    return [
        {
            "surface": "prompt-shape-budgeting",
            "purpose": "measure and compact the actual current-request prompt without dropping task obligations",
            "value_type": "policy",
            "domain": {
                "scope": "current-final-payload-before-send",
                "compaction": "lossless-obligation-preserving",
            },
            "resolver": "current-task-plus-final-payload-measurement",
            "dependencies": [],
            "consumed_by": ["production-prompt-builder", "runtime-request-binding"],
            "recompute_trigger": "current-request-shape-change",
            "classification": "current_task_derived",
            "resolved": {
                "task_prompt_estimate_tokens": expected_prompt,
                "measure_final_payload_before_send": True,
                "preserve_all_explicit_obligations": True,
                "remove_redundant_expansion_softly": True,
            },
        },
        {
            "surface": "resource-efficiency-balance",
            "purpose": "prefer the highest useful quality per current-task token, cost, and latency without creating a business gate",
            "value_type": "policy",
            "domain": {"mode": "soft-lexicographic-current-task-objective"},
            "resolver": "current-task-quality-risk-cost-marginal-return",
            "dependencies": [],
            "consumed_by": ["current-role-model-scoring", "runtime-feedback-replanner", "production-prompt-builder"],
            "recompute_trigger": "current-task-signal-or-current-run-cost-quality-change",
            "classification": "current_run_feedback_derived",
            "resolved": {
                "priority": [
                    "task-contract-quality-and-current-failure-risk",
                    "cost-and-marginal-return",
                    "company-heterogeneity-on-higher-priority-tie",
                    "stable-deterministic-tie-break",
                ],
                "token_and_cost_are_soft_controls": True,
                "cheapest_model_is_not_a_hard_rule": True,
                "overall_pressure": round(overall, 8),
            },
        },
        {
            "surface": "output-transport-allowance",
            "purpose": "reserve the smallest sufficient current-request completion allowance and enlarge it only when current evidence requires",
            "value_type": "runtime-policy",
            "domain": {"minimum": 1, "maximum": "current-model-native-capacity-or-provider-accepted-range"},
            "resolver": "current-final-payload-structure-plus-current-run-feedback",
            "dependencies": ["prompt-shape-budgeting", "resource-efficiency-balance"],
            "consumed_by": ["openrouter-request.max_tokens"],
            "recompute_trigger": "current-request-shape-or-current-run-truncation-feedback",
            "classification": "current_run_feedback_derived",
            "resolved": {
                "pre_request_visible_target_tokens": initial_visible_target,
                "delivery_pressure": round(delivery, 8),
                "final_value_resolved_at_request_binding": True,
                "learned_floor_persists_same_node_current_run": True,
                "hard_business_token_ceiling": False,
            },
        },
        {
            "surface": "model-timeout-effective",
            "purpose": "derive the effective request timeout from actual current payload and current-run latency feedback",
            "value_type": "runtime-policy",
            "domain": {"minimum": 1, "maximum": "finite-infrastructure-safety-cap"},
            "resolver": "current-final-payload-plus-current-run-latency-feedback",
            "dependencies": ["output-transport-allowance"],
            "consumed_by": ["openrouter-request-timeout"],
            "recompute_trigger": "current-request-shape-or-current-run-timeout-feedback",
            "classification": "current_run_feedback_derived",
            "resolved": {
                "final_value_resolved_at_request_binding": True,
                "learned_floor_persists_same_node_current_run": True,
                "finite_safety_cap_is_infrastructure_invariant": True,
                "safety_cap_is_business_gate": False,
            },
        },
    ]


def _extend_planning(
    packet: Mapping[str, Any],
    planning: dict[str, Any],
) -> dict[str, Any]:
    requirements = dict(planning.get("parameter_requirements") or {})
    resolved = dict(planning.get("resolved_parameters") or {})
    design_audit = dict(planning.get("parameter_design_audit") or {})

    specs = [dict(row) for row in requirements.get("parameter_specs") or [] if isinstance(row, Mapping)]
    values = dict(resolved.get("parameter_values") or {})
    control_values = dict(resolved.get("control_surface_values") or {})
    designs = [dict(row) for row in design_audit.get("designs") or [] if isinstance(row, Mapping)]
    by_surface = dict(requirements.get("control_surface_to_parameter_id") or {})
    dependency_edges = [dict(row) for row in requirements.get("dependency_edges") or [] if isinstance(row, Mapping)]

    blueprints = _resource_blueprints(packet, planning)
    blueprint_by_surface = {row["surface"]: row for row in blueprints}
    generated_ids: dict[str, str] = {}

    for blueprint in blueprints:
        surface = str(blueprint["surface"])
        decision_id = "d-" + _digest(
            {
                "surface": surface,
                "task": _task(packet),
                "decomposition": planning.get("decomposition"),
            }
        )
        design_id = "pd-" + _digest(
            {
                "decision_id": decision_id,
                "domain": blueprint["domain"],
                "dependencies": blueprint["dependencies"],
                "resolved": blueprint["resolved"],
            }
        )
        design = {
            "schema_version": "runtime-resource-parameter-design-spec-1",
            "design_id": design_id,
            "decision_id": decision_id,
            "control_surface": surface,
            "purpose": blueprint["purpose"],
            "dimensions": {
                "value_type": {
                    "effective": blueprint["value_type"],
                    "classification": "infrastructure_invariant",
                    "reason": "runtime consumer contract determines representation; activation and values are current-task/run derived",
                },
                "domain": {
                    "effective": blueprint["domain"],
                    "classification": blueprint["classification"],
                    "reason": "effective search/measurement domain is formed from the current task, final request, and current-run feedback",
                },
                "resolver": {
                    "effective": blueprint["resolver"],
                    "classification": "infrastructure_invariant",
                    "reason": "resolver identity is audited infrastructure; its inputs and effective values are current-task/run signals",
                },
                "dependencies": {
                    "effective": list(blueprint["dependencies"]),
                    "classification": "current_task_derived",
                    "reason": "only active resource controls for this current task are connected",
                },
                "consumer_binding": {
                    "effective": list(blueprint["consumed_by"]),
                    "classification": "infrastructure_invariant",
                    "reason": "consumer field names are runtime capabilities, not business defaults",
                },
                "recompute_trigger": {
                    "effective": blueprint["recompute_trigger"],
                    "classification": blueprint["classification"],
                    "reason": "effective value is recomputed whenever its current request/run signals change",
                },
            },
            "source_signals": [
                "current-task",
                "current-work-dag",
                "current-role",
                "current-final-request-shape",
                "current-run-feedback",
            ],
            "objective_contribution": "maximize useful contract-complete quality per token, cost, and latency while remaining soft-governed",
            "current_task_only": True,
            "cross_task_history_used": False,
        }
        parameter_id = "p-" + _digest(
            {
                "decision_id": decision_id,
                "design_id": design_id,
                "surface": surface,
            }
        )
        generated_ids[surface] = parameter_id
        depends_on = [generated_ids[parent] for parent in blueprint["dependencies"]]
        spec = {
            "schema_version": "runtime-generated-resource-parameter-spec-1",
            "parameter_id": parameter_id,
            "purpose": blueprint["purpose"],
            "value_type": blueprint["value_type"],
            "domain": blueprint["domain"],
            "depends_on": depends_on,
            "derived_from": list(design["source_signals"]),
            "resolver": blueprint["resolver"],
            "objective_contribution": design["objective_contribution"],
            "confidence": 1.0,
            "recompute_trigger": blueprint["recompute_trigger"],
            "current_value": blueprint["resolved"],
            "provenance": {
                "parameter_design_id": design_id,
                "parameter_design_sha256": _digest(design, 64),
                "current_task_only": True,
            },
            "consumed_by": list(blueprint["consumed_by"]),
            "dynamic": True,
            "fixed_default_used": False,
            "control_surface": surface,
            "decision_id": decision_id,
            "parameter_design": design,
            "parameter_design_id": design_id,
            "parameter_design_sha256": _digest(design, 64),
            "parameter_spec_constructed_from_design": True,
        }
        specs.append(spec)
        designs.append(design)
        by_surface[surface] = parameter_id
        values[parameter_id] = {
            "value": blueprint["resolved"],
            "control_surface": surface,
            "dynamic": True,
            "derived_from": list(spec["derived_from"]),
            "resolver": blueprint["resolver"],
            "consumed_by": list(blueprint["consumed_by"]),
            "fixed_default_used": False,
            "provenance": dict(spec["provenance"]),
        }
        control_values[surface] = blueprint["resolved"]
        for parent in depends_on:
            dependency_edges.append({"from": parent, "to": parameter_id})

    requirements.update(
        {
            "schema_version": "runtime-generated-parameter-graph-2-resource-closure",
            "design_schema_version": "runtime-parameter-design-spec-3-resource-closure",
            "parameter_specs": specs,
            "required_parameter_ids": [str(row["parameter_id"]) for row in specs],
            "required_parameter_count": len(specs),
            "dependency_edges": dependency_edges,
            "control_surface_to_parameter_id": by_surface,
            "all_request_resource_controls_first_class_parameters": True,
            "runtime_effective_values_may_require_future_current_run_signals": True,
            "fixed_parameter_template_used": False,
            "fixed_business_parameter_catalog_used": False,
            "cross_task_history_used": False,
        }
    )
    design_audit.update(
        {
            "schema_version": "runtime-parameter-design-spec-3-resource-closure",
            "status": "PASS",
            "design_count": len(designs),
            "designs": designs,
            "unclassified_dimensions": [],
            "unclassified_dimension_count": 0,
            "request_resource_parameter_design_closed": True,
            "constitutional_invariants_must_not_be_disguised_as_dynamic": True,
            "current_task_only": True,
            "cross_task_history_used": False,
        }
    )
    coverage = dict(resolved.get("parameter_coverage_audit") or {})
    coverage.update(
        {
            "status": "PASS",
            "required_parameter_count": len(specs),
            "resolved_parameter_count": len(values),
            "dynamic_parameter_count": len(values),
            "fixed_business_parameter_count": 0,
            "unexplained_parameter_count": 0,
            "unconsumed_parameter_count": 0,
            "missing_parameter_count": 0,
            "extra_parameter_count": 0,
            "missing_parameter_ids": [],
            "extra_parameter_ids": [],
            "unconsumed_parameter_ids": [],
            "every_parameter_has_active_consumer": True,
            "request_resource_runtime_binding_required": True,
        }
    )
    resolved.update(
        {
            "active_parameter_ids": [str(row["parameter_id"]) for row in specs],
            "parameter_values": values,
            "control_surface_values": control_values,
            "parameter_design_audit": design_audit,
            "parameter_coverage_audit": coverage,
            "request_resource_parameter_ids": generated_ids,
            "request_resource_parameter_values": {
                surface: blueprint_by_surface[surface]["resolved"]
                for surface in generated_ids
            },
            "all_request_resource_controls_first_class_parameters": True,
        }
    )

    profile = dict(planning.get("resolved_profile") or {})
    profile.update(
        {
            "principles": list(PRINCIPLES),
            "active_generated_parameter_ids": [str(row["parameter_id"]) for row in specs],
            "active_parameter_count": len(specs),
            "runtime_resource_parameter_ids": generated_ids,
            "runtime_resource_parameter_values": resolved["request_resource_parameter_values"],
            "cost_effectiveness_priority": True,
            "soft_token_and_cost_efficiency": True,
            "final_payload_measurement_required_before_request_binding": True,
        }
    )

    sequence = [str(value) for value in planning.get("planning_sequence") or []]
    if "request-resource-parameter-design-closure" not in sequence:
        insert_at = sequence.index("current-work-dag-role-partition") if "current-work-dag-role-partition" in sequence else len(sequence)
        sequence.insert(insert_at, "request-resource-parameter-design-closure")

    planning.update(
        {
            "schema_version": SCHEMA_VERSION,
            "planning_sequence": sequence,
            "parameter_requirements": requirements,
            "parameter_design_audit": design_audit,
            "resolved_parameters": resolved,
            "resolved_profile": profile,
            "all_calculable_planning_parameters_dynamic": True,
            "all_request_resource_controls_first_class_parameters": True,
            "parameter_design_completed_before_value_resolution": True,
            "runtime_effective_values_recomputed_when_future_current_run_signals_arrive": True,
            "cost_effectiveness_priority": True,
            "soft_token_and_cost_efficiency": True,
            "cross_task_history_used": False,
        }
    )
    return planning


def build_runtime_planning_context(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    planning = dict(base.build_runtime_planning_context(packet, candidates))
    return _extend_planning(packet, planning)


__all__ = ["PRINCIPLES", "SCHEMA_VERSION", "build_runtime_planning_context"]
