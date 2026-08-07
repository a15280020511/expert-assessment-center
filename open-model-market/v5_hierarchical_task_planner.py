"""Hierarchical current-task planner for dynamic expert composition.

Planning is intentionally staged:

1. decompose the current task into an auditable finite work graph;
2. discover which planning parameters this task actually needs;
3. resolve values for only those active parameters from the current task and
   current governance candidate inventory;
4. derive team shape and role topology for the downstream OR-Tools assignment.

No semantic/domain keyword routing, Provider gating, cross-task history, fixed
team template, or fixed Top-N model rule participates.  The only hard model
execution boundary remains no-tools; task/plan integrity and finite DAG rules
are structural invariants rather than model-business admission gates.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from v5_task_adaptive_scoring import build_task_demand_profile

SCHEMA_VERSION = "v5-hierarchical-task-parameter-planner-1"
PARAMETER_SCHEMA_VERSION = "v5-dynamic-parameter-requirements-1"


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
    if value is None:
        return ""
    return str(value).strip()


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def _deliverables(task: Mapping[str, Any]) -> list[Any]:
    result: list[Any] = []
    for key in ("required_outputs", "outputs", "deliverables", "required_fields"):
        result.extend(_sequence(task.get(key)))
    return result


def _unit(
    unit_id: str,
    kind: str,
    payload: Any,
    dependencies: Sequence[str] = (),
) -> dict[str, Any]:
    rendered = _text(payload)
    return {
        "unit_id": unit_id,
        "kind": kind,
        "payload": payload,
        "character_count": len(rendered),
        "dependencies": list(dict.fromkeys(str(value) for value in dependencies if value)),
        "structural_weight": max(1, 1 + math.ceil(len(rendered) / 160)),
    }


def decompose_task(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Create a finite structural work graph from the current ticket only."""
    task = _mapping(packet.get("task"))
    question = task.get("question") or packet.get("question") or task
    requirements = _sequence(task.get("requirements"))
    evidence = _sequence(packet.get("evidence"))
    acceptance = _sequence(packet.get("execution_acceptance"))
    deliverables = _deliverables(task)

    units: list[dict[str, Any]] = []
    objective_id = "objective-1"
    units.append(_unit(objective_id, "objective", question))

    evidence_ids: list[str] = []
    for index, row in enumerate(evidence, 1):
        unit_id = f"evidence-{index}"
        evidence_ids.append(unit_id)
        units.append(_unit(unit_id, "evidence", row, (objective_id,)))

    requirement_ids: list[str] = []
    requirement_dependencies = evidence_ids or [objective_id]
    for index, row in enumerate(requirements, 1):
        unit_id = f"requirement-{index}"
        requirement_ids.append(unit_id)
        units.append(_unit(unit_id, "requirement", row, requirement_dependencies))

    acceptance_ids: list[str] = []
    acceptance_dependencies = requirement_ids or evidence_ids or [objective_id]
    for index, row in enumerate(acceptance, 1):
        unit_id = f"acceptance-{index}"
        acceptance_ids.append(unit_id)
        units.append(_unit(unit_id, "acceptance", row, acceptance_dependencies))

    deliverable_ids: list[str] = []
    deliverable_dependencies = acceptance_ids or requirement_ids or evidence_ids or [objective_id]
    for index, row in enumerate(deliverables, 1):
        unit_id = f"deliverable-{index}"
        deliverable_ids.append(unit_id)
        units.append(_unit(unit_id, "deliverable", row, deliverable_dependencies))

    terminal_dependencies = deliverable_ids or acceptance_ids or requirement_ids or evidence_ids or [objective_id]
    if len(units) > 1:
        units.append(_unit("synthesis-1", "synthesis", "integrate-current-task", terminal_dependencies))

    by_id = {str(row["unit_id"]): row for row in units}
    depth_by_id: dict[str, int] = {}
    for row in units:
        dependencies = [value for value in row["dependencies"] if value in by_id]
        depth_by_id[str(row["unit_id"])] = 1 + max(
            (depth_by_id.get(value, 1) for value in dependencies),
            default=0,
        )

    depth_counts: dict[int, int] = {}
    edges: list[dict[str, str]] = []
    max_fan_in = 0
    for row in units:
        unit_id = str(row["unit_id"])
        depth = depth_by_id[unit_id]
        row["depth"] = depth
        depth_counts[depth] = depth_counts.get(depth, 0) + 1
        max_fan_in = max(max_fan_in, len(row["dependencies"]))
        for dependency in row["dependencies"]:
            edges.append({"from": dependency, "to": unit_id})

    counts_by_kind: dict[str, int] = {}
    for row in units:
        kind = str(row["kind"])
        counts_by_kind[kind] = counts_by_kind.get(kind, 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "current-ticket-structural-decomposition",
        "semantic_keyword_routing_used": False,
        "domain_hardcoding_used": False,
        "cross_task_history_used": False,
        "finite_acyclic_by_construction": True,
        "work_units": units,
        "dependency_edges": edges,
        "work_unit_count": len(units),
        "dependency_edge_count": len(edges),
        "maximum_depth": max(depth_by_id.values(), default=1),
        "maximum_parallel_width": max(depth_counts.values(), default=1),
        "maximum_fan_in": max_fan_in,
        "counts_by_kind": counts_by_kind,
    }


