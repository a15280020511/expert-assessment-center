#!/usr/bin/env python3
"""Optimize an expert team from a frozen governance top-50 reasoning pool.

The historical filename and public aliases are retained for compatibility.
Governance owns the immutable popularity ranking. The expert center uses
OR-Tools CP-SAT to assign four active roles, exposes only the ticket-approved
number of warm replacements to the current execution graph, and retains every
other qualified model as ordered extended recovery inventory.
"""
from __future__ import annotations

import hashlib
import json
import math
from importlib.metadata import version as package_version
from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

POOL_SCHEMA_VERSION = "governance-openrouter-top50-reasoning-pool-v1"
POOL_SOURCE = "openrouter-most-popular-last-week-token-volume"
STANDBY_SCHEMA_VERSION = "expert-center-top50-standby-inventory-v1"
EXPECTED_POOL_SIZE = 50
EXPECTED_PRIMARY_COUNT = 4
ROLE_ORDER = ("evidence", "options", "review", "synthesis")
REQUIRED_SELECTION_EVIDENCE = (
    "openrouter-top-weekly-reasoning",
    "live-exact-endpoint-qualified",
    "authenticated-zdr-endpoint-qualified",
)


class Top50PoolOptimizationError(RuntimeError):
    """Raised when a safe deterministic assignment cannot be formed."""


Top20PoolSelectionError = Top50PoolOptimizationError


def _stable_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_bytes(value)).hexdigest()


def _plan_digest(plan: Mapping[str, Any]) -> str:
    material = dict(plan)
    material.pop("plan_sha256", None)
    return _sha256(material)


