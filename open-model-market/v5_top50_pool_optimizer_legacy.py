#!/usr/bin/env python3
"""OR-Tools assignment for a governance-frozen top-50 reasoning pool."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

from v5_top20_pool_selector import materialize_top20_selection

POOL_SCHEMA_VERSION = "governance-openrouter-top50-reasoning-pool-v1"
POOL_SOURCE = "openrouter-most-popular-last-week-token-volume"
POOL_PERIOD = "week"
POOL_SIZE = 50
PRIMARY_COUNT = 4
RECOVERY_COUNT = 4
REQUIRED_EVIDENCE = (
    "openrouter-top-weekly-reasoning",
    "live-exact-endpoint-qualified",
    "authenticated-zdr-endpoint-qualified",
)
ROLES = (
    {
        "role_id": "evidence",
        "role_kind": "independent",
        "role": "独立分析专家：检查证据、事实、数据质量、关键假设与不确定性",
    },
    {
        "role_id": "options",
        "role_kind": "independent",
        "role": "独立分析专家：检查备选方案、机制、因果链与反事实",
    },
    {
        "role_id": "review",
        "role_kind": "review",
        "role": "交叉审查专家：比较前序分析，识别冲突、遗漏、薄弱证据和失败模式",
    },
    {
        "role_id": "synthesis",
        "role_kind": "synthesis",
        "role": "最终综合专家：依据任务和全部前序结果形成唯一完整交付",
    },
)


class Top50PoolOptimizationError(RuntimeError):
    """Raised when the frozen top-50 pool cannot produce a valid team."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _plan_sha(plan: Mapping[str, Any]) -> str:
    value = dict(plan)
    value.pop("plan_sha256", None)
    return _sha(value)


