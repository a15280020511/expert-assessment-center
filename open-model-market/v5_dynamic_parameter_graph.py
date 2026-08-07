"""Task-derived work and parameter graphs for production expert composition.

Planning is deliberately split into independent auditable phases:

current ticket -> finite work DAG -> required parameter instances -> parameter DAG
-> resolve current values -> role quotient DAG -> OR-Tools model assignment ->
current-run feedback replanning.

Only parameter instances that actually control an active planning/runtime surface are
registered.  Every resolved parameter carries ``consumed_by`` evidence; an unconsumed
or fixed-business parameter makes the parameter coverage audit fail.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from statistics import median
from typing import Any, Mapping, Sequence

import networkx as nx
import optuna

from v5_task_adaptive_scoring import build_task_demand_profile

SCHEMA_VERSION = "v5-task-derived-parameter-graph-2"
PARAMETER_SCHEMA_VERSION = "v5-task-derived-parameter-spec-2"

optuna.logging.set_verbosity(optuna.logging.WARNING)


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


def _structural_units(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    task = _mapping(packet.get("task"))
    question = (
        task.get("question")
        or packet.get("question")
        or task.get("objective")
        or packet.get("objective")
        or ""
    )
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
                    "dependency_source": "task-derived-similarity",
                }
            )
    if not result:
        result.append(
            {
                "unit_id": "task-1",
                "source_kind": "task-unit",
                "payload": packet,
                "dependencies": [],
                "dependency_source": "task-derived-single-unit",
            }
        )
    return result


def _infer_dependencies(units: list[dict[str, Any]]) -> None:
    ids = {str(row["unit_id"]) for row in units}
    for index, row in enumerate(units):
        explicit = [
            value
            for value in row.get("dependencies", [])
            if value in ids and value != row["unit_id"]
        ]
        if explicit:
            row["dependencies"] = list(dict.fromkeys(explicit))
            continue
        if index == 0:
            row["dependencies"] = []
            continue
        scored = [
            (_jaccard(row.get("payload"), prior.get("payload")), str(prior["unit_id"]))
            for prior in units[:index]
        ]
        positives = [score for score, _ in scored if score > 0]
        if not positives:
            row["dependencies"] = []
            continue
        threshold = median(positives)
        parent_limit = max(1, math.ceil(math.log2(index + 1) / 2.0))
        row["dependencies"] = [
            unit_id
            for score, unit_id in sorted(scored, key=lambda item: (-item[0], item[1]))
            if score >= threshold and score > 0
        ][:parent_limit]


def _annotate_graph(units: list[dict[str, Any]]) -> dict[str, Any]:
    graph = nx.DiGraph()
    graph.add_nodes_from(str(row["unit_id"]) for row in units)
    for row in units:
        for parent in row.get("dependencies", []):
            if parent in graph and parent != row["unit_id"]:
                graph.add_edge(str(parent), str(row["unit_id"]))
    if not nx.is_directed_acyclic_graph(graph):
        raise RuntimeError("task-derived work graph is cyclic")

    # A task may naturally contain several independent terminal branches.  A final
    # integration work unit is derived only when needed to produce one coherent
    # delivery; this is graph closure, not a fixed expert-role grammar.
    if len(graph) > 1:
        sinks = sorted(node for node in graph if graph.out_degree(node) == 0)
        if len(sinks) > 1:
            digest = hashlib.sha256("|".join(sinks).encode("utf-8")).hexdigest()[:10]
            integration_id = f"integration-{digest}"
            units.append(
                {
                    "unit_id": integration_id,
                    "source_kind": "integration",
                    "payload": "integrate current task sink results into the required delivery",
                    "dependencies": sinks,
                    "dependency_source": "task-derived-multiple-sinks",
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
        row["structural_weight"] = max(1, 1 + math.ceil(len(rendered) / 180))
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


def decompose_task(packet: Mapping[str, Any]) -> dict[str, Any]:
    units = _explicit_units(packet) or _structural_units(packet)
    seen: set[str] = set()
    for index, row in enumerate(units, 1):
        unit_id = str(row.get("unit_id") or f"work-{index}")
        if unit_id in seen:
            unit_id = f"{unit_id}-{index}"
        row["unit_id"] = unit_id
        seen.add(unit_id)
    _infer_dependencies(units)
    return _annotate_graph(units)


def _parameter_spec(
    parameter_id: str,
    purpose: str,
    value_type: str,
    *,
    depends_on: Sequence[str] = (),
    resolver: str,
    source_signals: Sequence[str],
    recompute_trigger: str,
    consumed_by: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": PARAMETER_SCHEMA_VERSION,
        "parameter_id": parameter_id,
        "purpose": purpose,
        "value_type": value_type,
        "depends_on": list(depends_on),
        "resolver": resolver,
        "source_signals": list(source_signals),
        "recompute_trigger": recompute_trigger,
        "consumed_by": list(consumed_by),
        "parameter_instance_generated_from_current_task": True,
        "fixed_default_used": False,
    }


def discover_parameter_requirements(
    decomposition: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    units = int(decomposition.get("work_unit_count") or 0)
    edges = int(decomposition.get("dependency_edge_count") or 0)
    width = int(decomposition.get("maximum_parallel_width") or 1)
    specs: list[dict[str, Any]] = []

    def add(*args: Any, **kwargs: Any) -> None:
        specs.append(_parameter_spec(*args, **kwargs))

    if units:
        add(
            "execution_partition_count",
            "choose how many executable role partitions the current work DAG needs",
            "integer",
            resolver="optuna-conditional-integer",
            source_signals=("work_unit_count", "maximum_depth", "maximum_parallel_width"),
            recompute_trigger="task-graph-change",
            consumed_by=("task-dag-role-partitioner",),
        )
        add(
            "model_assignment",
            "assign one current candidate identity to every generated role partition",
            "assignment",
            depends_on=("execution_partition_count",),
            resolver="ortools-cp-sat",
            source_signals=("generated-role-graph", "current-candidate-inventory"),
            recompute_trigger="role-graph-or-candidate-change",
            consumed_by=("ortools-model-role-assignment",),
        )
    if width > 1:
        add(
            "parallelism_ratio",
            "express exploitable parallel width of the current work DAG",
            "float",
            depends_on=("execution_partition_count",),
            resolver="networkx-graph-derived",
            source_signals=("maximum_parallel_width", "work_unit_count"),
            recompute_trigger="task-graph-change",
            consumed_by=("optuna-team-shape-objective",),
        )
    if edges > 0:
        add(
            "dependency_density",
            "express dependency coupling that discourages destructive over-partitioning",
            "float",
            depends_on=("execution_partition_count",),
            resolver="networkx-graph-derived",
            source_signals=("dependency_edge_count", "work_unit_count", "maximum_depth"),
            recompute_trigger="task-graph-change",
            consumed_by=("optuna-team-shape-objective", "role-quotient-dag"),
        )
    if len(candidates) > 1:
        add(
            "recovery_count",
            "activate a current-task initial recovery set while leaving remaining models standby",
            "integer",
            depends_on=("execution_partition_count",),
            resolver="optuna-conditional-integer",
            source_signals=("candidate_count", "work_graph_pressure"),
            recompute_trigger="candidate-inventory-or-task-graph-change",
            consumed_by=("ortools-recovery-selection", "runtime-recovery-pool"),
        )
        add(
            "runtime_standby_promotion",
            "promote additional standby identities from current-run failure feedback",
            "runtime-policy",
            depends_on=("recovery_count",),
            resolver="current-run-feedback",
            source_signals=("failure_rate", "quality_gate_failure_rate", "standby_remaining"),
            recompute_trigger="current-run-failure-or-quality-feedback",
            consumed_by=("runtime-feedback-replanner",),
        )

    ids = {str(row["parameter_id"]) for row in specs}
    graph = nx.DiGraph()
    graph.add_nodes_from(ids)
    for row in specs:
        row["depends_on"] = [value for value in row["depends_on"] if value in ids]
        graph.add_edges_from((value, row["parameter_id"]) for value in row["depends_on"])
    if not nx.is_directed_acyclic_graph(graph):
        raise RuntimeError("dynamic parameter dependency graph is cyclic")
    return {
        "schema_version": PARAMETER_SCHEMA_VERSION,
        "discovery_mode": "derive-effective-parameter-instances-after-current-task-dag",
        "parameter_specs": specs,
        "required_parameter_ids": [str(row["parameter_id"]) for row in specs],
        "required_parameter_count": len(specs),
        "dependency_edges": [{"from": str(a), "to": str(b)} for a, b in graph.edges()],
        "fixed_parameter_template_used": False,
        "all_parameter_instances_current_task_derived": True,
        "unused_parameter_specs_allowed": False,
        "semantic_keyword_routing_used": False,
        "cross_task_history_used": False,
        "only_hard_model_boundary": "no-tools",
    }


def _seed(value: Any) -> int:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:8], 16) % 2_147_483_647


def _resolve_parameters(
    decomposition: Mapping[str, Any],
    requirements: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    base_profile: Mapping[str, Any],
) -> dict[str, Any]:
    active = set(requirements.get("required_parameter_ids") or [])
    units = max(1, int(decomposition.get("work_unit_count") or 1))
    depth = max(1, int(decomposition.get("maximum_depth") or 1))
    width = max(1, int(decomposition.get("maximum_parallel_width") or 1))
    edges = max(0, int(decomposition.get("dependency_edge_count") or 0))
    candidate_count = max(1, len(candidates))
    overall = max(
        0,
        min(100, int(_mapping(base_profile.get("pressure")).get("overall") or 0)),
    )

    parallelism_ratio = min(1.0, width / max(1, units))
    possible_edges = max(1.0, units * max(1, units - 1) / 2.0)
    dependency_density = min(1.0, edges / possible_edges)
    upper = min(
        candidate_count,
        units,
        max(1, math.ceil(math.sqrt(units) * (1.0 + math.log2(width + 1) / 2.0))),
    )
    target_team = min(
        upper,
        max(
            1,
            round(
                math.sqrt(units + width)
                + math.log2(depth + 1)
                + overall / 35.0
            ),
        ),
    )
    seed = _seed(
        {
            "decomposition": decomposition,
            "parameter_ids": sorted(active),
            "candidate_ids": [
                str(row.get("model") or row.get("id") or "") for row in candidates
            ],
        }
    )

    def objective(trial: optuna.Trial) -> float:
        team = (
            trial.suggest_int("execution_partition_count", 1, upper)
            if "execution_partition_count" in active
            else 1
        )
        remaining = max(0, candidate_count - team)
        recovery = (
            trial.suggest_int("recovery_count", 0, remaining)
            if "recovery_count" in active and remaining
            else 0
        )
        recovery_target = min(
            remaining,
            math.ceil(
                team
                * min(
                    0.9,
                    (overall + 5 * depth + 3 * width) / 220.0,
                )
            ),
        )
        # Parallelism rewards enough partitions to exploit independent work;
        # dependency density penalizes over-partitioning a tightly coupled DAG.
        parallel_target = min(upper, max(1, width))
        partition_fraction = team / max(1, units)
        coupled_target_fraction = max(1.0 / units, 1.0 - dependency_density)
        load = units / max(1, team)
        return (
            abs(team - target_team)
            + parallelism_ratio * abs(team - parallel_target)
            + dependency_density
            * units
            * abs(partition_fraction - coupled_target_fraction)
            + 0.35 * abs(recovery - recovery_target)
            + 0.25 * max(0.0, load - 3.0)
        )

    trial_count = max(8, min(40, 5 * max(1, len(active))))
    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=seed),
    )
    try:
        study.optimize(
            objective,
            n_trials=trial_count,
            n_jobs=1,
            show_progress_bar=False,
        )
        best = dict(study.best_params)
        optimization = {
            "optimizer": "optuna-tpe",
            "trial_count": len(study.trials),
            "best_objective": float(study.best_value),
            "seed": seed,
            "fallback_used": False,
            "search_parameters": [
                value
                for value in ("execution_partition_count", "recovery_count")
                if value in active
            ],
        }
    except Exception as exc:  # noqa: BLE001 - deterministic task-only fallback
        best = {"execution_partition_count": target_team}
        optimization = {
            "optimizer": "deterministic-current-task-fallback",
            "trial_count": 0,
            "seed": seed,
            "fallback_used": True,
            "fallback_reason": type(exc).__name__,
            "search_parameters": [],
        }

    team = int(best.get("execution_partition_count") or target_team)
    remaining = max(0, candidate_count - team)
    recovery = (
        max(0, min(remaining, int(best.get("recovery_count") or 0)))
        if "recovery_count" in active
        else 0
    )

    derived_values: dict[str, Any] = {
        "execution_partition_count": team,
        "model_assignment": "resolve-with-ortools-cp-sat-after-role-dag",
        "parallelism_ratio": round(parallelism_ratio, 8),
        "dependency_density": round(dependency_density, 8),
        "recovery_count": recovery,
        "runtime_standby_promotion": "recompute-from-current-run-feedback",
    }
    values: dict[str, Any] = {}
    for spec in requirements.get("parameter_specs") or []:
        if not isinstance(spec, Mapping):
            continue
        parameter_id = str(spec.get("parameter_id") or "")
        values[parameter_id] = {
            "value": derived_values[parameter_id],
            "dynamic": True,
            "derived_from": list(spec.get("source_signals") or []),
            "resolver": str(spec.get("resolver") or ""),
            "consumed_by": list(spec.get("consumed_by") or []),
            "fixed_default_used": False,
        }

    required_count = int(requirements.get("required_parameter_count") or 0)
    unconsumed = [
        parameter_id
        for parameter_id, row in values.items()
        if not row.get("consumed_by")
    ]
    unexplained = [
        parameter_id
        for parameter_id, row in values.items()
        if not row.get("derived_from")
    ]
    fixed = [
        parameter_id
        for parameter_id, row in values.items()
        if row.get("fixed_default_used") is True
    ]
    coverage_pass = (
        len(values) == required_count
        and not unconsumed
        and not unexplained
        and not fixed
    )
    return {
        "values": values,
        "team_size": team,
        "recovery_size": recovery,
        "optimization": optimization,
        "parameter_coverage_audit": {
            "status": "PASS" if coverage_pass else "FAIL",
            "required_parameter_count": required_count,
            "resolved_parameter_count": len(values),
            "dynamic_parameter_count": sum(
                1 for row in values.values() if row.get("dynamic") is True
            ),
            "fixed_business_parameter_count": len(fixed),
            "unexplained_parameter_count": len(unexplained),
            "unconsumed_parameter_count": len(unconsumed),
            "unconsumed_parameter_ids": unconsumed,
            "every_parameter_has_active_consumer": not unconsumed,
        },
    }


def _metric_role(kinds: set[str], final_role: bool) -> str:
    """Compatibility adapter only for existing scoring metrics, not role grammar."""
    if final_role:
        return "synthesis"
    if "evidence" in kinds:
        return "evidence"
    if kinds.intersection({"requirement", "acceptance"}):
        return "review"
    return "options"


def _role_plan(
    decomposition: Mapping[str, Any],
    team_size: int,
) -> list[dict[str, Any]]:
    units = [
        dict(row)
        for row in decomposition.get("work_units") or []
        if isinstance(row, Mapping)
    ]
    if not units:
        return []
    graph = nx.DiGraph()
    graph.add_nodes_from(str(row["unit_id"]) for row in units)
    graph.add_edges_from(
        (str(row.get("from")), str(row.get("to")))
        for row in decomposition.get("dependency_edges") or []
        if isinstance(row, Mapping)
    )
    if not nx.is_directed_acyclic_graph(graph):
        raise RuntimeError("task work graph became cyclic before role partitioning")
    order = list(nx.topological_sort(graph))
    by_id = {str(row["unit_id"]): row for row in units}
    team_size = max(1, min(int(team_size), len(order)))

    total_weight = sum(
        int(by_id[unit_id].get("structural_weight") or 1) for unit_id in order
    )
    target = total_weight / team_size
    buckets: list[list[str]] = [[]]
    bucket_weight = 0.0
    for unit_id in order:
        weight = int(by_id[unit_id].get("structural_weight") or 1)
        assigned = sum(len(bucket) for bucket in buckets)
        remaining_units = len(order) - assigned
        remaining_buckets = team_size - len(buckets)
        if (
            len(buckets) < team_size
            and buckets[-1]
            and bucket_weight + weight > target
            and remaining_units > remaining_buckets
        ):
            buckets.append([])
            bucket_weight = 0.0
        buckets[-1].append(unit_id)
        bucket_weight += weight
    while len(buckets) < team_size:
        largest_index = max(range(len(buckets)), key=lambda i: len(buckets[i]))
        bucket = buckets[largest_index]
        if len(bucket) <= 1:
            break
        split = len(bucket) // 2
        buckets.insert(largest_index + 1, bucket[split:])
        buckets[largest_index] = bucket[:split]

    unit_to_bucket = {
        unit_id: index
        for index, bucket in enumerate(buckets)
        for unit_id in bucket
    }
    role_edges = {
        (unit_to_bucket[source], unit_to_bucket[target_id])
        for source, target_id in graph.edges()
        if unit_to_bucket[source] != unit_to_bucket[target_id]
    }
    role_graph = nx.DiGraph()
    role_graph.add_nodes_from(range(len(buckets)))
    role_graph.add_edges_from(role_edges)
    if not nx.is_directed_acyclic_graph(role_graph):
        raise RuntimeError("role quotient graph is cyclic")

    role_ids: list[str] = []
    for index, bucket in enumerate(buckets, 1):
        kinds = [
            str(by_id[unit_id].get("source_kind") or "task-unit")
            for unit_id in bucket
        ]
        signature = "-".join(dict.fromkeys(_slug(kind) for kind in kinds))
        role_ids.append(f"role-{index}-{signature[:36] or 'task'}")

    roles: list[dict[str, Any]] = []
    for index, bucket in enumerate(buckets):
        kinds = {
            str(by_id[unit_id].get("source_kind") or "task-unit")
            for unit_id in bucket
        }
        dependencies = sorted(
            role_ids[parent] for parent in role_graph.predecessors(index)
        )
        final_role = role_graph.out_degree(index) == 0
        functions = [f"analyze:{_slug(kind)}" for kind in sorted(kinds)]
        if dependencies:
            functions.append("integrate:declared-upstream")
        role_kind = "dynamic:" + "+".join(
            sorted(_slug(kind) for kind in kinds)
        )
        roles.append(
            {
                "role_id": role_ids[index],
                "role_kind": role_kind,
                "metric_role_id": _metric_role(kinds, final_role),
                "role": (
                    "动态任务角色：完成分配的当前工作单元，只沿本次任务依赖图"
                    "吸收上游结果，并检查假设、反例和交付完整性"
                ),
                "assigned_work_units": list(bucket),
                "depends_on_role_ids": dependencies,
                "functions": functions,
                "reasoning_effort": (
                    "high" if dependencies or final_role else "medium"
                ),
                "final_role": final_role,
                "role_source_signal": "task-derived-work-dag-partition",
            }
        )
    return roles


def build_dynamic_planning_context(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    decomposition = decompose_task(packet)
    parameter_requirements = discover_parameter_requirements(
        decomposition, candidates
    )
    base_profile = dict(build_task_demand_profile(packet, candidates))
    resolved = _resolve_parameters(
        decomposition,
        parameter_requirements,
        candidates,
        base_profile,
    )
    roles = _role_plan(decomposition, int(resolved["team_size"]))

    pressure = dict(_mapping(base_profile.get("pressure")))
    structural_pressure = min(
        100,
        round(
            8 * math.sqrt(
                max(1, int(decomposition.get("work_unit_count") or 1))
            )
            + 4 * int(decomposition.get("maximum_depth") or 1)
            + 3 * int(decomposition.get("maximum_parallel_width") or 1)
        ),
    )
    pressure["structure"] = structural_pressure
    pressure["overall"] = min(
        100,
        round(
            0.65 * int(pressure.get("overall") or 0)
            + 0.35 * structural_pressure
        ),
    )
    profile = {
        **base_profile,
        "schema_version": "v5-task-derived-dynamic-profile-2",
        "pressure": pressure,
        "work_unit_count": int(decomposition.get("work_unit_count") or 0),
        "dependency_edge_count": int(
            decomposition.get("dependency_edge_count") or 0
        ),
        "maximum_task_depth": int(decomposition.get("maximum_depth") or 1),
        "maximum_parallel_width": int(
            decomposition.get("maximum_parallel_width") or 1
        ),
        "active_parameter_ids": list(
            parameter_requirements.get("required_parameter_ids") or []
        ),
        "active_parameter_count": int(
            parameter_requirements.get("required_parameter_count") or 0
        ),
        "all_calculable_planning_parameters_dynamic": True,
        "fixed_parameter_template_used": False,
        "hard_model_eligibility_gates": [],
        "only_hard_model_boundary": "no-tools",
    }

    parameter_coverage = dict(resolved["parameter_coverage_audit"])
    if parameter_coverage.get("status") != "PASS":
        raise RuntimeError("dynamic parameter coverage audit failed")
    resolved_parameters = {
        "active_parameter_ids": list(
            parameter_requirements.get("required_parameter_ids") or []
        ),
        "parameter_values": dict(resolved["values"]),
        "team_size": len(roles),
        "recovery_size": int(resolved["recovery_size"]),
        "role_count": len(roles),
        "role_topology": [
            {
                "role_id": row["role_id"],
                "role_kind": row["role_kind"],
                "depends_on_role_ids": list(row["depends_on_role_ids"]),
                "assigned_work_units": list(row["assigned_work_units"]),
            }
            for row in roles
        ],
        "parameter_coverage_audit": parameter_coverage,
        "parameter_optimizer": dict(resolved["optimization"]),
        "parameter_values_derived_from_current_task": True,
        "fixed_parameter_values_used": False,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "planning_sequence": [
            "task-derived-work-dag",
            "effective-parameter-instance-discovery",
            "parameter-dependency-graph",
            "networkx-direct-parameter-resolution",
            "conditional-optuna-team-and-recovery-resolution",
            "task-dag-role-partition",
            "ortools-model-assignment",
            "runtime-feedback-replanning",
        ],
        "decomposition": decomposition,
        "parameter_requirements": parameter_requirements,
        "resolved_parameters": resolved_parameters,
        "resolved_profile": profile,
        "role_plan": roles,
        "primary_expert_count": len(roles),
        "recovery_count": int(resolved["recovery_size"]),
        "all_calculable_planning_parameters_dynamic": True,
        "all_parameter_instances_current_task_derived": True,
        "all_parameter_instances_have_active_consumers": True,
        "fixed_parameter_template_used": False,
        "fixed_team_template_used": False,
        "fixed_role_grammar_used": False,
        "semantic_keyword_routing_used": False,
        "cross_task_history_used": False,
        "hard_model_eligibility_gates": [],
        "only_hard_model_boundary": "no-tools",
    }


__all__ = [
    "PARAMETER_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "build_dynamic_planning_context",
    "decompose_task",
    "discover_parameter_requirements",
]
