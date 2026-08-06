#!/usr/bin/env python3
"""Materialize an expert execution plan from a frozen governance top-20 pool."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

POOL_SCHEMA_VERSION = "governance-openrouter-top20-reasoning-pool-v1"
POOL_SOURCE = "openrouter-most-popular-last-week-token-volume"
STANDBY_SCHEMA_VERSION = "expert-center-top20-standby-inventory-v1"
EXPECTED_POOL_SIZE = 20
EXPECTED_PRIMARY_COUNT = 4
EXPECTED_RECOVERY_COUNT = 4
EXPECTED_SELECTION_COUNT = EXPECTED_PRIMARY_COUNT + EXPECTED_RECOVERY_COUNT
REQUIRED_SELECTION_EVIDENCE = (
    "openrouter-top-weekly-reasoning",
    "live-exact-endpoint-qualified",
    "authenticated-zdr-endpoint-qualified",
)


class Top20PoolSelectionError(RuntimeError):
    """Raised when the expert center cannot select safely from the frozen pool."""


def _stable_bytes(value: Any) -> bytes:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return serialized.encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_bytes(value)).hexdigest()


def _plan_digest(plan: Mapping[str, Any]) -> str:
    material = dict(plan)
    material.pop("plan_sha256", None)
    return _sha256(material)


def _finite_nonnegative(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise Top20PoolSelectionError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise Top20PoolSelectionError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise Top20PoolSelectionError(f"{field} must be finite and nonnegative")
    return number


def _rows(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise Top20PoolSelectionError(f"{field} must be an array")
    result: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise Top20PoolSelectionError(f"{field}[{index}] must be an object")
        result.append(dict(row))
    return result


def _require_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise Top20PoolSelectionError(message)


def _validate_plan_envelope(plan: Mapping[str, Any]) -> None:
    checks = (
        (
            plan.get("plan_sha256"),
            _plan_digest(plan),
            "governance candidate-pool plan digest mismatch",
        ),
        (
            plan.get("top20_reasoning_pool_schema_version"),
            POOL_SCHEMA_VERSION,
            "top-20 reasoning pool schema is unsupported",
        ),
        (
            plan.get("top20_reasoning_pool_source"),
            POOL_SOURCE,
            "top-20 reasoning pool source is invalid",
        ),
        (
            plan.get("top20_reasoning_pool_size"),
            EXPECTED_POOL_SIZE,
            "top-20 reasoning pool size is not 20",
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
            plan.get("old_flagship_filter_applied_to_top20_pool"),
            False,
            "old flagship filtering must not alter the top-20 candidate pool",
        ),
    )
    for actual, expected, message in checks:
        _require_equal(actual, expected, message)


def _validate_pool_hashes(
    plan: Mapping[str, Any],
    raw: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
) -> None:
    _require_equal(
        len(raw),
        EXPECTED_POOL_SIZE,
        "top-20 reasoning pool is incomplete",
    )
    _require_equal(
        plan.get("top20_reasoning_pool_sha256"),
        _sha256(raw),
        "top-20 reasoning pool hash mismatch",
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
    if len(companies) < EXPECTED_SELECTION_COUNT:
        raise Top20PoolSelectionError(
            "fewer than eight distinct-company selectable models remain in the top-20 pool"
        )
    _require_equal(
        plan.get("expert_selectable_distinct_company_count"),
        len(companies),
        "expert selectable distinct-company count is inconsistent",
    )


def _validate_raw_pool(raw: list[dict[str, Any]]) -> set[str]:
    raw_models: set[str] = set()
    for rank, row in enumerate(raw, 1):
        model = str(row.get("model") or "").strip()
        company = str(row.get("company") or "").strip().casefold()
        _require_equal(
            row.get("popularity_rank"),
            rank,
            "top-20 popularity ranks must be contiguous",
        )
        if not model or model in raw_models or not company:
            raise Top20PoolSelectionError("top-20 pool contains an invalid identity")
        _require_equal(
            row.get("reasoning_supported"),
            True,
            "top-20 pool contains a non-reasoning model",
        )
        raw_models.add(model)
    return raw_models


def _candidate_identity(
    row: Mapping[str, Any],
    raw_models: set[str],
    seen_models: set[str],
) -> tuple[str, str]:
    model = str(row.get("model") or "").strip()
    company = str(row.get("company") or "").strip().casefold()
    if model not in raw_models:
        raise Top20PoolSelectionError(
            f"selectable model is outside the frozen top-20 pool: {model}"
        )
    if not company or model in seen_models:
        raise Top20PoolSelectionError(
            "selectable candidates must use valid unique model identities"
        )
    return model, company


def _validate_candidate_qualification(row: Mapping[str, Any], model: str) -> None:
    _require_equal(
        row.get("expert_center_selectable"),
        True,
        f"candidate is not marked selectable: {model}",
    )
    _require_equal(
        row.get("reasoning_rank_verified"),
        True,
        f"candidate lacks top-weekly reasoning-rank evidence: {model}",
    )
    popularity_rank = row.get("popularity_rank")
    if (
        isinstance(popularity_rank, bool)
        or not isinstance(popularity_rank, int)
        or not 1 <= popularity_rank <= EXPECTED_POOL_SIZE
    ):
        raise Top20PoolSelectionError(
            f"candidate has an invalid popularity rank: {model}"
        )
    providers = row.get("qualified_provider_count")
    provider_valid = (
        not isinstance(providers, bool)
        and isinstance(providers, int)
        and providers >= 1
    )
    if not provider_valid:
        raise Top20PoolSelectionError(f"candidate has no qualified provider: {model}")
    evidence = str(row.get("selection_evidence") or "")
    if any(fragment not in evidence for fragment in REQUIRED_SELECTION_EVIDENCE):
        raise Top20PoolSelectionError(
            f"candidate lacks direct top-20 endpoint evidence: {model}"
        )


def _validate_selectable_candidates(
    eligible: list[dict[str, Any]], raw_models: set[str]
) -> None:
    seen_models: set[str] = set()
    for index, row in enumerate(eligible):
        model, _ = _candidate_identity(row, raw_models, seen_models)
        _validate_candidate_qualification(row, model)
        _finite_nonnegative(
            row.get("price_rank_usd_per_million"),
            f"expert_selectable_candidates[{index}].price_rank_usd_per_million",
        )
        seen_models.add(model)


def _validate_pool(
    plan: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _validate_plan_envelope(plan)
    raw = _rows(plan.get("top20_reasoning_models"), "top20_reasoning_models")
    eligible = _rows(
        plan.get("expert_selectable_candidates"),
        "expert_selectable_candidates",
    )
    _validate_pool_hashes(plan, raw, eligible)
    raw_models = _validate_raw_pool(raw)
    _validate_selectable_candidates(eligible, raw_models)
    return raw, eligible


def _roles() -> list[dict[str, str]]:
    return [
        {
            "role_id": "evidence",
            "role_kind": "independent",
            "role": "独立分析专家：重点检查证据、事实、数据质量、关键假设与不确定性",
        },
        {
            "role_id": "options",
            "role_kind": "independent",
            "role": "独立分析专家：重点检查备选方案、机制、因果链与反事实",
        },
        {
            "role_id": "review",
            "role_kind": "review",
            "role": "交叉审查专家：比较前序分析，找出冲突、遗漏、薄弱证据和失败模式",
        },
        {
            "role_id": "synthesis",
            "role_kind": "synthesis",
            "role": "最终综合专家：依据原始任务和全部前序结果形成唯一完整交付",
        },
    ]


def _without_assignment(row: Mapping[str, Any]) -> dict[str, Any]:
    excluded = {"slot", "price_rank", "role", "role_id", "role_kind"}
    return {key: value for key, value in row.items() if key not in excluded}


def _select_rows(eligible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        eligible,
        key=lambda row: (
            _finite_nonnegative(
                row.get("price_rank_usd_per_million"),
                "candidate price",
            ),
            int(row.get("popularity_rank") or 1_000_000),
            int(row.get("official_intelligence_rank") or 1_000_000),
            str(row.get("model") or ""),
        ),
    )
    selected: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    seen_companies: set[str] = set()
    for row in ordered:
        model = str(row.get("model") or "").strip()
        company = str(row.get("company") or "").strip().casefold()
        if not model or not company or model in seen_models or company in seen_companies:
            continue
        selected.append(row)
        seen_models.add(model)
        seen_companies.add(company)
        if len(selected) == EXPECTED_SELECTION_COUNT:
            break
    if len(selected) != EXPECTED_SELECTION_COUNT:
        raise Top20PoolSelectionError(
            "unable to select four primary and four recovery models from eight distinct companies"
        )
    return selected


def _assign_primary_roles(primary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intelligence_order = sorted(
        primary_rows,
        key=lambda row: (
            int(row.get("official_intelligence_rank") or 1_000_000),
            int(row.get("popularity_rank") or 1_000_000),
            str(row.get("model") or ""),
        ),
    )
    synthesis = intelligence_order[0]
    review = intelligence_order[1]
    reserved = {
        str(synthesis.get("model") or ""),
        str(review.get("model") or ""),
    }
    independent = [
        row for row in primary_rows if str(row.get("model") or "") not in reserved
    ]
    if len(independent) != 2:
        raise Top20PoolSelectionError("primary model identities are not unique")
    templates = _roles()
    assigned = [
        {**_without_assignment(independent[0]), **templates[0]},
        {**_without_assignment(independent[1]), **templates[1]},
        {**_without_assignment(review), **templates[2]},
        {**_without_assignment(synthesis), **templates[3]},
    ]
    for slot, row in enumerate(assigned, 1):
        row["slot"] = slot
    return assigned


def _materialize_recovery_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recovery: list[dict[str, Any]] = []
    for slot, row in enumerate(rows, 1):
        record = _without_assignment(row)
        record["slot"] = slot
        recovery.append(record)
    return recovery


def _materialize_price_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, 1):
        record = _without_assignment(row)
        record["slot"] = rank
        record["price_rank"] = rank
        ranked.append(record)
    return ranked


def _standby_inventory(
    raw: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    selected_models: list[dict[str, Any]],
    recovery_models: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Retain every frozen top-20 model in explicit expert-center state."""
    eligible_by_model = {
        str(row.get("model") or "").strip(): _without_assignment(row)
        for row in eligible
    }
    active = {str(row.get("model") or "").strip() for row in selected_models}
    warm_recovery = {
        str(row.get("model") or "").strip() for row in recovery_models
    }
    inventory: list[dict[str, Any]] = []
    for standby_slot, raw_row in enumerate(raw, 1):
        model = str(raw_row.get("model") or "").strip()
        qualified = eligible_by_model.get(model)
        if model in active:
            state = "active"
        elif model in warm_recovery:
            state = "warm-recovery"
        elif qualified is not None:
            state = "extended-standby"
        else:
            state = "ineligible-standby"
        record = dict(raw_row)
        record.update(
            {
                "standby_slot": standby_slot,
                "standby_state": state,
                "execution_eligible": qualified is not None,
                "assigned_for_current_run": state == "active",
                "callable_under_current_recovery_ceiling": state == "warm-recovery",
                "retained_by_expert_center": True,
            }
        )
        if qualified is not None:
            record["qualified_candidate"] = qualified
        inventory.append(record)
    return inventory