def _rows(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise Top50PoolOptimizationError(f"{field} must be an array")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise Top50PoolOptimizationError(f"{field}[{index}] must be an object")
        rows.append(dict(row))
    return rows


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise Top50PoolOptimizationError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise Top50PoolOptimizationError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise Top50PoolOptimizationError(f"{field} must be finite and nonnegative")
    return number


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise Top50PoolOptimizationError(f"{field} must be a positive integer")
    return value


def _validate_pool(plan: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected = {
        "top50_reasoning_pool_schema_version": POOL_SCHEMA_VERSION,
        "top50_reasoning_pool_source": POOL_SOURCE,
        "top50_reasoning_pool_period": POOL_PERIOD,
        "top50_reasoning_pool_size": POOL_SIZE,
        "top50_candidate_pool_authority": "decision-system-governance",
        "top50_model_assignment_authority": "expert-assessment-center-ortools",
        "expert_center_top50_pool_selection_allowed": True,
        "top50_old_flagship_filter_applied": False,
        "top50_model_calls": 0,
    }
    for field, required in expected.items():
        if plan.get(field) != required:
            raise Top50PoolOptimizationError(f"top-50 contract mismatch: {field}")

    raw = _rows(plan.get("top50_reasoning_models"), "top50_reasoning_models")
    eligible = _rows(
        plan.get("top50_expert_selectable_candidates"),
        "top50_expert_selectable_candidates",
    )
    if len(raw) != POOL_SIZE or plan.get("top50_reasoning_pool_sha256") != _sha(raw):
        raise Top50PoolOptimizationError("top-50 raw pool is incomplete or corrupted")
    if plan.get("top50_expert_selectable_candidates_sha256") != _sha(eligible):
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
        rank = _positive_int(row.get("popularity_rank"), f"candidate[{index}].popularity_rank")
        if rank > POOL_SIZE:
            raise Top50PoolOptimizationError("candidate popularity rank exceeds top-50")
        _finite(row.get("price_rank_usd_per_million"), f"candidate[{index}].price")
        _positive_int(row.get("qualified_provider_count"), f"candidate[{index}].providers")
        if row.get("reasoning_rank_verified") is not True:
            raise Top50PoolOptimizationError(f"reasoning rank not verified: {model}")
        evidence = str(row.get("selection_evidence") or "")
        if any(fragment not in evidence for fragment in REQUIRED_EVIDENCE):
            raise Top50PoolOptimizationError(f"candidate evidence incomplete: {model}")
        seen.add(model)
        companies.add(company)
    if len(companies) < PRIMARY_COUNT + RECOVERY_COUNT:
        raise Top50PoolOptimizationError(
            "top-50 pool has fewer than eight distinct executable companies"
        )
    if plan.get("top50_expert_selectable_distinct_company_count") != len(companies):
        raise Top50PoolOptimizationError("top-50 distinct-company count mismatch")
    return raw, eligible


def _price_ranks(candidates: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    ordered = sorted(
        candidates,
        key=lambda row: (
            _finite(row.get("price_rank_usd_per_million"), "candidate price"),
            int(row.get("popularity_rank") or 1_000_000),
            int(row.get("official_intelligence_rank") or 1_000_000),
            str(row.get("model") or ""),
        ),
    )
    return {str(row["model"]): index for index, row in enumerate(ordered, 1)}


def _metrics(row: Mapping[str, Any], prices: Mapping[str, int]) -> tuple[int, int, int, int]:
    price = prices[str(row["model"])]
    popularity = min(10_000, int(row.get("popularity_rank") or 10_000))
    intelligence = min(10_000, int(row.get("official_intelligence_rank") or 10_000))
    providers = max(1, int(row.get("qualified_provider_count") or 1))
    fragility = max(0, 6 - min(providers, 6))
    return price, popularity, intelligence, fragility


def _active_cost(row: Mapping[str, Any], role: int, prices: Mapping[str, int]) -> int:
    price, popularity, intelligence, fragility = _metrics(row, prices)
    if role == 3:
        return 18 * price + 22 * popularity + 70 * intelligence + 30 * fragility
    if role == 2:
        return 25 * price + 25 * popularity + 50 * intelligence + 45 * fragility
    return 45 * price + 35 * popularity + 20 * intelligence + 35 * fragility


def _recovery_cost(row: Mapping[str, Any], prices: Mapping[str, int]) -> int:
    price, popularity, intelligence, fragility = _metrics(row, prices)
    return 45 * price + 30 * popularity + 20 * intelligence + 55 * fragility


def _solve(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    prices = _price_ranks(candidates)
    model = cp_model.CpModel()
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
    for index in range(len(candidates)):
        model.add(
            sum(active[index, role] for role in range(PRIMARY_COUNT)) + recovery[index] <= 1
        )
    model.add(sum(recovery.values()) == RECOVERY_COUNT)

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
        terms.extend(
            _active_cost(row, role, prices) * active[index, role]
            for role in range(PRIMARY_COUNT)
        )
        terms.append(_recovery_cost(row, prices) * recovery[index])
    model.minimize(sum(terms))

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.solve(model)
    if status != cp_model.OPTIMAL:
        raise Top50PoolOptimizationError(
            f"OR-Tools did not prove an optimal assignment: {solver.status_name(status)}"
        )

    selected: list[dict[str, Any]] = []
    for role in range(PRIMARY_COUNT):
        matches = [
            dict(candidates[index])
            for index in range(len(candidates))
            if solver.value(active[index, role]) == 1
        ]
        if len(matches) != 1:
            raise Top50PoolOptimizationError("invalid OR-Tools active assignment")
        selected.append(matches[0])
    backups = [
        dict(candidates[index])
        for index in range(len(candidates))
        if solver.value(recovery[index]) == 1
    ]
    backups.sort(
        key=lambda row: (
            _finite(row.get("price_rank_usd_per_million"), "recovery price"),
            int(row.get("popularity_rank") or 1_000_000),
            str(row.get("model") or ""),
        )
    )
    audit = {
        "optimizer": "ortools-cp-sat",
        "solver_status": solver.status_name(status),
        "objective_value": int(round(solver.objective_value)),
        "best_objective_bound": int(round(solver.best_objective_bound)),
        "wall_time_seconds": round(float(solver.wall_time), 6),
        "deterministic_workers": 1,
        "random_seed": 0,
        "optimality_proven": True,
        "constraints": {
            "four_role_slots": True,
            "four_warm_recovery_slots": True,
            "global_model_uniqueness": True,
            "global_company_uniqueness": True,
            "weekly_popularity_rank_used": True,
            "intelligence_rank_used": True,
            "price_rank_used": True,
            "provider_resilience_used": True,
        },
    }
    return selected, backups, audit


def _base(row: Mapping[str, Any]) -> dict[str, Any]:
    omitted = {"slot", "price_rank", "role", "role_id", "role_kind"}
    return {key: value for key, value in row.items() if key not in omitted}


def _selected(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for slot, (row, role) in enumerate(zip(rows, ROLES, strict=True), 1):
        result.append({**_base(row), **role, "slot": slot})
    return result


def _recoveries(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{**_base(row), "slot": slot} for slot, row in enumerate(rows, 1)]


def _price_order(
    selected: Sequence[Mapping[str, Any]], recoveries: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    ordered = sorted(
        [*selected, *recoveries],
        key=lambda row: (
            _finite(row.get("price_rank_usd_per_million"), "assigned price"),
            int(row.get("popularity_rank") or 1_000_000),
            str(row.get("model") or ""),
        ),
    )
    return [
        {**_base(row), "slot": rank, "price_rank": rank}
        for rank, row in enumerate(ordered, 1)
    ]


def _inventory(
    raw: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    selected_ids = {str(row["model"]) for row in selected}
    recovery_ids = {str(row["model"]) for row in recoveries}
    candidate_map = {str(row["model"]): _base(row) for row in candidates}
    prices = _price_ranks(candidates)
    remaining = [
        row for row in candidates if str(row["model"]) not in selected_ids | recovery_ids
    ]
    remaining.sort(
        key=lambda row: (
            _recovery_cost(row, prices),
            int(row.get("popularity_rank") or 1_000_000),
            str(row.get("model") or ""),
        )
    )
    priority = {str(row["model"]): index for index, row in enumerate(remaining, 1)}

    inventory: list[dict[str, Any]] = []
    for slot, raw_row in enumerate(raw, 1):
        model = str(raw_row["model"])
        qualified = candidate_map.get(model)
        if model in selected_ids:
            state = "active"
        elif model in recovery_ids:
            state = "warm-recovery"
        elif qualified is not None:
            state = "ordered-standby"
        else:
            state = "ineligible-standby"
        record = {
            **dict(raw_row),
            "pool_slot": slot,
            "standby_state": state,
            "execution_eligible": qualified is not None,
            "assigned_for_current_run": state == "active",
            "callable_under_current_recovery_ceiling": state == "warm-recovery",
            "retained_by_expert_center": True,
            "standby_priority": priority.get(model),
        }
        if qualified is not None:
            record["qualified_candidate"] = qualified
        inventory.append(record)
    return inventory


def materialize_top50_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = packet.get("governance_model_plan")
    if not isinstance(source, Mapping):
        raise Top50PoolOptimizationError("governance_model_plan is missing")
    source_plan = dict(source)
    raw, candidates = _validate_pool(source_plan)
    active_rows, backup_rows, audit = _solve(candidates)
    selected = _selected(active_rows)
    recoveries = _recoveries(backup_rows)
    price_ranked = _price_order(selected, recoveries)
    inventory = _inventory(raw, candidates, selected, recoveries)
    counts = {
        state: sum(row["standby_state"] == state for row in inventory)
        for state in (
            "active",
            "warm-recovery",
            "ordered-standby",
            "ineligible-standby",
        )
    }

    plan = dict(source_plan)
    plan.update(
        {
            "selected_models": selected,
            "recovery_models": recoveries,
            "price_ranked_models": price_ranked,
            "expert_count": PRIMARY_COUNT,
            "recovery_count": RECOVERY_COUNT,
            "expert_center_pool_selection_completed": True,
            "expert_center_top50_optimization_completed": True,
            "expert_center_reranking_allowed": False,
            "model_substitution_allowed": False,
            "selected_from_top20_reasoning_pool_only": False,
            "selected_from_top50_reasoning_pool_only": True,
            "source_governance_pool_plan_sha256": source_plan["plan_sha256"],
            "candidate_pool_authority": "decision-system-governance",
            "model_assignment_authority": "expert-assessment-center-ortools",
            "all_top50_models_received_by_expert_center": True,
            "expert_center_top50_inventory_schema_version": (
                "expert-center-top50-standby-inventory-v1"
            ),
            "expert_center_top50_inventory": inventory,
            "expert_center_top50_inventory_sha256": _sha(inventory),
            "expert_center_top50_inventory_count": len(inventory),
            "expert_center_top50_inventory_state_counts": counts,
            "expert_center_ordered_standby_count": counts["ordered-standby"],
            "optimizer": "ortools-cp-sat",
            "optimizer_version_contract": "ortools-9.15.6755",
            "optimizer_audit": audit,
            "selection_policy": (
                "weekly-top50-reasoning -> governance-endpoint-qualified -> "
                "ortools-four-active-four-warm-recovery -> all-extra-qualified-"
                "models-as-ordered-standby"
            ),
            "popularity_window_policy": (
                "week-primary; day-excluded-as-noisy; month-excluded-as-lagging"
            ),
        }
    )
    receipt = {
        "schema_version": "expert-center-top50-ortools-selection-receipt-v1",
        "candidate_pool_plan_sha256": source_plan["plan_sha256"],
        "candidate_pool_sha256": source_plan["top50_reasoning_pool_sha256"],
        "selected_models": [row["model"] for row in selected],
        "recovery_models": [row["model"] for row in recoveries],
        "inventory_count": len(inventory),
        "inventory_sha256": _sha(inventory),
        "optimizer_audit": audit,
        "model_calls": 0,
    }
    receipt["receipt_sha256"] = _sha(receipt)
    plan["expert_center_selection_receipt"] = receipt
    plan["plan_sha256"] = _plan_sha(plan)
    materialized = dict(packet)
    materialized["governance_model_plan"] = plan
    return materialized, receipt


def materialize_candidate_pool_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prefer top-50 OR-Tools assignment; preserve top-20 rollback compatibility."""
    plan = packet.get("governance_model_plan")
    if isinstance(plan, Mapping) and plan.get("top50_reasoning_pool_size") == POOL_SIZE:
        return materialize_top50_selection(packet)
    return materialize_top20_selection(packet)


__all__ = [
    "Top50PoolOptimizationError",
    "materialize_candidate_pool_selection",
    "materialize_top50_selection",
]
