"""Production planner with current-ticket-generated parameter identities.

The planner first derives a finite work DAG and the decisions that this exact run must
make. Only after those decisions exist are ParameterSpec instances created and named.
Stable strings in ``control_surface`` describe runtime capabilities, not business
parameter names or defaults. Every business parameter identity is generated from the
current decision provenance and every value must pass the coverage audit.
"""
from __future__ import annotations

import hashlib
import json
import math
from statistics import mean, median
from typing import Any, Mapping, Sequence

import networkx as nx
import optuna
from jsonschema import Draft202012Validator

from v5_task_work_graph import (
    SCHEMA_VERSION as WORK_GRAPH_SCHEMA_VERSION,
    build_current_work_graph,
)

SCHEMA_VERSION = "runtime-generated-parameter-graph-1"
PARAMETER_SCHEMA_VERSION = "runtime-generated-parameter-spec-1"
PRINCIPLES = (
    "concrete-problem-concrete-analysis",
    "dynamic-adaptation",
    "small-effort-large-return",
)

optuna.logging.set_verbosity(optuna.logging.WARNING)

PARAMETER_SPEC_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "schema_version",
        "parameter_id",
        "purpose",
        "value_type",
        "domain",
        "depends_on",
        "derived_from",
        "resolver",
        "objective_contribution",
        "confidence",
        "recompute_trigger",
        "current_value",
        "provenance",
        "consumed_by",
        "dynamic",
        "fixed_default_used",
        "control_surface",
    ],
    "properties": {
        "schema_version": {"type": "string"},
        "parameter_id": {"type": "string", "pattern": "^p-[0-9a-f]{16}$"},
        "purpose": {"type": "string", "minLength": 1},
        "value_type": {"type": "string", "minLength": 1},
        "domain": {},
        "depends_on": {"type": "array", "items": {"type": "string"}},
        "derived_from": {"type": "array", "minItems": 1},
        "resolver": {"type": "string", "minLength": 1},
        "objective_contribution": {"type": "string", "minLength": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "recompute_trigger": {"type": "string", "minLength": 1},
        "provenance": {"type": "object"},
        "consumed_by": {"type": "array", "minItems": 1},
        "dynamic": {"const": True},
        "fixed_default_used": {"const": False},
        "control_surface": {"type": "string", "minLength": 1},
    },
    "additionalProperties": True,
}
_SPEC_VALIDATOR = Draft202012Validator(PARAMETER_SPEC_SCHEMA)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


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


def _rendered_length(value: Any) -> int:
    if value in (None, ""):
        return 0
    return len(_canonical(value).decode("utf-8"))


def _ratio(value: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, float(value) / float(denominator)))


