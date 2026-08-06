"""Task-adaptive Top-50 OR-Tools optimizer with unrestricted Provider routing."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_top50_pool_optimizer_legacy as _legacy
from v5_task_adaptive_scoring import (
    PRINCIPLES,
    RECOVERY_ROLE_ID,
    ROLE_IDS,
    SCHEMA_VERSION as TASK_SCORING_SCHEMA_VERSION,
    TaskAdaptiveScoringError,
    build_role_metrics,
    build_task_demand_profile,
)
from v5_top20_pool_selector import Top20PoolSelectionError

POOL_SCHEMA_VERSION = "governance-openrouter-top50-reasoning-pool-v2-open-provider"
REQUIRED_EVIDENCE = (
    "openrouter-top-weekly-reasoning",
    "model-metadata-qualified",
    "unrestricted-openrouter-provider-routing",
)
PRIMARY_COUNT = 4
WARM_RECOVERY_COUNT = 4
MINIMUM_TOP50_CALLS = PRIMARY_COUNT + WARM_RECOVERY_COUNT

Top50PoolOptimizationError = _legacy.Top50PoolOptimizationError


def _open_validate_pool(
    plan: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected = {
        "top50_reasoning_pool_schema_version": POOL_SCHEMA_VERSION,
        "top50_reasoning_pool_source": _legacy.POOL_SOURCE,
        "top50_reasoning_pool_period": _legacy.POOL_PERIOD,
        "top50_reasoning_pool_size": _legacy.POOL_SIZE,
        "top50_candidate_pool_authority": "decision-system-governance",
        "top50_model_assignment_authority": "expert-assessment-center-ortools",
        "expert_center_top50_pool_selection_allowed": True,
        "top50_provider_routing_mode": "unrestricted-openrouter",
        "top50_provider_restrictions_applied": False,
        "top50_provider_endpoint_qualification_required": False,
        "top50_zdr_provider_qualification_required": False,
        "top50_old_flagship_filter_applied": False,
        "top50_model_calls": 0,
    }
    for field, required in expected.items():
        if plan.get(field) != required:
            raise Top50PoolOptimizationError(f"top-50 contract mismatch: {field}")

    raw = _legacy._rows(plan.get("top50_reasoning_models"), "top50_reasoning_models")
    eligible = _legacy._rows(
        plan.get("top50_expert_selectable_candidates"),
        "top50_expert_selectable_candidates",
    )
    if len(raw) != _legacy.POOL_SIZE or plan.get("top50_reasoning_pool_sha256") != _legacy._sha(raw):
        raise Top50PoolOptimizationError("top-50 raw pool is incomplete or corrupted")
    if plan.get("top50_expert_selectable_candidates_sha256") != _legacy._sha(eligible):
        raise Top50PoolOptimizationError("top-50 selectable pool hash mismatch")

    raw_models: set[str] = set()
    for rank, row in enumerate(raw, 1):
        model = str(row.get("model") or "").strip()
        company = str(row.get("company") or "").strip().casefold()
        if row.get("popularity_rank") != rank:
            raise Top50PoolOptimizationError("top-50 popularity ranks must be contiguous")
        if not model or "/" not in model or not company or model in raw_models:
            raise Top50PoolOptimizationError("top-50 pool contains an invalid identity")
        if row.get("reasoning_supported") is not True:
            raise Top50PoolOptimizationError("top-50 pool contains a non-reasoning model")
        raw_models.add(model)

    seen: set[str] = set()
    companies: set[str] = set()
    for index, row in enumerate(eligible):
        model = str(row.get("model") or "").strip()
        company = str(row.get("company") or "").strip().casefold()
        if model not in raw_models or model in seen or not company:
            raise Top50PoolOptimizationError("top-50 selectable identity is invalid")
        rank = _legacy._positive_int(row.get("popularity_rank"), f"candidate[{index}].popularity_rank")
        if rank > _legacy.POOL_SIZE:
            raise Top50PoolOptimizationError("candidate popularity rank exceeds top-50")
        _legacy._finite(row.get("price_rank_usd_per_million"), f"candidate[{index}].price")
        if row.get("reasoning_rank_verified") is not True:
            raise Top50PoolOptimizationError(f"reasoning rank not verified: {model}")
        if row.get("provider_routing_mode") != "unrestricted-openrouter":
            raise Top50PoolOptimizationError(f"provider routing is restricted: {model}")
        if row.get("provider_restrictions_applied") is not False:
            raise Top50PoolOptimizationError(f"provider restrictions detected: {model}")
        evidence = str(row.get("selection_evidence") or "")
        if any(fragment not in evidence for fragment in REQUIRED_EVIDENCE):
            raise Top50PoolOptimizationError(f"candidate evidence incomplete: {model}")
        seen.add(model)
        companies.add(company)
    if len(companies) < PRIMARY_COUNT + WARM_RECOVERY_COUNT:
        raise Top50PoolOptimizationError("top-50 pool has fewer than eight distinct executable companies")
    if plan.get("top50_expert_selectable_distinct_company_count") != len(companies):
        raise Top50PoolOptimizationError("top-50 distinct-company count mismatch")
    return raw, eligible


def _validate_top50_budget(packet: Mapping[str, Any]) -> None:
    budget = packet.get("approved_budget")
    if not isinstance(budget, Mapping):
        raise Top50PoolOptimizationError("approved_budget is required for top-50 assignment")
    calls = budget.get("calls")
    recovery = budget.get("maximum_recovery_calls")
    if isinstance(calls, bool) or not isinstance(calls, int) or not MINIMUM_TOP50_CALLS <= calls <= 16:
        raise Top50PoolOptimizationError("top-50 approved calls must be between 8 and 16")
    if recovery != WARM_RECOVERY_COUNT:
        raise Top50PoolOptimizationError("top-50 maximum_recovery_calls must equal four")
    if calls - WARM_RECOVERY_COUNT < PRIMARY_COUNT:
        raise Top50PoolOptimizationError("top-50 budget must leave four primary expert calls")


def _annotate(
    row: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    value = dict(row)
    value["estimated_task_cost_usd"] = float(metrics["estimated_task_cost_usd"])
    value["task_adaptive_role_id"] = str(metrics["role_id"])
    value["task_adaptive_objective_score"] = int(metrics["objective_score"])
    value["task_adaptive_ranks"] = dict(metrics["ranks"])
    value["task_adaptive_weights"] = dict(metrics["weights"])
    value["task_adaptive_role_tokens"] = dict(metrics["role_tokens"])
    value["task_adaptive_capacity_compatible"] = bool(metrics["compatible"])
    value["marginal_cost_per_quality"] = float(metrics["marginal_cost_per_quality"])
    return value


def _solve_dynamic(
    candidates: list[dict[str, Any]],
    profile: Mapping[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, dict[str, Any]]],
    dict[str, dict[str, Any]],
]:
    try:
        role_metrics = [
            build_role_metrics(candidates, profile, role_id)
            for role_id in ROLE_IDS
        ]
        recovery_metrics = build_role_metrics(candidates, profile, RECOVERY_ROLE_ID)
    except TaskAdaptiveScoringError as exc:
        raise Top50PoolOptimizationError(str(exc)) from exc

    model = _legacy.cp_model.CpModel()
    active = {
        (index, role): model.new_bool_var(f"active_{index}_{role}")
        for index in range(len(candidates))
        for role in range(PRIMARY_COUNT)
    }
    recovery = {
        index: model.new_bool_var(f"recovery_{index}")
        for index in range(len(candidates))
    }

    for role in range(PRIMARY_COUNT):
        model.add(sum(active[index, role] for index in range(len(candidates))) == 1)
    for index, row in enumerate(candidates):
        model_id = str(row["model"])
        model.add(
            sum(active[index, role] for role in range(PRIMARY_COUNT)) + recovery[index] <= 1
        )
        for role in range(PRIMARY_COUNT):
            if role_metrics[role][model_id]["compatible"] is not True:
                model.add(active[index, role] == 0)
        if recovery_metrics[model_id]["compatible"] is not True:
            model.add(recovery[index] == 0)
    model.add(sum(recovery.values()) == WARM_RECOVERY_COUNT)

    by_company: dict[str, list[int]] = {}
    for index, row in enumerate(candidates):
        by_company.setdefault(str(row["company"]).casefold(), []).append(index)
    for indices in by_company.values():
        model.add(
            sum(active[index, role] for index in indices for role in range(PRIMARY_COUNT))
            + sum(recovery[index] for index in indices)
            <= 1
        )

    terms = []
    for index, row in enumerate(candidates):
        model_id = str(row["model"])
        for role in range(PRIMARY_COUNT):
            terms.append(
                int(role_metrics[role][model_id]["objective_score"])
                * active[index, role]
            )
        terms.append(
            int(recovery_metrics[model_id]["objective_score"]) * recovery[index]
        )
    model.minimize(sum(terms))

    solver = _legacy.cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.solve(model)
    if status != _legacy.cp_model.OPTIMAL:
        raise Top50PoolOptimizationError(
            f"OR-Tools did not prove an optimal task-adaptive assignment: {solver.status_name(status)}"
        )

    selected: list[dict[str, Any]] = []
    role_audit: list[dict[str, Any]] = []
    for role in range(PRIMARY_COUNT):
        matches = [
            index
            for index in range(len(candidates))
            if solver.value(active[index, role]) == 1
        ]
        if len(matches) != 1:
            raise Top50PoolOptimizationError("invalid OR-Tools active assignment")
        index = matches[0]
        source = candidates[index]
        metric = role_metrics[role][str(source["model"])]
        selected.append(_annotate(source, metric))
        role_audit.append(
            {
                "role_id": ROLE_IDS[role],
                "model": source["model"],
                "company": source["company"],
                "objective_score": metric["objective_score"],
                "estimated_task_cost_usd": metric["estimated_task_cost_usd"],
                "ranks": metric["ranks"],
                "weights": metric["weights"],
                "role_tokens": metric["role_tokens"],
            }
        )

    backups: list[dict[str, Any]] = []
    for index, row in enumerate(candidates):
        if solver.value(recovery[index]) == 1:
            metric = recovery_metrics[str(row["model"])]
            backups.append(_annotate(row, metric))
    backups.sort(
        key=lambda row: (
            int(row["task_adaptive_objective_score"]),
            float(row["estimated_task_cost_usd"]),
            int(row.get("popularity_rank") or 1_000_000),
            str(row.get("model") or ""),
        )
    )
    for slot, row in enumerate(backups, 1):
        row["slot"] = slot
        row["warm_recovery_priority"] = slot

    audit = {
        "optimizer": "ortools-cp-sat",
        "solver_status": solver.status_name(status),
        "objective_value": int(round(solver.objective_value)),
        "best_objective_bound": int(round(solver.best_objective_bound)),
        "wall_time_seconds": round(float(solver.wall_time), 6),
        "deterministic_workers": 1,
        "random_seed": 0,
        "optimality_proven": True,
        "selection_principles": list(PRINCIPLES),
        "task_adaptive_scoring_schema_version": TASK_SCORING_SCHEMA_VERSION,
        "task_demand_profile": dict(profile),
        "role_assignments": role_audit,
        "warm_recovery_priority": [
            {
                "priority": index,
                "model": row["model"],
                "objective_score": row["task_adaptive_objective_score"],
                "estimated_task_cost_usd": row["estimated_task_cost_usd"],
            }
            for index, row in enumerate(backups, 1)
        ],
        "objective_components": [
            "role-aware-estimated-task-cost-rank",
            "dynamic-local-reasoning-intelligence-rank",
            "weekly-popularity-rank",
            "task-specific-native-capacity-headroom-rank",
            "marginal-cost-per-relative-quality-rank",
        ],
        "objective_strategy": (
            "current-task structural demand -> dynamic role weights -> "
            "small-effort-large-return cost-quality tradeoff"
        ),
        "constraints": {
            "four_role_slots": True,
            "four_warm_recovery_slots": True,
            "global_model_uniqueness": True,
            "global_company_uniqueness": True,
            "weekly_popularity_rank_used": True,
            "intelligence_rank_used": True,
            "estimated_task_cost_used": True,
            "task_role_native_capacity_compatibility": True,
            "dynamic_role_weights_used": True,
            "marginal_return_used": True,
            "semantic_keyword_routing_used": False,
            "cross_task_history_used": False,
            "provider_resilience_used": False,
            "provider_routing_unrestricted": True,
            "four_primary_calls_reserved": True,
            "four_warm_recovery_calls_reserved": True,
            "warm_recovery_priority_uses_same_objective": True,
        },
        "provider_objective_weight": 0,
        "provider_routing_mode": "unrestricted-openrouter",
    }
    return selected, backups, audit, role_metrics, recovery_metrics


def _selected(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for slot, (row, role) in enumerate(zip(rows, _legacy.ROLES, strict=True), 1):
        result.append({**_legacy._base(row), **role, "slot": slot})
    return result


def _task_cost_order(
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(
        [*selected, *recoveries],
        key=lambda row: (
            float(row.get("estimated_task_cost_usd") or 0.0),
            int(row.get("task_adaptive_objective_score") or 1_000_000),
            str(row.get("model") or ""),
        ),
    )
    return [
        {**_legacy._base(row), "slot": rank, "price_rank": rank}
        for rank, row in enumerate(ordered, 1)
    ]


def _inventory(
    raw: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
    recovery_metrics: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    selected_ids = {str(row["model"]) for row in selected}
    recovery_ids = {str(row["model"]) for row in recoveries}
    recovery_priority = {
        str(row["model"]): int(row.get("warm_recovery_priority") or 0)
        for row in recoveries
    }
    candidate_map = {str(row["model"]): _legacy._base(row) for row in candidates}
    remaining = [
        row for row in candidates if str(row["model"]) not in selected_ids | recovery_ids
    ]
    remaining.sort(
        key=lambda row: (
            int(recovery_metrics[str(row["model"])]["objective_score"]),
            float(recovery_metrics[str(row["model"])]["estimated_task_cost_usd"]),
            int(row.get("popularity_rank") or 1_000_000),
            str(row.get("model") or ""),
        )
    )
    standby_priority = {
        str(row["model"]): index for index, row in enumerate(remaining, 1)
    }

    inventory: list[dict[str, Any]] = []
    for slot, raw_row in enumerate(raw, 1):
        model_id = str(raw_row["model"])
        qualified = candidate_map.get(model_id)
        if model_id in selected_ids:
            state = "active"
        elif model_id in recovery_ids:
            state = "warm-recovery"
        elif qualified is not None:
            state = "ordered-standby"
        else:
            state = "ineligible-standby"
        record: dict[str, Any] = {
            **dict(raw_row),
            "pool_slot": slot,
            "standby_state": state,
            "execution_eligible": qualified is not None,
            "assigned_for_current_run": state == "active",
            "callable_under_current_recovery_ceiling": state == "warm-recovery",
            "retained_by_expert_center": True,
            "standby_priority": standby_priority.get(model_id),
            "warm_recovery_priority": recovery_priority.get(model_id),
        }
        if qualified is not None:
            metric = recovery_metrics[model_id]
            record["qualified_candidate"] = qualified
            record["task_adaptive_recovery_objective_score"] = int(metric["objective_score"])
            record["task_adaptive_recovery_estimated_cost_usd"] = float(
                metric["estimated_task_cost_usd"]
            )
            record["task_adaptive_recovery_capacity_compatible"] = bool(
                metric["compatible"]
            )
        inventory.append(record)
    return inventory


def materialize_top50_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _validate_top50_budget(packet)
    source = packet.get("governance_model_plan")
    if not isinstance(source, Mapping):
        raise Top50PoolOptimizationError("governance_model_plan is missing")
    source_plan = dict(source)
    raw, candidates = _open_validate_pool(source_plan)
    try:
        profile = build_task_demand_profile(packet, candidates)
    except TaskAdaptiveScoringError as exc:
        raise Top50PoolOptimizationError(str(exc)) from exc

    active_rows, backup_rows, audit, _, recovery_metrics = _solve_dynamic(
        candidates, profile
    )
    selected = _selected(active_rows)
    recoveries = [dict(row) for row in backup_rows]
    price_ranked = _task_cost_order(selected, recoveries)
    inventory = _inventory(raw, candidates, selected, recoveries, recovery_metrics)
    counts = {
        state: sum(row["standby_state"] == state for row in inventory)
        for state in (
            "active",
            "warm-recovery",
            "ordered-standby",
            "ineligible-standby",
        )
    }

    audit["approved_total_calls"] = int(packet["approved_budget"]["calls"])
    audit["approved_recovery_calls"] = WARM_RECOVERY_COUNT

    plan = dict(source_plan)
    plan.update(
        {
            "selected_models": selected,
            "recovery_models": recoveries,
            "price_ranked_models": price_ranked,
            "expert_count": PRIMARY_COUNT,
            "recovery_count": WARM_RECOVERY_COUNT,
            "expert_center_pool_selection_completed": True,
            "expert_center_top50_optimization_completed": True,
            "expert_center_reranking_allowed": False,
            "model_substitution_allowed": False,
            "selected_from_top20_reasoning_pool_only": False,
            "selected_from_top50_reasoning_pool_only": True,
            "source_governance_pool_plan_sha256": str(source_plan.get("plan_sha256") or ""),
            "candidate_pool_authority": "decision-system-governance",
            "model_assignment_authority": "expert-assessment-center-ortools",
            "all_top50_models_received_by_expert_center": True,
            "expert_center_top50_inventory_schema_version": (
                "expert-center-top50-task-adaptive-standby-inventory-v2"
            ),
            "expert_center_top50_inventory": inventory,
            "expert_center_top50_inventory_sha256": _legacy._sha(inventory),
            "expert_center_top50_inventory_count": len(inventory),
            "expert_center_top50_inventory_state_counts": counts,
            "expert_center_ordered_standby_count": counts["ordered-standby"],
            "optimizer": "ortools-cp-sat",
            "optimizer_version_contract": "ortools-9.15.6755",
            "optimizer_audit": audit,
            "task_adaptive_scoring_completed": True,
            "task_adaptive_scoring_schema_version": TASK_SCORING_SCHEMA_VERSION,
            "task_demand_profile": profile,
            "selection_principles": list(PRINCIPLES),
            "price_rank_basis": "role-aware-estimated-current-task-usd",
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "selection_policy": (
                "weekly-top50-reasoning -> model-metadata-qualified -> current-task-"
                "structural-demand-profile -> role-aware-estimated-task-cost-and-native-"
                "capacity -> dynamic-cost-quality-popularity-marginal-return-weights -> "
                "ortools-four-active-four-warm-recovery -> same-objective-recovery-order -> "
                "all-extra-qualified-models-as-ordered-standby -> unrestricted-openrouter-"
                "provider-routing"
            ),
            "popularity_window_policy": (
                "week-primary; day-excluded-as-noisy; month-excluded-as-lagging"
            ),
        }
    )

    receipt = {
        "schema_version": "expert-center-top50-ortools-selection-receipt-v1",
        "candidate_pool_plan_sha256": str(source_plan.get("plan_sha256") or ""),
        "candidate_pool_sha256": source_plan["top50_reasoning_pool_sha256"],
        "selected_models": [row["model"] for row in selected],
        "recovery_models": [row["model"] for row in recoveries],
        "inventory_count": len(inventory),
        "inventory_sha256": plan["expert_center_top50_inventory_sha256"],
        "optimizer_audit": audit,
        "task_adaptive_scoring_schema_version": TASK_SCORING_SCHEMA_VERSION,
        "selection_principles": list(PRINCIPLES),
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "approved_total_calls": int(packet["approved_budget"]["calls"]),
        "approved_recovery_calls": WARM_RECOVERY_COUNT,
        "warm_recovery_order_basis": "same-task-adaptive-recovery-objective",
        "model_calls": 0,
    }
    receipt["receipt_sha256"] = _legacy._sha(receipt)
    plan["expert_center_selection_receipt"] = receipt
    plan["plan_sha256"] = _legacy._plan_sha(plan)

    materialized = dict(packet)
    materialized["governance_model_plan"] = plan
    return materialized, receipt


def materialize_candidate_pool_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = packet.get("governance_model_plan")
    if isinstance(plan, Mapping) and plan.get("top50_reasoning_pool_size") == 50:
        return materialize_top50_selection(packet)
    try:
        return _legacy.materialize_top20_selection(packet)
    except Top20PoolSelectionError as exc:
        raise Top50PoolOptimizationError(f"legacy top-20 rollback selection failed: {exc}") from exc


__all__ = [
    "Top50PoolOptimizationError",
    "materialize_candidate_pool_selection",
    "materialize_top50_selection",
]