def discover_parameter_requirements(
    decomposition: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Decide which parameter families are needed for this task before values."""
    counts = _mapping(decomposition.get("counts_by_kind"))
    work_units = int(decomposition.get("work_unit_count") or 0)
    depth = int(decomposition.get("maximum_depth") or 1)
    width = int(decomposition.get("maximum_parallel_width") or 1)

    active: list[str] = [
        "work_graph_load",
        "team_size",
        "role_topology",
        "prompt_token_estimate",
        "completion_token_estimate",
        "protocol_reserve",
        "dependency_fan_in",
        "model_assignment",
        "solver_time",
        "solver_seed",
    ]
    reasons: dict[str, str] = {
        "work_graph_load": "every task has a finite work graph",
        "team_size": "execution width must match the current work graph",
        "role_topology": "roles are synthesized from current work units",
        "prompt_token_estimate": "model requests require current-task input sizing",
        "completion_token_estimate": "model outputs require current-task sizing",
        "protocol_reserve": "runtime protocol overhead must scale with this plan",
        "dependency_fan_in": "downstream context depends on the current graph",
        "model_assignment": "current roles must be assigned to current candidates",
        "solver_time": "optimization effort scales with current problem size",
        "solver_seed": "current task requires deterministic reproducibility",
    }

    def activate(parameter_id: str, reason: str) -> None:
        if parameter_id not in active:
            active.append(parameter_id)
        reasons[parameter_id] = reason

    if int(counts.get("evidence") or 0) > 0:
        activate("evidence_pressure", "ticket contains evidence work units")
        activate("evidence_coverage", "evidence must be represented in role planning")
    if int(counts.get("requirement") or 0) > 0:
        activate("constraint_pressure", "ticket contains explicit requirements")
        activate("constraint_coverage", "requirements must be represented in execution")
    if int(counts.get("acceptance") or 0) > 0:
        activate("validation_depth", "ticket contains explicit acceptance criteria")
    if int(counts.get("deliverable") or 0) > 0:
        activate("delivery_pressure", "ticket contains explicit deliverables")
    if work_units > 1:
        activate("synthesis_fan_in", "multiple work units require final integration")
    if depth > 2 or width > 1 or int(counts.get("acceptance") or 0) > 0:
        activate("review_depth", "work graph requires cross-checking before synthesis")
    if len(candidates) > 1:
        activate("recovery_depth", "candidate inventory permits current-task recovery")
        activate("recovery_order", "recovery candidates require task-specific ordering")

    has_price = any(
        float(row.get("prompt_usd_per_million") or 0.0) > 0
        or float(row.get("completion_usd_per_million") or 0.0) > 0
        or float(row.get("request_usd") or 0.0) > 0
        for row in candidates
    )
    has_intelligence = any(int(row.get("official_intelligence_rank") or 0) > 0 for row in candidates)
    has_popularity = any(int(row.get("popularity_rank") or 0) > 0 for row in candidates)
    has_capacity = any(
        int(row.get("context_length") or 0) > 0
        or int(row.get("max_completion_tokens") or 0) > 0
        for row in candidates
    )
    if has_price:
        activate("cost_weight", "candidate inventory exposes current price metadata")
    if has_intelligence:
        activate("intelligence_weight", "candidate inventory exposes reasoning/intelligence rank")
    if has_popularity:
        activate("popularity_weight", "candidate inventory exposes current usage popularity")
    if has_capacity:
        activate("capacity_headroom_weight", "candidate inventory exposes native capacity")
    if sum((has_price, has_intelligence, has_popularity, has_capacity)) >= 2:
        activate("marginal_return_weight", "multiple objective dimensions permit value-per-cost calculation")

    return {
        "schema_version": PARAMETER_SCHEMA_VERSION,
        "discovery_mode": "current-task-and-current-candidate-signals",
        "required_parameter_ids": active,
        "required_parameter_count": len(active),
        "activation_reasons": reasons,
        "fixed_parameter_template_used": False,
        "semantic_keyword_routing_used": False,
        "cross_task_history_used": False,
        "only_hard_model_boundary": "no-tools",
    }


def _hierarchical_profile(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    parameter_requirements: Mapping[str, Any],
) -> dict[str, Any]:
    base = dict(build_task_demand_profile(packet, candidates))
    pressure = dict(_mapping(base.get("pressure")))

    work_units = int(decomposition.get("work_unit_count") or 1)
    edges = int(decomposition.get("dependency_edge_count") or 0)
    depth = int(decomposition.get("maximum_depth") or 1)
    width = int(decomposition.get("maximum_parallel_width") or 1)
    max_fan_in = int(decomposition.get("maximum_fan_in") or 0)
    parameter_count = int(parameter_requirements.get("required_parameter_count") or 0)

    structure_pressure = _clamp(
        7 * math.sqrt(max(1, work_units))
        + 3 * depth
        + 2 * width
        + math.sqrt(max(0, edges)) * 3
    )
    base_overall = int(pressure.get("overall") or 0)
    overall = _clamp(0.62 * base_overall + 0.38 * structure_pressure)
    pressure["structure"] = structure_pressure
    pressure["overall"] = overall

    prompt_tokens = int(base.get("expected_prompt_tokens") or 1)
    completion_tokens = int(base.get("expected_completion_tokens") or 1)
    reserve_tokens = int(base.get("protocol_reserve_tokens") or 1)
    prompt_tokens += 24 * work_units + 8 * edges
    completion_tokens += 40 * work_units + 24 * depth + 12 * width
    reserve_tokens += 12 * parameter_count + 8 * max_fan_in

    base.update(
        {
            "schema_version": "v5-hierarchical-dynamic-task-profile-1",
            "planning_sequence": [
                "task-decomposition",
                "parameter-requirement-discovery",
                "parameter-value-resolution",
                "team-and-role-derivation",
                "ortools-model-assignment",
            ],
            "task_decomposition_completed": True,
            "parameter_discovery_completed": True,
            "parameter_values_resolved_before_model_assignment": True,
            "work_unit_count": work_units,
            "dependency_edge_count": edges,
            "maximum_task_depth": depth,
            "maximum_parallel_width": width,
            "expected_prompt_tokens": prompt_tokens,
            "expected_completion_tokens": completion_tokens,
            "protocol_reserve_tokens": reserve_tokens,
            "dependency_fan_in_estimate": max(
                int(base.get("dependency_fan_in_estimate") or 1),
                max_fan_in,
                math.ceil(math.sqrt(max(1, work_units))),
            ),
            "pressure": pressure,
            "active_parameter_ids": list(parameter_requirements.get("required_parameter_ids") or []),
            "active_parameter_count": parameter_count,
            "all_calculable_planning_parameters_dynamic": True,
            "hard_model_eligibility_gates": [],
            "only_hard_model_boundary": "no-tools",
        }
    )
    return base


def _team_shape(
    profile: Mapping[str, Any],
    candidate_count: int,
    parameter_requirements: Mapping[str, Any],
) -> tuple[int, int, float]:
    if candidate_count <= 0:
        return 0, 0, 0.0
    work_units = max(1, int(profile.get("work_unit_count") or 1))
    depth = max(1, int(profile.get("maximum_task_depth") or 1))
    width = max(1, int(profile.get("maximum_parallel_width") or 1))
    overall = int(_mapping(profile.get("pressure")).get("overall") or 0)

    primary = min(
        candidate_count,
        max(
            1,
            math.ceil(
                math.sqrt(work_units + width + depth + max(0, overall) / 18.0)
            ),
        ),
    )
    remaining = max(0, candidate_count - primary)
    active = set(parameter_requirements.get("required_parameter_ids") or [])
    if "recovery_depth" not in active or remaining <= 0:
        return primary, 0, 0.0

    recovery_ratio = min(
        0.85,
        max(
            0.08,
            (
                overall
                + 7 * depth
                + 4 * width
                + 3 * int(profile.get("acceptance_count") or 0)
            )
            / 240.0,
        ),
    )
    recovery = min(remaining, max(1, math.ceil(primary * recovery_ratio)))
    return primary, recovery, float(recovery_ratio)


def _role_plan(
    primary_count: int,
    decomposition: Mapping[str, Any],
    parameter_requirements: Mapping[str, Any],
) -> list[dict[str, Any]]:
    units = [dict(row) for row in _sequence(decomposition.get("work_units")) if isinstance(row, Mapping)]
    unit_ids = [str(row.get("unit_id") or "") for row in units if row.get("unit_id")]
    active = set(parameter_requirements.get("required_parameter_ids") or [])
    if primary_count <= 1:
        return [
            {
                "role_id": "synthesis",
                "role_kind": "synthesis",
                "metric_role_id": "synthesis",
                "role": "动态综合专家：执行本任务全部拆解单元并形成最终交付",
                "assigned_work_units": unit_ids,
                "role_source_signal": "single-node-hierarchical-plan",
            }
        ]

    review_count = 1 if "review_depth" in active and primary_count >= 3 else 0
    synthesis_count = 1
    independent_count = max(1, primary_count - review_count - synthesis_count)

    allocatable = [row for row in units if row.get("kind") != "synthesis"]
    allocatable.sort(
        key=lambda row: (
            -int(row.get("structural_weight") or 0),
            -int(row.get("depth") or 0),
            str(row.get("unit_id") or ""),
        )
    )
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(independent_count)]
    for index, row in enumerate(allocatable):
        buckets[index % independent_count].append(row)

    roles: list[dict[str, Any]] = []
    for index, bucket in enumerate(buckets, 1):
        evidence_units = sum(1 for row in bucket if row.get("kind") == "evidence")
        validation_units = sum(1 for row in bucket if row.get("kind") in {"requirement", "acceptance"})
        metric_role = "evidence" if evidence_units + validation_units > len(bucket) / 2 else "options"
        roles.append(
            {
                "role_id": f"workstream-{index}",
                "role_kind": "independent",
                "metric_role_id": metric_role,
                "role": f"动态工作流专家{index}：完成分配的当前任务拆解单元并检查反例",
                "assigned_work_units": [str(row.get("unit_id")) for row in bucket],
                "role_source_signal": "hierarchical-work-unit-allocation",
            }
        )

    if review_count:
        review_units = [
            str(row.get("unit_id"))
            for row in units
            if row.get("kind") in {"requirement", "acceptance", "deliverable"}
        ]
        roles.append(
            {
                "role_id": "review",
                "role_kind": "review",
                "metric_role_id": "review",
                "role": "动态交叉审查专家：按当前任务依赖图检查覆盖、冲突、遗漏和验收条件",
                "assigned_work_units": review_units,
                "role_source_signal": "parameter-required-review-depth",
            }
        )

    roles.append(
        {
            "role_id": "synthesis",
            "role_kind": "synthesis",
            "metric_role_id": "synthesis",
            "role": "动态最终综合专家：根据当前任务图和全部前序节点生成唯一完整交付",
            "assigned_work_units": unit_ids,
            "role_source_signal": "hierarchical-terminal-synthesis",
        }
    )
    return roles[:primary_count]


def build_hierarchical_planning_context(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    decomposition = decompose_task(packet)
    parameter_requirements = discover_parameter_requirements(decomposition, candidates)
    profile = _hierarchical_profile(packet, candidates, decomposition, parameter_requirements)
    primary_count, recovery_count, recovery_ratio = _team_shape(
        profile,
        len(candidates),
        parameter_requirements,
    )
    roles = _role_plan(primary_count, decomposition, parameter_requirements)

    resolved_parameters = {
        "active_parameter_ids": list(parameter_requirements["required_parameter_ids"]),
        "team_size": primary_count,
        "recovery_size": recovery_count,
        "recovery_ratio": round(recovery_ratio, 6),
        "role_count": len(roles),
        "role_topology": [str(row["role_id"]) for row in roles],
        "prompt_token_estimate": int(profile.get("expected_prompt_tokens") or 0),
        "completion_token_estimate": int(profile.get("expected_completion_tokens") or 0),
        "protocol_reserve": int(profile.get("protocol_reserve_tokens") or 0),
        "dependency_fan_in": int(profile.get("dependency_fan_in_estimate") or 0),
        "work_graph_load": {
            "work_units": int(decomposition.get("work_unit_count") or 0),
            "edges": int(decomposition.get("dependency_edge_count") or 0),
            "depth": int(decomposition.get("maximum_depth") or 0),
            "parallel_width": int(decomposition.get("maximum_parallel_width") or 0),
        },
        "parameter_values_derived_from_current_task": True,
        "fixed_parameter_values_used": False,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "planning_sequence": [
            "task-decomposition",
            "parameter-requirement-discovery",
            "parameter-value-resolution",
            "team-and-role-derivation",
            "ortools-model-assignment",
        ],
        "decomposition": decomposition,
        "parameter_requirements": parameter_requirements,
        "resolved_parameters": resolved_parameters,
        "resolved_profile": profile,
        "role_plan": roles,
        "primary_expert_count": primary_count,
        "recovery_count": recovery_count,
        "all_calculable_planning_parameters_dynamic": True,
        "fixed_parameter_template_used": False,
        "fixed_team_template_used": False,
        "semantic_keyword_routing_used": False,
        "cross_task_history_used": False,
        "hard_model_eligibility_gates": [],
        "only_hard_model_boundary": "no-tools",
    }


__all__ = [
    "PARAMETER_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "build_hierarchical_planning_context",
    "decompose_task",
    "discover_parameter_requirements",
]