def _finite_nonnegative(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise Top50PoolOptimizationError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise Top50PoolOptimizationError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise Top50PoolOptimizationError(f"{field} must be finite and nonnegative")
    return number


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise Top50PoolOptimizationError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise Top50PoolOptimizationError(f"{field} must be an integer") from exc
    if parsed < 0:
        raise Top50PoolOptimizationError(f"{field} must be nonnegative")
    return parsed


def _rows(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise Top50PoolOptimizationError(f"{field} must be an array")
    result: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise Top50PoolOptimizationError(f"{field}[{index}] must be an object")
        result.append(dict(row))
    return result


def _require_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise Top50PoolOptimizationError(message)


def _validate_plan_envelope(plan: Mapping[str, Any]) -> None:
    checks = (
        (plan.get("plan_sha256"), _plan_digest(plan), "candidate plan digest mismatch"),
        (
            plan.get("top50_reasoning_pool_schema_version"),
            POOL_SCHEMA_VERSION,
            "top-50 reasoning pool schema is unsupported",
        ),
        (
            plan.get("top50_reasoning_pool_source"),
            POOL_SOURCE,
            "top-50 reasoning pool source is invalid",
        ),
        (
            plan.get("top50_reasoning_pool_size"),
            EXPECTED_POOL_SIZE,
            "top-50 reasoning pool size is not 50",
        ),
        (
            plan.get("candidate_pool_authority"),
            "decision-system-governance",
            "candidate pool authority is invalid",
        ),
        (
            plan.get("model_assignment_authority"),
            "expert-assessment-center",
            "model assignment authority is invalid",
        ),
        (
            plan.get("expert_center_pool_selection_allowed"),
            True,
            "expert center pool selection is not allowed",
        ),
        (
            plan.get("old_flagship_filter_applied_to_top50_pool"),
            False,
            "old flagship filter altered the top-50 pool",
        ),
    )
    for actual, expected, message in checks:
        _require_equal(actual, expected, message)


def _validate_pool_hashes(
    plan: Mapping[str, Any],
    raw: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
) -> None:
    _require_equal(len(raw), 50, "top-50 reasoning pool is incomplete")
    _require_equal(
        plan.get("top50_reasoning_pool_sha256"),
        _sha256(raw),
        "top-50 reasoning pool hash mismatch",
    )
    _require_equal(
        plan.get("expert_selectable_candidates_sha256"),
        _sha256(eligible),
        "expert selectable candidate hash mismatch",
    )
    companies = {
        str(row.get("company") or "").strip().casefold()
        for row in eligible
        if str(row.get("company") or "").strip()
    }
    if len(companies) < 4:
        raise Top50PoolOptimizationError(
            "fewer than four distinct-company selectable models remain in the top-50 pool"
        )
    _require_equal(
        plan.get("expert_selectable_distinct_company_count"),
        len(companies),
        "expert selectable distinct-company count is inconsistent",
    )


def _validate_raw_pool(raw: list[dict[str, Any]]) -> set[str]:
    models: set[str] = set()
    for rank, row in enumerate(raw, 1):
        model = str(row.get("model") or "").strip()
        company = str(row.get("company") or "").strip().casefold()
        _require_equal(
            row.get("popularity_rank"),
            rank,
            "top-50 popularity ranks must be contiguous",
        )
        if not model or model in models or not company:
            raise Top50PoolOptimizationError("top-50 pool identity is invalid")
        _require_equal(
            row.get("reasoning_supported"),
            True,
            "top-50 pool contains a non-reasoning model",
        )
        models.add(model)
    return models


def _validate_candidate(
    row: Mapping[str, Any], raw_models: set[str], index: int
) -> str:
    model = str(row.get("model") or "").strip()
    company = str(row.get("company") or "").strip().casefold()
    if not model or not company or model not in raw_models:
        raise Top50PoolOptimizationError(
            f"selectable candidate identity is invalid: {model}"
        )
    _require_equal(
        row.get("expert_center_selectable"),
        True,
        f"candidate is not selectable: {model}",
    )
    _require_equal(
        row.get("reasoning_rank_verified"),
        True,
        f"candidate lacks reasoning-rank evidence: {model}",
    )
    rank = row.get("popularity_rank")
    if isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= 50:
        raise Top50PoolOptimizationError(f"candidate popularity rank is invalid: {model}")
    providers = row.get("qualified_provider_count")
    if isinstance(providers, bool) or not isinstance(providers, int) or providers < 1:
        raise Top50PoolOptimizationError(f"candidate has no qualified provider: {model}")
    evidence = str(row.get("selection_evidence") or "")
    if any(fragment not in evidence for fragment in REQUIRED_SELECTION_EVIDENCE):
        raise Top50PoolOptimizationError(f"candidate endpoint evidence is incomplete: {model}")
    _finite_nonnegative(
        row.get("price_rank_usd_per_million"),
        f"expert_selectable_candidates[{index}].price_rank_usd_per_million",
    )
    return model


def _validate_eligible(
    eligible: list[dict[str, Any]], raw_models: set[str]
) -> None:
    seen: set[str] = set()
    for index, row in enumerate(eligible):
        model = _validate_candidate(row, raw_models, index)
        if model in seen:
            raise Top50PoolOptimizationError("selectable candidate models are repeated")
        seen.add(model)


def _validate_pool(
    plan: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _validate_plan_envelope(plan)
    raw = _rows(plan.get("top50_reasoning_models"), "top50_reasoning_models")
    eligible = _rows(
        plan.get("expert_selectable_candidates"),
        "expert_selectable_candidates",
    )
    _validate_pool_hashes(plan, raw, eligible)
    raw_models = _validate_raw_pool(raw)
    _validate_eligible(eligible, raw_models)
    return raw, eligible


def _roles() -> dict[str, dict[str, str]]:
    return {
        "evidence": {
            "role_id": "evidence",
            "role_kind": "independent",
            "role": "独立分析专家：重点检查证据、事实、数据质量、关键假设与不确定性",
        },
        "options": {
            "role_id": "options",
            "role_kind": "independent",
            "role": "独立分析专家：重点检查备选方案、机制、因果链与反事实",
        },
        "review": {
            "role_id": "review",
            "role_kind": "review",
            "role": "交叉审查专家：比较前序分析，找出冲突、遗漏、薄弱证据和失败模式",
        },
        "synthesis": {
            "role_id": "synthesis",
            "role_kind": "synthesis",
            "role": "最终综合专家：依据原始任务和全部前序结果形成唯一完整交付",
        },
    }


def _without_assignment(row: Mapping[str, Any]) -> dict[str, Any]:
    excluded = {
        "slot",
        "price_rank",
        "candidate_price_rank",
        "role",
        "role_id",
        "role_kind",
        "optimizer_role",
    }
    return {key: value for key, value in row.items() if key not in excluded}


def _ordinal_penalties(
    rows: list[dict[str, Any]], key: str, *, reverse: bool = False
) -> dict[str, int]:
    ordered = sorted(
        rows,
        key=lambda row: (
            -float(row.get(key) or 0) if reverse else float(row.get(key) or 0),
            int(row.get("popularity_rank") or 1_000_000),
            str(row.get("model") or ""),
        ),
    )
    denominator = max(1, len(ordered) - 1)
    return {
        str(row["model"]): round(index * 1000 / denominator)
        for index, row in enumerate(ordered)
    }


def _candidate_penalties(
    eligible: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    price = _ordinal_penalties(eligible, "price_rank_usd_per_million")
    intelligence = _ordinal_penalties(eligible, "official_intelligence_rank")
    popularity = _ordinal_penalties(eligible, "popularity_rank")
    providers = _ordinal_penalties(eligible, "qualified_provider_count", reverse=True)
    result: dict[str, dict[str, int]] = {}
    for index, row in enumerate(eligible):
        model = str(row["model"])
        result[model] = {
            "price": price[model],
            "intelligence": intelligence[model],
            "provider_redundancy": providers[model],
            "popularity": popularity[model],
            "base": (
                38 * price[model]
                + 30 * intelligence[model]
                + 18 * providers[model]
                + 14 * popularity[model]
            ),
            "tie_break": index,
        }
    return result


def _role_cost(role: str, penalties: Mapping[str, int]) -> int:
    base = int(penalties["base"])
    if role == "synthesis":
        return base + 30 * int(penalties["intelligence"]) + 10 * int(
            penalties["provider_redundancy"]
        )
    if role == "review":
        return (
            base
            + 20 * int(penalties["intelligence"])
            + 10 * int(penalties["provider_redundancy"])
            + 5 * int(penalties["popularity"])
        )
    return base + 5 * int(penalties["intelligence"])


def _assignment_variables(
    model: cp_model.CpModel, eligible: list[dict[str, Any]]
) -> dict[tuple[int, str], Any]:
    return {
        (index, role): model.NewBoolVar(f"assign_{index}_{role}")
        for index in range(len(eligible))
        for role in ROLE_ORDER
    }


def _company_indices(eligible: list[dict[str, Any]]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for index, row in enumerate(eligible):
        result.setdefault(str(row["company"]).casefold(), []).append(index)
    return result


def _add_constraints(
    model: cp_model.CpModel,
    variables: Mapping[tuple[int, str], Any],
    eligible: list[dict[str, Any]],
) -> None:
    for role in ROLE_ORDER:
        model.Add(
            sum(variables[(index, role)] for index in range(len(eligible))) == 1
        )
    for index in range(len(eligible)):
        model.Add(sum(variables[(index, role)] for role in ROLE_ORDER) <= 1)
    for indices in _company_indices(eligible).values():
        model.Add(
            sum(
                variables[(index, role)]
                for index in indices
                for role in ROLE_ORDER
            )
            <= 1
        )


def _objective_terms(
    eligible: list[dict[str, Any]],
    penalties: Mapping[str, Mapping[str, int]],
    variables: Mapping[tuple[int, str], Any],
) -> list[Any]:
    terms: list[Any] = []
    for index, row in enumerate(eligible):
        model_id = str(row["model"])
        for role_index, role in enumerate(ROLE_ORDER):
            cost = (
                _role_cost(role, penalties[model_id]) * 1000
                + int(penalties[model_id]["tie_break"]) * 10
                + role_index
            )
            terms.append(cost * variables[(index, role)])
    return terms


def _solver() -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_time_in_seconds = 10.0
    return solver


def _selected_rows(
    solver: cp_model.CpSolver,
    variables: Mapping[tuple[int, str], Any],
    eligible: list[dict[str, Any]],
    penalties: Mapping[str, Mapping[str, int]],
) -> list[dict[str, Any]]:
    templates = _roles()
    assigned: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(eligible):
        for role in ROLE_ORDER:
            if solver.Value(variables[(index, role)]):
                assigned[role] = {
                    **_without_assignment(row),
                    **templates[role],
                    "optimizer_role": role,
                    "optimizer_role_cost": _role_cost(
                        role, penalties[str(row["model"])]
                    ),
                }
    if set(assigned) != set(ROLE_ORDER):
        raise Top50PoolOptimizationError("OR-Tools returned an incomplete assignment")
    selected = [assigned[role] for role in ROLE_ORDER]
    for slot, row in enumerate(selected, 1):
        row["slot"] = slot
    return selected


def _optimizer_audit(
    solver: cp_model.CpSolver, status: int, selected: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "optimizer": "ortools-cp-sat",
        "ortools_version": package_version("ortools"),
        "solver_status": solver.StatusName(status),
        "objective_value": int(round(solver.ObjectiveValue())),
        "deterministic": True,
        "num_search_workers": 1,
        "random_seed": 0,
        "active_model_count": len(selected),
        "active_companies": [row["company"] for row in selected],
        "objective_definition": {
            "base_weights": {
                "combined_input_output_price_rank": 38,
                "official_intelligence_rank": 30,
                "qualified_provider_redundancy": 18,
                "weekly_popularity_rank": 14,
            },
            "role_adjustments": {
                "synthesis": "extra intelligence and provider-redundancy penalty",
                "review": "extra intelligence, provider and popularity penalty",
                "independent": "small extra intelligence penalty",
            },
            "hard_constraints": [
                "exactly-one-model-per-role",
                "one-role-per-model",
                "one-active-model-per-company",
                "frozen-top50-pool-only",
            ],
        },
    }


def _optimize_active_team(
    eligible: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[str, int]]]:
    penalties = _candidate_penalties(eligible)
    model = cp_model.CpModel()
    variables = _assignment_variables(model, eligible)
    _add_constraints(model, variables, eligible)
    model.Minimize(sum(_objective_terms(eligible, penalties, variables)))
    solver = _solver()
    status = solver.Solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise Top50PoolOptimizationError(
            "OR-Tools could not form a distinct-company four-role team"
        )
    selected = _selected_rows(solver, variables, eligible, penalties)
    return selected, _optimizer_audit(solver, status, selected), penalties


def _ordered_recoveries(
    eligible: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    penalties: Mapping[str, Mapping[str, int]],
    recovery_call_ceiling: int,
) -> list[dict[str, Any]]:
    selected_models = {str(row["model"]) for row in selected}
    active_companies = {str(row["company"]).casefold() for row in selected}
    remaining = [row for row in eligible if str(row["model"]) not in selected_models]
    remaining.sort(
        key=lambda row: (
            str(row["company"]).casefold() in active_companies,
            int(penalties[str(row["model"])]["base"]),
            int(row.get("popularity_rank") or 1_000_000),
            int(row.get("official_intelligence_rank") or 1_000_000),
            str(row.get("model") or ""),
        )
    )
    records: list[dict[str, Any]] = []
    for priority, row in enumerate(remaining, 1):
        record = _without_assignment(row)
        record.update(
            {
                "slot": priority,
                "recovery_priority": priority,
                "optimizer_base_penalty": int(
                    penalties[str(row["model"])]["base"]
                ),
                "warm_recovery": priority <= recovery_call_ceiling,
                "company_conflicts_with_active": (
                    str(row["company"]).casefold() in active_companies
                ),
                "approved_standby": True,
            }
        )
        records.append(record)
    return records


def _runtime_recovery_rows(
    all_recoveries: list[dict[str, Any]], recovery_call_ceiling: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    used_companies: set[str] = set()
    for candidate in all_recoveries:
        company = str(candidate.get("company") or "").casefold()
        if not company or company in used_companies:
            continue
        record = dict(candidate)
        record["slot"] = len(rows) + 1
        rows.append(record)
        used_companies.add(company)
        if len(rows) == recovery_call_ceiling:
            break
    if len(rows) != recovery_call_ceiling:
        raise Top50PoolOptimizationError(
            "not enough distinct-company warm recovery candidates for the approved reserve"
        )
    return rows


def _inventory(
    raw: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    all_recoveries: list[dict[str, Any]],
    warm_models: set[str],
) -> list[dict[str, Any]]:
    eligible_by_model = {
        str(row["model"]): _without_assignment(row) for row in eligible
    }
    active = {str(row["model"]) for row in selected}
    priorities = {
        str(row["model"]): int(row["recovery_priority"]) for row in all_recoveries
    }
    inventory: list[dict[str, Any]] = []
    for slot, raw_row in enumerate(raw, 1):
        model = str(raw_row["model"])
        qualified = eligible_by_model.get(model)
        priority = priorities.get(model)
        if model in active:
            state = "active"
        elif model in warm_models:
            state = "warm-recovery"
        elif priority is not None:
            state = "extended-recovery"
        else:
            state = "ineligible-standby"
        record = dict(raw_row)
        record.update(
            {
                "standby_slot": slot,
                "standby_state": state,
                "execution_eligible": qualified is not None,
                "assigned_for_current_run": state == "active",
                "recovery_priority": priority,
                "retained_by_expert_center": True,
            }
        )
        if qualified is not None:
            record["qualified_candidate"] = qualified
        inventory.append(record)
    return inventory


def _price_ranked_rows(
    selected: list[dict[str, Any]], warm_recoveries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    source = [
        *[_without_assignment(row) for row in selected],
        *[_without_assignment(row) for row in warm_recoveries],
    ]
    ranked: list[dict[str, Any]] = []
    for rank, row in enumerate(source, 1):
        record = dict(row)
        record["slot"] = rank
        record["price_rank"] = rank
        ranked.append(record)
    return ranked


def _selection_receipt(
    source_plan: Mapping[str, Any],
    selected: list[dict[str, Any]],
    warm_recoveries: list[dict[str, Any]],
    extended_recoveries: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
    optimizer_audit: Mapping[str, Any],
    recovery_call_ceiling: int,
) -> dict[str, Any]:
    receipt = {
        "schema_version": "expert-center-top50-ortools-selection-receipt-v2",
        "candidate_pool_plan_sha256": source_plan["plan_sha256"],
        "candidate_pool_sha256": source_plan["top50_reasoning_pool_sha256"],
        "selectable_candidates_sha256": source_plan[
            "expert_selectable_candidates_sha256"
        ],
        "selection_policy": (
            "frozen-top50 -> ortools-four-role-team -> distinct-company-warm-"
            "recovery-within-call-ceiling -> all-other-qualified-models-extended-recovery"
        ),
        "selected_models": [row["model"] for row in selected],
        "recovery_models": [row["model"] for row in warm_recoveries],
        "extended_recovery_models": [row["model"] for row in extended_recoveries],
        "total_qualified_recovery_inventory_count": (
            len(warm_recoveries) + len(extended_recoveries)
        ),
        "recovery_call_ceiling": recovery_call_ceiling,
        "top50_inventory_models": [row["model"] for row in inventory],
        "top50_inventory_count": len(inventory),
        "standby_inventory_sha256": _sha256(inventory),
        "optimizer_audit": dict(optimizer_audit),
        "model_calls": 0,
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return receipt


def materialize_top50_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan_value = packet.get("governance_model_plan")
    if not isinstance(plan_value, Mapping):
        raise Top50PoolOptimizationError("governance_model_plan is missing")
    source_plan = dict(plan_value)
    raw, eligible = _validate_pool(source_plan)
    budget = packet.get("approved_budget")
    budget = budget if isinstance(budget, Mapping) else {}
    recovery_call_ceiling = _nonnegative_int(
        budget.get("maximum_recovery_calls", 4),
        "approved_budget.maximum_recovery_calls",
    )

    selected, optimizer_audit, penalties = _optimize_active_team(eligible)
    all_recoveries = _ordered_recoveries(
        eligible, selected, penalties, recovery_call_ceiling
    )
    warm_recoveries = _runtime_recovery_rows(
        all_recoveries, recovery_call_ceiling
    )
    warm_models = {str(row["model"]) for row in warm_recoveries}
    extended_recoveries = [
        row for row in all_recoveries if str(row["model"]) not in warm_models
    ]
    inventory = _inventory(
        raw, eligible, selected, all_recoveries, warm_models
    )
    state_counts = {
        state: sum(1 for row in inventory if row["standby_state"] == state)
        for state in (
            "active",
            "warm-recovery",
            "extended-recovery",
            "ineligible-standby",
        )
    }

    derived = dict(source_plan)
    derived.update(
        {
            "selected_models": selected,
            "recovery_models": warm_recoveries,
            "extended_recovery_models": extended_recoveries,
            "price_ranked_models": _price_ranked_rows(selected, warm_recoveries),
            "expert_count": EXPECTED_PRIMARY_COUNT,
            "recovery_count": recovery_call_ceiling,
            "recovery_inventory_count": len(warm_recoveries),
            "total_qualified_recovery_inventory_count": len(all_recoveries),
            "extended_recovery_model_count": len(extended_recoveries),
            "expert_center_pool_selection_completed": True,
            "expert_center_reranking_allowed": True,
            "expert_center_reranking_scope": "frozen-top50-pool-only",
            "legacy_governance_selected_models_are_preview_only": False,
            "source_governance_pool_plan_sha256": str(
                source_plan.get("plan_sha256") or ""
            ),
            "model_assignment_authority": "expert-assessment-center",
            "candidate_pool_authority": "decision-system-governance",
            "selected_from_top50_reasoning_pool_only": True,
            "selected_from_top20_reasoning_pool_only": False,
            "all_top50_models_received_by_expert_center": True,
            "expert_center_top50_inventory_schema_version": STANDBY_SCHEMA_VERSION,
            "expert_center_top50_inventory": inventory,
            "expert_center_top50_inventory_sha256": _sha256(inventory),
            "expert_center_top50_inventory_count": len(inventory),
            "expert_center_top50_inventory_state_counts": state_counts,
            "standby_inventory_policy": (
                "all-frozen-top50-retained -> four-active -> approved-distinct-company-"
                "warm-recovery -> all-other-qualified-models-extended-recovery"
            ),
            "optimizer_used": True,
            "optimizer_library": "ortools",
            "optimizer_algorithm": "cp-sat",
            "optimizer_audit": optimizer_audit,
            "role_assignment_policy": (
                "ortools-cp-sat-joint-model-and-role-assignment-with-distinct-company-"
                "hard-constraint"
            ),
            "company_uniqueness_scope": (
                "active-and-warm-recovery-hard-unique; extended-inventory-use-once-guarded"
            ),
            "model_substitution_allowed": False,
            "unapproved_model_substitution_allowed": False,
        }
    )
    receipt = _selection_receipt(
        source_plan,
        selected,
        warm_recoveries,
        extended_recoveries,
        inventory,
        optimizer_audit,
        recovery_call_ceiling,
    )
    derived["expert_center_selection_receipt"] = receipt
    material = dict(derived)
    material.pop("plan_sha256", None)
    derived["plan_sha256"] = _sha256(material)

    materialized_packet = dict(packet)
    materialized_packet["governance_model_plan"] = derived
    return materialized_packet, receipt


def materialize_top20_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    return materialize_top50_selection(packet)


__all__ = [
    "Top50PoolOptimizationError",
    "Top20PoolSelectionError",
    "materialize_top50_selection",
    "materialize_top20_selection",
]