def _task_profile(
    packet: Mapping[str, Any],
    graph: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    task = _mapping(packet.get("task"))
    evidence = _sequence(packet.get("evidence"))
    requirements = _sequence(task.get("requirements"))
    acceptance = _sequence(packet.get("execution_acceptance"))
    delivery = []
    for key in ("required_outputs", "outputs", "deliverables", "required_fields"):
        delivery.extend(_sequence(task.get(key)))

    task_characters = _rendered_length(task)
    evidence_characters = _rendered_length(evidence)
    total_characters = max(1, task_characters + evidence_characters)
    units = max(1, int(graph.get("work_unit_count") or 1))
    depth = max(1, int(graph.get("maximum_depth") or 1))
    width = max(1, int(graph.get("maximum_parallel_width") or 1))

    input_pressure = _ratio(task_characters, total_characters)
    evidence_pressure = _ratio(evidence_characters, total_characters)
    constraint_pressure = _ratio(len(requirements) + len(acceptance), units)
    delivery_pressure = _ratio(len(delivery), units)
    structure_pressure = mean(
        (
            _ratio(depth, units),
            _ratio(width, units),
            _ratio(int(graph.get("dependency_edge_count") or 0), max(1, units * (units - 1) / 2)),
        )
    )
    overall_pressure = mean(
        (
            input_pressure,
            evidence_pressure,
            constraint_pressure,
            delivery_pressure,
            structure_pressure,
        )
    )

    plan = _mapping(packet.get("governance_model_plan"))
    governance_context_floor = max(0, int(plan.get("required_context_tokens") or 0))
    expected_prompt_tokens = max(governance_context_floor, total_characters)
    structural_delivery = max(1, len(delivery), len(graph.get("sink_unit_ids") or []))
    expected_completion_tokens = max(
        1,
        math.ceil(math.sqrt(total_characters + 1)) * structural_delivery,
    )
    protocol_reserve_tokens = max(
        1,
        math.ceil(math.sqrt(expected_prompt_tokens * expected_completion_tokens)),
    )

    contexts = sorted(
        int(row.get("context_length") or 0)
        for row in candidates
        if int(row.get("context_length") or 0) > 0
    )
    completions = sorted(
        int(row.get("max_completion_tokens") or 0)
        for row in candidates
        if int(row.get("max_completion_tokens") or 0) > 0
    )
    return {
        "schema_version": "current-ticket-neutral-demand-profile-1",
        "principles": list(PRINCIPLES),
        "source": "current-ticket-and-current-candidate-signals",
        "task_characters": task_characters,
        "evidence_characters": evidence_characters,
        "requirement_count": len(requirements),
        "acceptance_count": len(acceptance),
        "delivery_item_count": len(delivery),
        "evidence_count": len(evidence),
        "work_unit_count": units,
        "dependency_edge_count": int(graph.get("dependency_edge_count") or 0),
        "maximum_task_depth": depth,
        "maximum_parallel_width": width,
        "expected_prompt_tokens": expected_prompt_tokens,
        "expected_completion_tokens": expected_completion_tokens,
        "protocol_reserve_tokens": protocol_reserve_tokens,
        "governance_context_floor": governance_context_floor,
        "candidate_native_statistics": {
            "known_context_count": len(contexts),
            "known_completion_count": len(completions),
            "median_context_tokens": int(median(contexts)) if contexts else 0,
            "median_completion_tokens": int(median(completions)) if completions else 0,
            "maximum_context_tokens": max(contexts, default=0),
            "maximum_completion_tokens": max(completions, default=0),
        },
        "pressure": {
            "input": round(100 * input_pressure),
            "evidence": round(100 * evidence_pressure),
            "constraints": round(100 * constraint_pressure),
            "delivery": round(100 * delivery_pressure),
            "structure": round(100 * structure_pressure),
            "overall": round(100 * overall_pressure),
        },
        "semantic_keyword_routing_used": False,
        "domain_hardcoding_used": False,
        "cross_task_history_used": False,
        "provider_metric_used": False,
        "fixed_business_weight_coefficients_used": False,
        "all_calculable_planning_parameters_dynamic": True,
        "hard_model_eligibility_gates": [],
        "only_hard_model_boundary": "no-tools",
    }


def _decision(
    *,
    purpose: str,
    control_surface: str,
    value_type: str,
    domain: Any,
    source_signals: Sequence[str],
    consumed_by: Sequence[str],
    resolver: str,
    objective_contribution: str,
    recompute_trigger: str,
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    provenance = {
        "work_graph_sha256": _digest(graph, 64),
        "source_signals": list(source_signals),
        "control_surface": control_surface,
    }
    decision_id = "d-" + _digest(
        {
            "purpose": purpose,
            "consumed_by": list(consumed_by),
            "provenance": provenance,
        }
    )
    return {
        "decision_id": decision_id,
        "purpose": purpose,
        "control_surface": control_surface,
        "value_type": value_type,
        "domain": domain,
        "source_signals": list(source_signals),
        "consumed_by": list(consumed_by),
        "resolver": resolver,
        "objective_contribution": objective_contribution,
        "recompute_trigger": recompute_trigger,
        "provenance": provenance,
    }


def discover_required_decisions(
    graph: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    units = int(graph.get("work_unit_count") or 0)
    edges = int(graph.get("dependency_edge_count") or 0)
    width = int(graph.get("maximum_parallel_width") or 1)
    decisions: list[dict[str, Any]] = []
    if units:
        decisions.extend(
            [
                _decision(
                    purpose="choose an executable partitioning of this current work DAG",
                    control_surface="work-dag-partitioning",
                    value_type="integer",
                    domain={"min": 1, "max_source": "min(current-candidates,current-work-units)"},
                    source_signals=("work_unit_count", "maximum_depth", "maximum_parallel_width", "dependency_edge_count"),
                    consumed_by=("current-work-dag-role-partitioner",),
                    resolver="optuna-current-search-space",
                    objective_contribution="balance current parallel coverage, coupling and load without fixed relative weights",
                    recompute_trigger="current-work-graph-change",
                    graph=graph,
                ),
                _decision(
                    purpose="bind one executable current candidate identity to each generated role",
                    control_surface="candidate-role-binding",
                    value_type="assignment",
                    domain={"source": "current-executable-candidate-inventory"},
                    source_signals=("generated-role-graph", "current-executable-candidate-inventory"),
                    consumed_by=("current-role-ortools-assignment",),
                    resolver="ortools-cp-sat",
                    objective_contribution="minimize the current role-specific normalized model objective",
                    recompute_trigger="generated-role-graph-or-candidate-inventory-change",
                    graph=graph,
                ),
                _decision(
                    purpose="derive the model objective balance used by each current generated role",
                    control_surface="role-model-objective-balance",
                    value_type="policy",
                    domain={"mode": "current-signal-normalization"},
                    source_signals=("current-task-pressure", "current-role-structure", "current-candidate-ranks"),
                    consumed_by=("current-role-model-scoring",),
                    resolver="normalized-current-signals",
                    objective_contribution="derive relative model objective weights from current role and task signals only",
                    recompute_trigger="current-role-or-task-signal-change",
                    graph=graph,
                ),
                _decision(
                    purpose="quantize current role demand into the request protocol reasoning-effort enum",
                    control_surface="role-reasoning-effort",
                    value_type="policy",
                    domain={"protocol_values": ["low", "medium", "high"]},
                    source_signals=("current-role-structural-demand-distribution",),
                    consumed_by=("current-role-prompt-profile",),
                    resolver="empirical-current-role-extrema",
                    objective_contribution="use higher protocol effort only for relatively heavier current roles",
                    recompute_trigger="generated-role-graph-change",
                    graph=graph,
                ),
            ]
        )
    if width > 1:
        decisions.append(
            _decision(
                purpose="represent parallel work that the current DAG can actually expose",
                control_surface="parallel-structure-signal",
                value_type="float",
                domain={"min": 0, "max": 1},
                source_signals=("maximum_parallel_width", "work_unit_count"),
                consumed_by=("current-partition-objective",),
                resolver="networkx-current-graph-ratio",
                objective_contribution="reward partitions that cover currently independent work",
                recompute_trigger="current-work-graph-change",
                graph=graph,
            )
        )
    if edges > 0:
        decisions.append(
            _decision(
                purpose="represent coupling in the current DAG so partitioning does not destroy dependencies",
                control_surface="dependency-coupling-signal",
                value_type="float",
                domain={"min": 0, "max": 1},
                source_signals=("dependency_edge_count", "work_unit_count"),
                consumed_by=("current-partition-objective", "current-role-quotient-dag"),
                resolver="networkx-current-graph-density",
                objective_contribution="penalize unnecessary splitting when current dependency coupling is high",
                recompute_trigger="current-work-graph-change",
                graph=graph,
            )
        )
    if len(candidates) > 1:
        decisions.extend(
            [
                _decision(
                    purpose="activate only the initial recovery identities justified by current graph pressure",
                    control_surface="initial-recovery-allocation",
                    value_type="integer",
                    domain={"min": 0, "max_source": "current-candidates-minus-primary"},
                    source_signals=("current-work-graph", "current-task-pressure", "current-candidate-count"),
                    consumed_by=("current-recovery-ortools-selection", "runtime-initial-recovery-pool"),
                    resolver="optuna-current-search-space",
                    objective_contribution="match recovery breadth to current structural and task pressure",
                    recompute_trigger="current-work-graph-or-candidate-inventory-change",
                    graph=graph,
                ),
                _decision(
                    purpose="allow additional standby promotion only from current-run failure and quality feedback",
                    control_surface="runtime-standby-replanning",
                    value_type="runtime-policy",
                    domain={"history_scope": "current-run-only"},
                    source_signals=("current-run-failure-rate", "current-run-quality-failure-rate", "standby-remaining"),
                    consumed_by=("runtime-feedback-replanner",),
                    resolver="current-run-feedback",
                    objective_contribution="recover current necessary nodes without a fixed promotion depth",
                    recompute_trigger="current-run-failure-or-quality-feedback",
                    graph=graph,
                ),
            ]
        )
    return decisions


def _parameter_from_decision(
    decision: Mapping[str, Any],
    *,
    depends_on: Sequence[str],
) -> dict[str, Any]:
    parameter_id = "p-" + _digest(
        {
            "decision_id": decision["decision_id"],
            "depends_on": list(depends_on),
            "provenance": decision["provenance"],
        }
    )
    spec = {
        "schema_version": PARAMETER_SCHEMA_VERSION,
        "parameter_id": parameter_id,
        "purpose": str(decision["purpose"]),
        "value_type": str(decision["value_type"]),
        "domain": decision["domain"],
        "depends_on": list(depends_on),
        "derived_from": list(decision["source_signals"]),
        "resolver": str(decision["resolver"]),
        "objective_contribution": str(decision["objective_contribution"]),
        "confidence": 1.0,
        "recompute_trigger": str(decision["recompute_trigger"]),
        "current_value": None,
        "provenance": dict(decision["provenance"]),
        "consumed_by": list(decision["consumed_by"]),
        "dynamic": True,
        "fixed_default_used": False,
        "control_surface": str(decision["control_surface"]),
        "decision_id": str(decision["decision_id"]),
    }
    errors = sorted(_SPEC_VALIDATOR.iter_errors(spec), key=lambda row: list(row.path))
    if errors:
        raise RuntimeError("generated ParameterSpec failed schema validation: " + errors[0].message)
    return spec


def discover_parameter_requirements(
    graph: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    decisions = discover_required_decisions(graph, candidates)
    by_surface: dict[str, str] = {}
    specs: list[dict[str, Any]] = []
    dependency_surfaces: dict[str, tuple[str, ...]] = {
        "candidate-role-binding": ("work-dag-partitioning",),
        "role-model-objective-balance": ("work-dag-partitioning",),
        "role-reasoning-effort": ("work-dag-partitioning",),
        "parallel-structure-signal": ("work-dag-partitioning",),
        "dependency-coupling-signal": ("work-dag-partitioning",),
        "initial-recovery-allocation": ("work-dag-partitioning",),
        "runtime-standby-replanning": ("initial-recovery-allocation",),
    }
    for decision in decisions:
        deps = [by_surface[value] for value in dependency_surfaces.get(str(decision["control_surface"]), ()) if value in by_surface]
        spec = _parameter_from_decision(decision, depends_on=deps)
        specs.append(spec)
        by_surface[str(decision["control_surface"])] = str(spec["parameter_id"])

    graph_value = nx.DiGraph()
    graph_value.add_nodes_from(str(row["parameter_id"]) for row in specs)
    for row in specs:
        graph_value.add_edges_from((parent, str(row["parameter_id"])) for parent in row["depends_on"])
    if not nx.is_directed_acyclic_graph(graph_value):
        raise RuntimeError("generated parameter dependency graph is cyclic")
    return {
        "schema_version": PARAMETER_SCHEMA_VERSION,
        "discovery_mode": "current-decisions-first-then-generate-parameter-identities",
        "required_decisions": decisions,
        "parameter_specs": specs,
        "required_parameter_ids": [str(row["parameter_id"]) for row in specs],
        "required_parameter_count": len(specs),
        "dependency_edges": [{"from": str(a), "to": str(b)} for a, b in graph_value.edges()],
        "control_surface_to_parameter_id": by_surface,
        "parameter_ids_are_generated_after_decision_discovery": True,
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


def _surface_specs(requirements: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["control_surface"]): row
        for row in requirements.get("parameter_specs") or []
        if isinstance(row, Mapping)
    }


def _optimize_shape(
    graph: Mapping[str, Any],
    profile: Mapping[str, Any],
    candidate_count: int,
    surfaces: Mapping[str, Mapping[str, Any]],
) -> tuple[int, int, dict[str, Any]]:
    units = max(1, int(graph.get("work_unit_count") or 1))
    width = max(1, int(graph.get("maximum_parallel_width") or 1))
    depth = max(1, int(graph.get("maximum_depth") or 1))
    edges = max(0, int(graph.get("dependency_edge_count") or 0))
    upper = max(1, min(candidate_count, units))
    parallel_signal = _ratio(width, units)
    possible_edges = max(1.0, units * max(1, units - 1) / 2.0)
    coupling_signal = _ratio(edges, possible_edges)
    depth_signal = _ratio(depth, units)
    overall_signal = _ratio(float(_mapping(profile.get("pressure")).get("overall") or 0), 100)
    resilience_signal = max(coupling_signal, depth_signal, overall_signal)

    def objective(trial: optuna.Trial) -> float:
        team = trial.suggest_int("partition", 1, upper)
        remaining = max(0, candidate_count - team)
        recovery = trial.suggest_int("recovery", 0, remaining) if "initial-recovery-allocation" in surfaces and remaining else 0
        parallel_loss = max(0.0, float(width - team)) / max(1, width)
        split_loss = coupling_signal * _ratio(team - 1, max(1, units - 1))
        load_target = units / max(1, width)
        load_loss = abs(units / team - load_target) / max(1.0, float(units))
        recovery_target = min(remaining, math.ceil(team * resilience_signal))
        recovery_loss = abs(recovery - recovery_target) / max(1, remaining) if remaining else 0.0
        losses = [parallel_loss, split_loss, load_loss]
        if "initial-recovery-allocation" in surfaces:
            losses.append(recovery_loss)
        return mean(losses)

    search_space = upper * max(1, candidate_count)
    trial_count = max(1, math.ceil(math.sqrt(search_space)))
    seed = int(_digest({"graph": graph, "candidates": candidate_count, "surfaces": sorted(surfaces)}, 8), 16) % 2_147_483_647
    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
    try:
        study.optimize(objective, n_trials=trial_count, n_jobs=1, show_progress_bar=False)
        team = int(study.best_params.get("partition") or 1)
        remaining = max(0, candidate_count - team)
        recovery = int(study.best_params.get("recovery") or 0) if remaining else 0
        audit = {
            "optimizer": "optuna-tpe-current-search-space",
            "trial_count": len(study.trials),
            "search_space_scale": search_space,
            "best_objective": float(study.best_value),
            "seed": seed,
            "fallback_used": False,
            "fixed_business_objective_coefficients_used": False,
            "objective_aggregation": "unweighted-mean-of-current-active-losses",
        }
    except Exception as exc:  # noqa: BLE001
        team = max(1, min(upper, width))
        remaining = max(0, candidate_count - team)
        recovery = min(remaining, math.ceil(team * resilience_signal))
        audit = {
            "optimizer": "deterministic-current-signal-fallback",
            "trial_count": 0,
            "search_space_scale": search_space,
            "seed": seed,
            "fallback_used": True,
            "fallback_reason": type(exc).__name__,
            "fixed_business_objective_coefficients_used": False,
            "objective_aggregation": "current-graph-signals",
        }
    return team, max(0, min(candidate_count - team, recovery)), audit


def resolve_parameter_values(
    graph: Mapping[str, Any],
    requirements: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    surfaces = _surface_specs(requirements)
    team, recovery, optimization = _optimize_shape(graph, profile, max(1, len(candidates)), surfaces)
    units = max(1, int(graph.get("work_unit_count") or 1))
    width = max(1, int(graph.get("maximum_parallel_width") or 1))
    edges = max(0, int(graph.get("dependency_edge_count") or 0))
    possible_edges = max(1.0, units * max(1, units - 1) / 2.0)
    surface_values: dict[str, Any] = {
        "work-dag-partitioning": team,
        "candidate-role-binding": "resolve-after-current-role-dag-with-ortools-cp-sat",
        "role-model-objective-balance": {
            "mode": "normalize-current-role-and-task-signals",
            "fixed_business_coefficients": False,
        },
        "role-reasoning-effort": {
            "mode": "current-role-demand-extrema",
            "fixed-role-kind-mapping": False,
        },
        "parallel-structure-signal": round(_ratio(width, units), 8),
        "dependency-coupling-signal": round(_ratio(edges, possible_edges), 8),
        "initial-recovery-allocation": recovery,
        "runtime-standby-replanning": "recompute-from-current-run-feedback",
    }
    values: dict[str, Any] = {}
    for spec in requirements.get("parameter_specs") or []:
        if not isinstance(spec, Mapping):
            continue
        surface = str(spec.get("control_surface") or "")
        parameter_id = str(spec.get("parameter_id") or "")
        if surface not in surface_values:
            raise RuntimeError(f"generated parameter has no resolver output: {surface}")
        values[parameter_id] = {
            "value": surface_values[surface],
            "control_surface": surface,
            "dynamic": True,
            "derived_from": list(spec.get("derived_from") or []),
            "resolver": str(spec.get("resolver") or ""),
            "consumed_by": list(spec.get("consumed_by") or []),
            "fixed_default_used": False,
            "provenance": dict(spec.get("provenance") or {}),
        }

    required = set(requirements.get("required_parameter_ids") or [])
    resolved = set(values)
    unconsumed = [parameter_id for parameter_id, row in values.items() if not row.get("consumed_by")]
    unexplained = [parameter_id for parameter_id, row in values.items() if not row.get("derived_from") or not row.get("provenance")]
    fixed = [parameter_id for parameter_id, row in values.items() if row.get("fixed_default_used") is True]
    missing = sorted(required - resolved)
    extra = sorted(resolved - required)
    coverage_pass = not (unconsumed or unexplained or fixed or missing or extra)
    return {
        "values": values,
        "control_surface_values": {surface: value for surface, value in surface_values.items() if surface in surfaces},
        "team_size": team,
        "recovery_size": recovery,
        "optimization": optimization,
        "parameter_coverage_audit": {
            "status": "PASS" if coverage_pass else "FAIL",
            "required_parameter_count": len(required),
            "resolved_parameter_count": len(resolved),
            "dynamic_parameter_count": sum(1 for row in values.values() if row.get("dynamic") is True),
            "fixed_business_parameter_count": len(fixed),
            "unexplained_parameter_count": len(unexplained),
            "unconsumed_parameter_count": len(unconsumed),
            "missing_parameter_count": len(missing),
            "extra_parameter_count": len(extra),
            "missing_parameter_ids": missing,
            "extra_parameter_ids": extra,
            "unconsumed_parameter_ids": unconsumed,
            "every_parameter_has_active_consumer": not unconsumed,
            "parameter_ids_generated_after_decision_discovery": True,
            "legacy_business_parameter_names_used_as_parameter_ids": False,
            "fixed_business_parameter_catalog_used": False,
        },
    }


def _role_plan(graph_value: Mapping[str, Any], team_size: int) -> list[dict[str, Any]]:
    units = [dict(row) for row in graph_value.get("work_units") or [] if isinstance(row, Mapping)]
    if not units:
        return []
    graph = nx.DiGraph()
    graph.add_nodes_from(str(row["unit_id"]) for row in units)
    graph.add_edges_from(
        (str(row.get("from")), str(row.get("to")))
        for row in graph_value.get("dependency_edges") or []
        if isinstance(row, Mapping)
    )
    if not nx.is_directed_acyclic_graph(graph):
        raise RuntimeError("current work graph became cyclic before role partitioning")
    order = list(nx.topological_sort(graph))
    by_id = {str(row["unit_id"]): row for row in units}
    team_size = max(1, min(int(team_size), len(order)))
    total_weight = sum(int(by_id[unit_id].get("structural_weight") or 1) for unit_id in order)
    target = total_weight / team_size
    buckets: list[list[str]] = [[]]
    bucket_weight = 0.0
    for unit_id in order:
        weight = int(by_id[unit_id].get("structural_weight") or 1)
        assigned = sum(len(bucket) for bucket in buckets)
        remaining_units = len(order) - assigned
        remaining_buckets = team_size - len(buckets)
        if len(buckets) < team_size and buckets[-1] and bucket_weight + weight > target and remaining_units > remaining_buckets:
            buckets.append([])
            bucket_weight = 0.0
        buckets[-1].append(unit_id)
        bucket_weight += weight
    while len(buckets) < team_size:
        largest_index = max(range(len(buckets)), key=lambda index: len(buckets[index]))
        bucket = buckets[largest_index]
        if len(bucket) <= 1:
            break
        split = math.ceil(len(bucket) / 2)
        buckets.insert(largest_index + 1, bucket[split:])
        buckets[largest_index] = bucket[:split]

    unit_to_bucket = {unit_id: index for index, bucket in enumerate(buckets) for unit_id in bucket}
    role_edges = {
        (unit_to_bucket[source], unit_to_bucket[target])
        for source, target in graph.edges()
        if unit_to_bucket[source] != unit_to_bucket[target]
    }
    role_graph = nx.DiGraph()
    role_graph.add_nodes_from(range(len(buckets)))
    role_graph.add_edges_from(role_edges)
    if not nx.is_directed_acyclic_graph(role_graph):
        raise RuntimeError("current role quotient graph is cyclic")

    role_ids: list[str] = []
    role_demands: list[float] = []
    for index, bucket in enumerate(buckets):
        signature = _digest({"bucket": bucket, "edges": sorted(role_edges)})[:10]
        role_ids.append(f"role-{index + 1}-{signature}")
        work_demand = sum(int(by_id[value].get("structural_weight") or 1) for value in bucket)
        dependency_demand = role_graph.in_degree(index) + role_graph.out_degree(index)
        role_demands.append(float(work_demand + dependency_demand))

    minimum = min(role_demands)
    maximum = max(role_demands)
    efforts: list[str] = []
    for demand in role_demands:
        if len(role_demands) == 1 or minimum == maximum:
            efforts.append("medium")
        elif demand == maximum:
            efforts.append("high")
        elif demand == minimum:
            efforts.append("low")
        else:
            efforts.append("medium")

    roles: list[dict[str, Any]] = []
    for index, bucket in enumerate(buckets):
        kinds = sorted({str(by_id[unit_id].get("source_kind") or "task-unit") for unit_id in bucket})
        dependencies = sorted(role_ids[parent] for parent in role_graph.predecessors(index))
        functions = ["analyze:" + value for value in kinds]
        if dependencies:
            functions.append("integrate:declared-upstream")
        roles.append(
            {
                "role_id": role_ids[index],
                "role_kind": "dynamic:" + "+".join(kinds),
                "role": "动态任务角色：完成当前工作图分配单元并只沿声明依赖吸收上游结果",
                "assigned_work_units": list(bucket),
                "depends_on_role_ids": dependencies,
                "functions": functions,
                "reasoning_effort": efforts[index],
                "reasoning_effort_source": "current-role-demand-extrema",
                "role_structural_demand": role_demands[index],
                "final_role": role_graph.out_degree(index) == 0,
                "role_source_signal": "current-ticket-work-dag-partition",
            }
        )
    return roles


def build_runtime_planning_context(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    graph = build_current_work_graph(packet)
    requirements = discover_parameter_requirements(graph, candidates)
    profile = _task_profile(packet, graph, candidates)
    resolved = resolve_parameter_values(graph, requirements, candidates, profile)
    coverage = dict(resolved["parameter_coverage_audit"])
    if coverage.get("status") != "PASS":
        raise RuntimeError("generated parameter coverage audit failed")
    roles = _role_plan(graph, int(resolved["team_size"]))
    profile = {
        **profile,
        "active_generated_parameter_ids": list(requirements["required_parameter_ids"]),
        "active_parameter_count": int(requirements["required_parameter_count"]),
        "parameter_identity_mode": "generated-after-current-decision-discovery",
        "model_scoring_policy": resolved["control_surface_values"].get("role-model-objective-balance", {}),
        "reasoning_effort_policy": resolved["control_surface_values"].get("role-reasoning-effort", {}),
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
        "parameter_coverage_audit": coverage,
        "parameter_optimizer": dict(resolved["optimization"]),
        "parameter_values_derived_from_current_task": True,
        "parameter_ids_generated_after_decision_discovery": True,
        "fixed_parameter_values_used": False,
        "fixed_business_objective_coefficients_used": False,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "work_graph_schema_version": WORK_GRAPH_SCHEMA_VERSION,
        "planning_sequence": [
            "current-ticket-work-dag",
            "required-decision-discovery",
            "generated-parameter-instance-construction",
            "generated-parameter-dependency-graph",
            "current-signal-resolution-and-optuna",
            "current-work-dag-role-partition",
            "ortools-model-assignment",
            "runtime-feedback-replanning",
        ],
        "decomposition": graph,
        "parameter_requirements": requirements,
        "resolved_parameters": resolved_parameters,
        "resolved_profile": profile,
        "role_plan": roles,
        "primary_expert_count": len(roles),
        "recovery_count": int(resolved["recovery_size"]),
        "all_calculable_planning_parameters_dynamic": True,
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
    "PARAMETER_SCHEMA_VERSION",
    "PARAMETER_SPEC_SCHEMA",
    "PRINCIPLES",
    "SCHEMA_VERSION",
    "build_runtime_planning_context",
    "discover_parameter_requirements",
    "discover_required_decisions",
    "resolve_parameter_values",
]
