"""Task-dynamic OR-Tools expert composition with open model eligibility.

Historical Top50/4+4/company-uniqueness/free-first/OPTIMAL-only/price/flagship
rules are not model-admission gates.  Governance supplies a reasoning-popularity
candidate sequence; the current task determines team size, role mix, recovery
depth, role demand, scoring weights and solver time.  Provider routing remains
unrestricted and delegated to OpenRouter.

The only hard model-execution boundary is no-tools.  Protocol integrity, unique
execution identities and DAG validity are structural invariants rather than
business model-eligibility gates.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

from v5_task_adaptive_scoring import (
    PRINCIPLES,
    RECOVERY_ROLE_ID,
    SCHEMA_VERSION as TASK_SCORING_SCHEMA_VERSION,
    TaskAdaptiveScoringError,
    build_role_metrics,
    build_task_demand_profile,
)


class Top50PoolOptimizationError(RuntimeError):
    """Raised only when there is no structurally executable planning input."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _candidate_rows(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Accept every governance-supplied candidate identity without business gates."""
    sources = (
        plan.get("expert_candidate_pool"),
        plan.get("top50_expert_selectable_candidates"),
        plan.get("top50_reasoning_models"),
        plan.get("top20_expert_selectable_candidates"),
    )
    source = next((rows for rows in (_rows(value) for value in sources) if rows), [])
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(source, 1):
        model = str(raw.get("model") or raw.get("id") or "").strip()
        if not model or model in seen:
            continue
        row = dict(raw)
        row["model"] = model
        row.setdefault(
            "company",
            model.split("/", 1)[0] if "/" in model else "unknown",
        )
        row.setdefault("popularity_rank", index)
        row.setdefault("official_intelligence_rank", index)
        row.setdefault(
            "prompt_usd_per_million",
            row.get("prompt_price_per_million", 0.0) or 0.0,
        )
        row.setdefault(
            "completion_usd_per_million",
            row.get("completion_price_per_million", 0.0) or 0.0,
        )
        row.setdefault("request_usd", 0.0)
        row.setdefault("context_length", 0)
        row.setdefault("max_completion_tokens", 0)
        row["provider_routing_mode"] = "unrestricted-openrouter"
        row["provider_restrictions_applied"] = False
        row["tool_use_forbidden"] = True
        row["tools_allowed"] = False
        result.append(row)
        seen.add(model)
    if not result:
        raise Top50PoolOptimizationError(
            "governance supplied no usable expert candidates"
        )
    return result


def _pressure(profile: Mapping[str, Any], key: str) -> int:
    pressure = profile.get("pressure")
    values = pressure if isinstance(pressure, Mapping) else {}
    try:
        return max(0, min(100, int(values.get(key) or 0)))
    except (TypeError, ValueError):
        return 0


def _recovery_resilience_ratio(profile: Mapping[str, Any]) -> float:
    """Compute current-task recovery depth from pressure and structural breadth."""
    overall = _pressure(profile, "overall")
    requirements = max(0, int(profile.get("requirement_count") or 0))
    acceptance = max(0, int(profile.get("acceptance_count") or 0))
    delivery = max(0, int(profile.get("delivery_item_count") or 0))
    evidence = max(0, int(profile.get("evidence_count") or 0))
    breadth_pressure = min(
        100,
        5 * (requirements + acceptance + delivery) + 3 * evidence,
    )
    # The ratio is task-derived; clamps only prevent nonsensical zero/all-pool
    # recovery shapes and are not model eligibility gates.
    return min(
        0.90,
        max(0.10, (overall + breadth_pressure + 20) / 220.0),
    )


def _dynamic_team_shape(
    profile: Mapping[str, Any],
    candidate_count: int,
) -> tuple[int, int]:
    """Derive team/recovery size from current-task load rather than fixed counts."""
    if candidate_count <= 0:
        return 0, 0
    overall = _pressure(profile, "overall")
    structural = (
        1
        + int(profile.get("requirement_count") or 0)
        + int(profile.get("acceptance_count") or 0)
        + int(profile.get("delivery_item_count") or 0)
        + int(profile.get("evidence_count") or 0)
        + math.ceil(
            (
                int(profile.get("task_characters") or 0)
                + int(profile.get("evidence_characters") or 0)
            )
            / 4000
        )
        + math.ceil(overall / 15)
    )
    primary = min(
        candidate_count,
        max(1, math.ceil(math.sqrt(max(1, structural)))),
    )
    remaining = max(0, candidate_count - primary)
    recovery = min(
        remaining,
        max(0, math.ceil(primary * _recovery_resilience_ratio(profile))),
    )
    return primary, recovery