def _selection_receipt(
    source_plan: Mapping[str, Any],
    selected_models: list[dict[str, Any]],
    recovery_models: list[dict[str, Any]],
    price_ranked_models: list[dict[str, Any]],
    standby_inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    receipt = {
        "schema_version": "expert-center-top20-pool-selection-receipt-v2",
        "candidate_pool_plan_sha256": source_plan["plan_sha256"],
        "candidate_pool_sha256": source_plan["top20_reasoning_pool_sha256"],
        "selectable_candidates_sha256": source_plan[
            "expert_selectable_candidates_sha256"
        ],
        "selection_policy": (
            "top20-reasoning-only -> governance-qualified -> distinct-company -> "
            "combined-token-price-ascending -> four-primary-four-warm-recovery -> "
            "retain-all-remaining-models-as-explicit-standby"
        ),
        "selected_models": [row["model"] for row in selected_models],
        "recovery_models": [row["model"] for row in recovery_models],
        "price_ranked_models": [row["model"] for row in price_ranked_models],
        "top20_inventory_models": [row["model"] for row in standby_inventory],
        "top20_inventory_states": [
            {"model": row["model"], "state": row["standby_state"]}
            for row in standby_inventory
        ],
        "top20_inventory_count": len(standby_inventory),
        "standby_inventory_sha256": _sha256(standby_inventory),
        "model_calls": 0,
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return receipt


def materialize_top20_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan_value = packet.get("governance_model_plan")
    if not isinstance(plan_value, Mapping):
        raise Top20PoolSelectionError("governance_model_plan is missing")
    source_plan = dict(plan_value)
    raw, eligible = _validate_pool(source_plan)
    selected_eight = _select_rows(eligible)
    primary_rows = [dict(row) for row in selected_eight[:EXPECTED_PRIMARY_COUNT]]
    recovery_rows = [dict(row) for row in selected_eight[EXPECTED_PRIMARY_COUNT:]]

    selected_models = _assign_primary_roles(primary_rows)
    recovery_models = _materialize_recovery_rows(recovery_rows)
    price_ranked_models = _materialize_price_rows(selected_eight)
    standby_inventory = _standby_inventory(
        raw,
        eligible,
        selected_models,
        recovery_models,
    )
    source_plan_sha256 = str(source_plan.get("plan_sha256") or "")
    state_counts = {
        state: sum(1 for row in standby_inventory if row["standby_state"] == state)
        for state in (
            "active",
            "warm-recovery",
            "extended-standby",
            "ineligible-standby",
        )
    }

    derived = dict(source_plan)
    derived.update(
        {
            "selected_models": selected_models,
            "recovery_models": recovery_models,
            "price_ranked_models": price_ranked_models,
            "expert_count": EXPECTED_PRIMARY_COUNT,
            "recovery_count": EXPECTED_RECOVERY_COUNT,
            "expert_center_pool_selection_completed": True,
            "expert_center_reranking_allowed": False,
            "legacy_governance_selected_models_are_preview_only": False,
            "source_governance_pool_plan_sha256": source_plan_sha256,
            "model_assignment_authority": "expert-assessment-center",
            "candidate_pool_authority": "decision-system-governance",
            "selected_from_top20_reasoning_pool_only": True,
            "all_top20_models_received_by_expert_center": True,
            "expert_center_top20_inventory_schema_version": STANDBY_SCHEMA_VERSION,
            "expert_center_top20_inventory": standby_inventory,
            "expert_center_top20_inventory_sha256": _sha256(standby_inventory),
            "expert_center_top20_inventory_count": len(standby_inventory),
            "expert_center_standby_model_count": (
                len(standby_inventory) - EXPECTED_PRIMARY_COUNT
            ),
            "expert_center_top20_inventory_state_counts": state_counts,
            "standby_inventory_policy": (
                "all-frozen-top20-models-retained -> four-active -> four-warm-recovery -> "
                "remaining-qualified-models-extended-standby -> "
                "unqualified-models-retained-with-ineligible-state"
            ),
            "role_assignment_policy": (
                "top20-pool-price-minimal-distinct-company-set -> "
                "official-intelligence-rank-ascending -> strongest-final-synthesis -> "
                "second-strongest-cross-review -> remaining-independent"
            ),
            "final_synthesis_official_intelligence_rank": int(
                selected_models[-1]["official_intelligence_rank"]
            ),
            "cross_review_official_intelligence_rank": int(
                selected_models[-2]["official_intelligence_rank"]
            ),
        }
    )
    receipt = _selection_receipt(
        source_plan,
        selected_models,
        recovery_models,
        price_ranked_models,
        standby_inventory,
    )
    derived["expert_center_selection_receipt"] = receipt
    material = dict(derived)
    material.pop("plan_sha256", None)
    derived["plan_sha256"] = _sha256(material)

    materialized_packet = dict(packet)
    materialized_packet["governance_model_plan"] = derived
    return materialized_packet, receipt
