"""Task-dynamic OR-Tools expert composition without fixed business gates.

Historical Top50/4+4/company-uniqueness/OPTIMAL-only rules are compatibility
metadata only. The current task determines team size, role mix and recovery
capacity. Any candidate supplied by governance may participate; Provider routing
remains unrestricted and delegated to OpenRouter.
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
    """Accept every governance-supplied candidate source without pool gates."""
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
        row.setdefault("company", model.split("/", 1)[0] if "/" in model else "unknown")
        row.setdefault("popularity_rank", index)
        row.setdefault("official_intelligence_rank", index)
        row.setdefault("prompt_usd_per_million", row.get("prompt_price_per_million", 0.0) or 0.0)
        row.setdefault(
            "completion_usd_per_million",
            row.get("completion_price_per_million", 0.0) or 0.0,
        )
        row.setdefault("request_usd", 0.0)
        row.setdefault("context_length", 0)
        row.setdefault("max_completion_tokens", 0)
        row["provider_routing_mode"] = "unrestricted-openrouter"
        row["provider_restrictions_applied"] = False
        result.append(row)
        seen.add(model)
    if not result:
        raise Top50PoolOptimizationError("governance supplied no usable expert candidates")
    return result


def _dynamic_team_shape(profile: Mapping[str, Any], candidate_count: int) -> tuple[int, int]:
    """Derive team/recovery size from current-task structural load, not fixed counts."""
    pressure = profile.get("pressure")
    pressure_map = pressure if isinstance(pressure, Mapping) else {}
    overall = max(0, int(pressure_map.get("overall") or 0))
    structural = (
        1
        + int(profile.get("requirement_count") or 0)
        + int(profile.get("acceptance_count") or 0)
        + int(profile.get("delivery_item_count") or 0)
        + min(20, int(profile.get("evidence_count") or 0))
        + math.ceil(
            (int(profile.get("task_characters") or 0) + int(profile.get("evidence_characters") or 0))
            / 6000
        )
        + math.ceil(overall / 10)
    )
    primary = min(candidate_count, max(1, math.ceil(math.sqrt(max(1, structural)))))
    remaining = max(0, candidate_count - primary)
    resilience = min(
        1.0,
        0.10
        + overall / 150.0
        + min(20, int(profile.get("evidence_count") or 0)) / 100.0,
    )
    recovery = min(remaining, max(0, math.ceil(primary * resilience) - 1))
    return primary, recovery


def _role_plan(primary_count: int) -> list[dict[str, str]]:
    if primary_count <= 1:
        return [
            {
                "role_id": "synthesis",
                "role_kind": "synthesis",
                "role": "动态综合专家：独立完成当前任务的分析、审查与最终交付",
            }
        ]
    if primary_count == 2:
        return [
            {
                "role_id": "evidence",
                "role_kind": "independent",
                "role": "动态独立专家：形成第一份完整分析并检查证据与假设",
            },
            {
                "role_id": "synthesis",
                "role_kind": "synthesis",
                "role": "动态综合专家：审查前序结果并形成最终交付",
            },
        ]

    independent_count = max(1, primary_count - 2)
    roles: list[dict[str, str]] = []
    for index in range(independent_count):
        metric_role = "evidence" if index % 2 == 0 else "options"
        roles.append(
            {
                "role_id": f"independent-{index + 1}",
                "metric_role_id": metric_role,
                "role_kind": "independent",
                "role": f"动态独立专家{index + 1}：从不同角度分析证据、机制、方案、反例与不确定性",
            }
        )
    roles.extend(
        [
            {
                "role_id": "review",
                "role_kind": "review",
                "role": "动态交叉审查专家：比较全部前序分析并找出冲突、遗漏和失败模式",
            },
            {
                "role_id": "synthesis",
                "role_kind": "synthesis",
                "role": "动态最终综合专家：依据任务和全部前序结果形成唯一完整交付",
            },
        ]
    )
    return roles


def _metric_role(role: Mapping[str, Any]) -> str:
    value = str(role.get("metric_role_id") or role.get("role_id") or "evidence")
    return value if value in {"evidence", "options", "review", "synthesis"} else "evidence"


def _annotate(row: Mapping[str, Any], metric: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(row)
    value.update(
        {
            "estimated_task_cost_usd": float(metric.get("estimated_task_cost_usd") or 0.0),
            "task_adaptive_objective_score": int(metric.get("objective_score") or 0),
            "task_adaptive_ranks": dict(metric.get("ranks") or {}),
            "task_adaptive_weights": dict(metric.get("weights") or {}),
            "task_adaptive_role_tokens": dict(metric.get("role_tokens") or {}),
            "task_adaptive_capacity_compatible": bool(metric.get("compatible", True)),
            "marginal_cost_per_quality": float(metric.get("marginal_cost_per_quality") or 0.0),
        }
    )
    return value


def _solve(
    candidates: list[dict[str, Any]],
    profile: Mapping[str, Any],
    roles: Sequence[Mapping[str, str]],
    recovery_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    try:
        metrics_by_role = [
            build_role_metrics(candidates, profile, _metric_role(role)) for role in roles
        ]
        recovery_metrics = build_role_metrics(candidates, profile, RECOVERY_ROLE_ID)
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
        index: model.new_bool_var(f"recovery_{index}") for index in range(len(candidates))
    }

    for role_index in range(len(roles)):
        model.add(
            sum(active[index, role_index] for index in range(len(candidates))) == 1
        )

    # Exact-model duplication is prevented only because it would be the same expert
    # identity twice; company/provider diversity is intentionally not constrained.
    for index in range(len(candidates)):
        model.add(
            sum(active[index, role] for role in range(len(roles))) + recovery[index]
            <= 1
        )
    model.add(sum(recovery.values()) == int(recovery_count))

    terms: list[Any] = []
    for index, row in enumerate(candidates):
        model_id = str(row["model"])
        for role_index in range(len(roles)):
            metric = metrics_by_role[role_index][model_id]
            penalty = 0 if metric.get("compatible") is True else 1_000_000_000
            terms.append(
                (int(metric.get("objective_score") or 0) + penalty)
                * active[index, role_index]
            )
        recovery_metric = recovery_metrics[model_id]
        recovery_penalty = 0 if recovery_metric.get("compatible") is True else 1_000_000_000
        terms.append(
            (int(recovery_metric.get("objective_score") or 0) + recovery_penalty)
            * recovery[index]
        )
    model.minimize(sum(terms))

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.solve(model)
    accepted_statuses = {cp_model.OPTIMAL, cp_model.FEASIBLE}

    if status not in accepted_statuses:
        # Non-blocking heuristic fallback: best currently scored unique candidates.
        used: set[str] = set()
        selected: list[dict[str, Any]] = []
        for role_index, role in enumerate(roles):
            ranked = sorted(
                candidates,
                key=lambda row: (
                    not bool(metrics_by_role[role_index][str(row["model"])]["compatible"]),
                    int(metrics_by_role[role_index][str(row["model"])]["objective_score"]),
                    str(row["model"]),
                ),
            )
            source = next((row for row in ranked if str(row["model"]) not in used), ranked[0])
            used.add(str(source["model"]))
            selected.append(
                {
                    **_annotate(source, metrics_by_role[role_index][str(source["model"])]),
                    **dict(role),
                    "slot": role_index + 1,
                }
            )
        recovery_ranked = sorted(
            [row for row in candidates if str(row["model"]) not in used],
            key=lambda row: (
                not bool(recovery_metrics[str(row["model"])]["compatible"]),
                int(recovery_metrics[str(row["model"])]["objective_score"]),
                str(row["model"]),
            ),
        )
        backups = [
            {
                **_annotate(row, recovery_metrics[str(row["model"])]),
                "slot": index,
                "warm_recovery_priority": index,
            }
            for index, row in enumerate(recovery_ranked[:recovery_count], 1)
        ]
        return selected, backups, {
            "optimizer": "ortools-cp-sat-with-heuristic-fallback",
            "solver_status": solver.status_name(status),
            "optimality_proven": False,
            "fallback_used": True,
        }

    selected = []
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
                **_annotate(source, metrics_by_role[role_index][str(source["model"])]),
                **dict(role),
                "slot": role_index + 1,
            }
        )

    backups = []
    for index, row in enumerate(candidates):
        if solver.value(recovery[index]) == 1:
            backups.append(_annotate(row, recovery_metrics[str(row["model"])]))
    backups.sort(
        key=lambda row: (
            not bool(row.get("task_adaptive_capacity_compatible", True)),
            int(row.get("task_adaptive_objective_score") or 0),
            float(row.get("estimated_task_cost_usd") or 0.0),
            str(row.get("model") or ""),
        )
    )
    for index, row in enumerate(backups, 1):
        row["slot"] = index
        row["warm_recovery_priority"] = index

    return selected, backups, {
        "optimizer": "ortools-cp-sat",
        "solver_status": solver.status_name(status),
        "optimality_proven": status == cp_model.OPTIMAL,
        "fallback_used": False,
        "objective_value": float(solver.objective_value),
        "best_objective_bound": float(solver.best_objective_bound),
        "wall_time_seconds": round(float(solver.wall_time), 6),
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

    primary_count, recovery_count = _dynamic_team_shape(profile, len(candidates))
    roles = _role_plan(primary_count)
    selected, recoveries, solver_audit = _solve(
        candidates, profile, roles, recovery_count
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
        "schema_version": "v5-task-dynamic-expert-composition-1",
        "selection_principles": list(PRINCIPLES),
        "task_adaptive_scoring_schema_version": TASK_SCORING_SCHEMA_VERSION,
        "task_demand_profile": dict(profile),
        "primary_expert_count": len(selected),
        "recovery_count": len(recoveries),
        "role_plan": [dict(role) for role in roles],
        "fixed_team_size_used": False,
        "fixed_four_plus_four_used": False,
        "company_uniqueness_constraint_used": False,
        "top50_membership_constraint_used": False,
        "budget_constraint_used": False,
        "provider_constraint_used": False,
        "optimality_required_to_execute": False,
        "semantic_keyword_routing_used": False,
        "provider_routing_mode": "unrestricted-openrouter",
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
            "model_assignment_authority": "expert-assessment-center-ortools",
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
            "selection_policy": (
                "governance candidates -> current-task structural demand -> dynamic team size "
                "and roles -> OR-Tools task-value assignment -> feasible-or-heuristic fallback "
                "-> unrestricted OpenRouter Provider routing"
            ),
        }
    )
    plan.pop("plan_sha256", None)
    selection_basis_sha256 = _sha(plan)

    receipt = {
        "schema_version": "expert-center-task-dynamic-selection-receipt-v1",
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