def _role_plan(
    primary_count: int,
    profile: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Generate role topology from current pressure rather than a fixed 4-role graph."""
    if primary_count <= 1:
        return [
            {
                "role_id": "synthesis",
                "role_kind": "synthesis",
                "metric_role_id": "synthesis",
                "role": "动态综合专家：独立完成当前任务的分析、审查与最终交付",
                "role_source_signal": "single-expert-current-task-shape",
            }
        ]

    evidence_signal = _pressure(profile, "evidence") + _pressure(
        profile, "constraints"
    )
    options_signal = _pressure(profile, "delivery") + _pressure(
        profile, "constraints"
    )
    first_metric = "evidence" if evidence_signal >= options_signal else "options"
    second_metric = "options" if first_metric == "evidence" else "evidence"

    if primary_count == 2:
        return [
            {
                "role_id": "independent-1",
                "role_kind": "independent",
                "metric_role_id": first_metric,
                "role": "动态独立专家：依据当前任务主压力形成完整分析并检查反例",
                "role_source_signal": first_metric,
            },
            {
                "role_id": "synthesis",
                "role_kind": "synthesis",
                "metric_role_id": "synthesis",
                "role": "动态综合专家：审查前序结果并形成最终交付",
                "role_source_signal": "downstream-synthesis",
            },
        ]

    independent_count = max(1, primary_count - 2)
    roles: list[dict[str, str]] = []
    for index in range(independent_count):
        metric_role = first_metric if index % 2 == 0 else second_metric
        roles.append(
            {
                "role_id": f"independent-{index + 1}",
                "metric_role_id": metric_role,
                "role_kind": "independent",
                "role": (
                    f"动态独立专家{index + 1}："
                    "依据当前结构压力分析证据、机制、方案、反例与不确定性"
                ),
                "role_source_signal": metric_role,
            }
        )
    roles.extend(
        [
            {
                "role_id": "review",
                "role_kind": "review",
                "metric_role_id": "review",
                "role": "动态交叉审查专家：比较全部前序分析并定位冲突、遗漏和失败模式",
                "role_source_signal": "current-fan-in-review",
            },
            {
                "role_id": "synthesis",
                "role_kind": "synthesis",
                "metric_role_id": "synthesis",
                "role": "动态最终综合专家：依据任务和全部前序结果形成唯一完整交付",
                "role_source_signal": "current-fan-in-synthesis",
            },
        ]
    )
    return roles


def _metric_role(role: Mapping[str, Any]) -> str:
    value = str(
        role.get("metric_role_id") or role.get("role_id") or "evidence"
    )
    return (
        value
        if value in {"evidence", "options", "review", "synthesis"}
        else "evidence"
    )


def _annotate(
    row: Mapping[str, Any],
    metric: Mapping[str, Any],
) -> dict[str, Any]:
    value = dict(row)
    value.update(
        {
            "estimated_task_cost_usd": float(
                metric.get("estimated_task_cost_usd") or 0.0
            ),
            "task_adaptive_objective_score": int(
                metric.get("objective_score") or 0
            ),
            "task_adaptive_base_objective_score": int(
                metric.get("base_objective_score") or 0
            ),
            "task_adaptive_ranks": dict(metric.get("ranks") or {}),
            "task_adaptive_weights": dict(metric.get("weights") or {}),
            "task_adaptive_role_tokens": dict(metric.get("role_tokens") or {}),
            "task_adaptive_capacity_compatible": bool(
                metric.get("compatible", True)
            ),
            "capacity_shortfall": float(
                metric.get("capacity_shortfall") or 0.0
            ),
            "capacity_shortfall_penalty": int(
                metric.get("capacity_shortfall_penalty") or 0
            ),
            "capacity_is_hard_gate": False,
            "marginal_cost_per_quality": float(
                metric.get("marginal_cost_per_quality") or 0.0
            ),
            "tool_use_forbidden": True,
            "tools_allowed": False,
        }
    )
    return value


def _annotate_recovery_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows.sort(
        key=lambda row: (
            int(row.get("task_adaptive_objective_score") or 0),
            float(row.get("estimated_task_cost_usd") or 0.0),
            int(row.get("popularity_rank") or 1_000_000),
            str(row.get("model") or ""),
        )
    )
    for index, row in enumerate(rows, 1):
        row["slot"] = index
        row["warm_recovery_priority"] = index
        row["recovery_resilience"] = {
            "selection_source": "current-task-recovery-role-objective",
            "soft_company_penalty": 0,
            "soft_free_route_penalty": 0,
            "hard_company_diversity_constraint": False,
            "free_model_forbidden": False,
            "provider_constraint": False,
            "capacity_hard_gate": False,
        }
    return rows


def _heuristic_recoveries(
    candidates: Sequence[Mapping[str, Any]],
    recovery_metrics: Mapping[str, Mapping[str, Any]],
    used: set[str],
    recovery_count: int,
) -> list[dict[str, Any]]:
    ranked = sorted(
        (
            row
            for row in candidates
            if str(row.get("model") or "") not in used
        ),
        key=lambda row: (
            int(
                recovery_metrics[str(row["model"])].get("objective_score")
                or 0
            ),
            float(
                recovery_metrics[str(row["model"])].get(
                    "estimated_task_cost_usd"
                )
                or 0.0
            ),
            str(row["model"]),
        ),
    )
    backups: list[dict[str, Any]] = []
    for source in ranked[:recovery_count]:
        model_id = str(source["model"])
        backups.append(_annotate(source, recovery_metrics[model_id]))
        used.add(model_id)
    return _annotate_recovery_rows(backups)


def _dynamic_solver_profile(
    profile: Mapping[str, Any],
    candidate_count: int,
    role_count: int,
    recovery_count: int,
) -> dict[str, Any]:
    problem_cells = max(1, candidate_count * (role_count + 1))
    overall = _pressure(profile, "overall")
    structural = (
        int(profile.get("requirement_count") or 0)
        + int(profile.get("acceptance_count") or 0)
        + int(profile.get("delivery_item_count") or 0)
        + int(profile.get("evidence_count") or 0)
    )
    max_time = min(
        60.0,
        max(
            2.0,
            1.0
            + math.log2(problem_cells + 1)
            + overall / 12.0
            + structural / 8.0
            + recovery_count / 3.0,
        ),
    )
    seed_material = {
        "task_characters": int(profile.get("task_characters") or 0),
        "evidence_characters": int(profile.get("evidence_characters") or 0),
        "pressure": dict(profile.get("pressure") or {}),
        "candidate_count": candidate_count,
        "role_count": role_count,
        "recovery_count": recovery_count,
    }
    seed = int(_sha(seed_material)[:8], 16) % 2_147_483_647
    return {
        "problem_cells": problem_cells,
        "max_time_in_seconds": round(max_time, 6),
        "random_seed": seed,
        # Single worker is retained as a reproducibility/integrity invariant,
        # not a task/model eligibility gate.
        "num_search_workers": 1,
        "single_worker_reason": "deterministic-audit-reproducibility",
    }


def _solve(
    candidates: list[dict[str, Any]],
    profile: Mapping[str, Any],
    roles: Sequence[Mapping[str, str]],
    recovery_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    try:
        metrics_by_role = [
            build_role_metrics(candidates, profile, _metric_role(role))
            for role in roles
        ]
        recovery_metrics = build_role_metrics(
            candidates,
            profile,
            RECOVERY_ROLE_ID,
        )
    except TaskAdaptiveScoringError as exc:
        raise Top50PoolOptimizationError(str(exc)) from exc

    model = cp_model.CpModel()
    active = {
        (candidate_index, role_index): model.new_bool_var(
            f"active_{candidate_index}_{role_index}"
        )
        for candidate_index in range(len(candidates))
        for role_index in range(len(roles))
    }
    recovery = {
        index: model.new_bool_var(f"recovery_{index}")
        for index in range(len(candidates))
    }

    for role_index in range(len(roles)):
        model.add(
            sum(
                active[index, role_index]
                for index in range(len(candidates))
            )
            == 1
        )

    # Same model identity cannot simultaneously occupy multiple expert/recovery
    # nodes.  This is graph identity integrity, not a company/provider/model gate.
    for index in range(len(candidates)):
        model.add(
            sum(active[index, role] for role in range(len(roles)))
            + recovery[index]
            <= 1
        )
    model.add(sum(recovery.values()) == int(recovery_count))

    # No company, price, free/paid, flagship, TopN, Provider or capacity hard
    # constraints are applied.  All current-task considerations enter objective
    # metrics only.
    tie_base = max(2, len(candidates) * max(1, len(roles)) + 1)
    terms: list[Any] = []
    for index, row in enumerate(candidates):
        model_id = str(row["model"])
        for role_index in range(len(roles)):
            metric = metrics_by_role[role_index][model_id]
            tie = index * max(1, len(roles)) + role_index
            terms.append(
                (
                    int(metric.get("objective_score") or 0) * tie_base
                    + tie
                )
                * active[index, role_index]
            )
        recovery_metric = recovery_metrics[model_id]
        terms.append(
            (
                int(recovery_metric.get("objective_score") or 0) * tie_base
                + index
            )
            * recovery[index]
        )

    model.minimize(sum(terms))

    solver_profile = _dynamic_solver_profile(
        profile,
        len(candidates),
        len(roles),
        recovery_count,
    )
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = int(
        solver_profile["num_search_workers"]
    )
    solver.parameters.random_seed = int(solver_profile["random_seed"])
    solver.parameters.max_time_in_seconds = float(
        solver_profile["max_time_in_seconds"]
    )
    status = solver.solve(model)
    accepted_statuses = {cp_model.OPTIMAL, cp_model.FEASIBLE}

    if status not in accepted_statuses:
        used: set[str] = set()
        selected: list[dict[str, Any]] = []
        for role_index, role in enumerate(roles):
            ranked = sorted(
                candidates,
                key=lambda row: (
                    int(
                        metrics_by_role[role_index][str(row["model"])].get(
                            "objective_score"
                        )
                        or 0
                    ),
                    float(
                        metrics_by_role[role_index][str(row["model"])].get(
                            "estimated_task_cost_usd"
                        )
                        or 0.0
                    ),
                    str(row["model"]),
                ),
            )
            source = next(
                (
                    row
                    for row in ranked
                    if str(row["model"]) not in used
                ),
                ranked[0],
            )
            used.add(str(source["model"]))
            selected.append(
                {
                    **_annotate(
                        source,
                        metrics_by_role[role_index][str(source["model"])],
                    ),
                    **dict(role),
                    "slot": role_index + 1,
                }
            )
        backups = _heuristic_recoveries(
            candidates,
            recovery_metrics,
            used,
            recovery_count,
        )
        return selected, backups, {
            "optimizer": "ortools-cp-sat-with-heuristic-fallback",
            "solver_status": solver.status_name(status),
            "optimality_proven": False,
            "fallback_used": True,
            "dynamic_solver_profile": solver_profile,
        }

    selected: list[dict[str, Any]] = []
    for role_index, role in enumerate(roles):
        matches = [
            index
            for index in range(len(candidates))
            if solver.value(active[index, role_index]) == 1
        ]
        index = matches[0]
        source = candidates[index]
        selected.append(
            {
                **_annotate(
                    source,
                    metrics_by_role[role_index][str(source["model"])],
                ),
                **dict(role),
                "slot": role_index + 1,
            }
        )

    backups: list[dict[str, Any]] = []
    for index, row in enumerate(candidates):
        if solver.value(recovery[index]) == 1:
            backups.append(
                _annotate(row, recovery_metrics[str(row["model"])])
            )
    backups = _annotate_recovery_rows(backups)

    return selected, backups, {
        "optimizer": "ortools-cp-sat",
        "solver_status": solver.status_name(status),
        "optimality_proven": status == cp_model.OPTIMAL,
        "fallback_used": False,
        "objective_value": float(solver.objective_value),
        "best_objective_bound": float(solver.best_objective_bound),
        "wall_time_seconds": round(float(solver.wall_time), 6),
        "dynamic_solver_profile": solver_profile,
    }


def materialize_top50_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = packet.get("governance_model_plan")
    if not isinstance(source, Mapping):
        raise Top50PoolOptimizationError("governance_model_plan is missing")
    source_plan = dict(source)
    candidates = _candidate_rows(source_plan)
    try:
        profile = build_task_demand_profile(packet, candidates)
    except TaskAdaptiveScoringError as exc:
        raise Top50PoolOptimizationError(str(exc)) from exc

    primary_count, recovery_count = _dynamic_team_shape(
        profile,
        len(candidates),
    )
    roles = _role_plan(primary_count, profile)
    selected, recoveries, solver_audit = _solve(
        candidates,
        profile,
        roles,
        recovery_count,
    )
    selected_ids = {str(row["model"]) for row in selected}
    recovery_ids = {str(row["model"]) for row in recoveries}
    standby = [
        dict(row)
        for row in candidates
        if str(row["model"]) not in selected_ids | recovery_ids
    ]

    audit = {
        **solver_audit,
        "schema_version": "v5-fully-dynamic-expert-composition-2",
        "selection_principles": list(PRINCIPLES),
        "task_adaptive_scoring_schema_version": TASK_SCORING_SCHEMA_VERSION,
        "task_demand_profile": dict(profile),
        "primary_expert_count": len(selected),
        "recovery_count": len(recoveries),
        "role_plan": [dict(role) for role in roles],
        "recovery_resilience": {
            "computed_ratio": round(_recovery_resilience_ratio(profile), 6),
            "recovery_count": len(recoveries),
            "selection_source": "current-task-recovery-role-objective",
            "free_route_soft_penalty": 0,
            "primary_company_overlap_soft_penalty": 0,
            "recovery_company_concentration_soft_penalty": 0,
            "free_models_forbidden": False,
            "company_diversity_hard_constraint": False,
            "provider_diversity_hard_constraint": False,
            "capacity_hard_constraint": False,
            "cross_task_failure_history_used": False,
        },
        "all_calculable_planning_parameters_dynamic": True,
        "hard_model_eligibility_gates": [],
        "only_hard_model_boundary": "no-tools",
        "tool_use_forbidden": True,
        "fixed_team_size_used": False,
        "fixed_four_plus_four_used": False,
        "fixed_role_topology_used": False,
        "company_uniqueness_constraint_used": False,
        "top50_membership_constraint_used": False,
        "budget_constraint_used": False,
        "price_gate_used": False,
        "flagship_gate_used": False,
        "free_first_gate_used": False,
        "canary_gate_used": False,
        "provider_constraint_used": False,
        "capacity_gate_used": False,
        "optimizer_optimality_required": False,
        "semantic_keyword_routing_used": False,
        "provider_routing_mode": "unrestricted-openrouter",
        "same_model_identity_reuse_prevented_for_graph_integrity": True,
    }

    plan = dict(source_plan)
    plan.update(
        {
            "selected_models": selected,
            "recovery_models": recoveries,
            "expert_count": len(selected),
            "recovery_count": len(recoveries),
            "expert_center_ordered_standby": standby,
            "expert_center_ordered_standby_count": len(standby),
            "expert_center_pool_selection_completed": True,
            "expert_center_top50_optimization_completed": True,
            "expert_center_dynamic_composition_completed": True,
            "selected_from_top20_reasoning_pool_only": False,
            "selected_from_top50_reasoning_pool_only": False,
            "selected_from_governance_candidate_pool": True,
            "candidate_pool_authority": "decision-system-governance",
            "model_assignment_authority": "expert-assessment-center-dynamic-ortools",
            "optimizer": str(audit["optimizer"]),
            "optimizer_audit": audit,
            "task_adaptive_scoring_completed": True,
            "task_adaptive_scoring_schema_version": TASK_SCORING_SCHEMA_VERSION,
            "task_demand_profile": profile,
            "selection_principles": list(PRINCIPLES),
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "model_substitution_allowed": True,
            "fixed_team_size_required": False,
            "fixed_role_topology_required": False,
            "company_deduplication_required": False,
            "free_first_required": False,
            "canary_required_before_execution": False,
            "optimizer_optimality_required": False,
            "price_filter_required": False,
            "flagship_filter_required": False,
            "intelligence_rank_required": False,
            "provider_endpoint_qualification_required": False,
            "zdr_provider_qualification_required": False,
            "tool_use_forbidden": True,
            "tools_allowed": False,
            "only_hard_model_boundary": "no-tools",
            "all_calculable_planning_parameters_dynamic": True,
            "selection_policy": (
                "governance reasoning-popularity candidates -> current-task "
                "structural demand -> dynamic token/reserve/fan-in estimates -> "
                "dynamic team size, role topology and recovery depth -> dynamic "
                "cost/intelligence/popularity/capacity/marginal-return scoring -> "
                "OR-Tools assignment -> feasible-or-heuristic recovery -> "
                "unrestricted OpenRouter Provider routing; no business eligibility "
                "gates; hard model boundary=no-tools"
            ),
        }
    )
    plan.pop("plan_sha256", None)
    selection_basis_sha256 = _sha(plan)

    receipt = {
        "schema_version": "expert-center-fully-dynamic-selection-receipt-v2",
        "selection_basis_sha256": selection_basis_sha256,
        "selected_models": [row["model"] for row in selected],
        "recovery_models": [row["model"] for row in recoveries],
        "primary_expert_count": len(selected),
        "recovery_count": len(recoveries),
        "standby_count": len(standby),
        "optimizer_audit": audit,
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "free_first_required": False,
        "tool_use_forbidden": True,
        "tools_allowed": False,
        "only_hard_model_boundary": "no-tools",
        "model_calls": 0,
    }
    receipt["receipt_sha256"] = _sha(receipt)
    plan["expert_center_selection_receipt"] = receipt
    plan["plan_sha256"] = _sha(plan)

    materialized = dict(packet)
    materialized["governance_model_plan"] = plan
    return materialized, receipt


def materialize_candidate_pool_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    return materialize_top50_selection(packet)


__all__ = [
    "Top50PoolOptimizationError",
    "materialize_candidate_pool_selection",
    "materialize_top50_selection",
]
