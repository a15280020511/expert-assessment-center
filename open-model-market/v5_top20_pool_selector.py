#!/usr/bin/env python3
"""Materialize an expert execution plan from a frozen governance top-20 pool."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

POOL_SCHEMA_VERSION = "governance-openrouter-top20-reasoning-pool-v1"
POOL_SOURCE = "openrouter-most-popular-last-week-token-volume"
EXPECTED_POOL_SIZE = 20
EXPECTED_PRIMARY_COUNT = 4
EXPECTED_RECOVERY_COUNT = 4


class Top20PoolSelectionError(RuntimeError):
    """Raised when the expert center cannot select safely from the frozen pool."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


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


def _validate_pool(plan: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if plan.get("plan_sha256") != _plan_digest(plan):
        raise Top20PoolSelectionError("governance candidate-pool plan digest mismatch")
    if plan.get("top20_reasoning_pool_schema_version") != POOL_SCHEMA_VERSION:
        raise Top20PoolSelectionError("top-20 reasoning pool schema is unsupported")
    if plan.get("top20_reasoning_pool_source") != POOL_SOURCE:
        raise Top20PoolSelectionError("top-20 reasoning pool source is invalid")
    if plan.get("top20_reasoning_pool_size") != EXPECTED_POOL_SIZE:
        raise Top20PoolSelectionError("top-20 reasoning pool size is not 20")
    if plan.get("candidate_pool_authority") != "decision-system-governance":
        raise Top20PoolSelectionError("candidate pool authority is invalid")
    if plan.get("model_assignment_authority") != "expert-assessment-center":
        raise Top20PoolSelectionError("model assignment authority is invalid")
    if plan.get("expert_center_pool_selection_allowed") is not True:
        raise Top20PoolSelectionError("expert center pool selection is not allowed")

    raw = _rows(plan.get("top20_reasoning_models"), "top20_reasoning_models")
    eligible = _rows(
        plan.get("expert_selectable_candidates"),
        "expert_selectable_candidates",
    )
    if len(raw) != EXPECTED_POOL_SIZE:
        raise Top20PoolSelectionError("top-20 reasoning pool is incomplete")
    if plan.get("top20_reasoning_pool_sha256") != _sha256(raw):
        raise Top20PoolSelectionError("top-20 reasoning pool hash mismatch")
    if plan.get("expert_selectable_candidates_sha256") != _sha256(eligible):
        raise Top20PoolSelectionError("expert selectable candidate hash mismatch")
    if len(eligible) < EXPECTED_PRIMARY_COUNT + EXPECTED_RECOVERY_COUNT:
        raise Top20PoolSelectionError(
            "fewer than eight selectable models remain in the top-20 pool"
        )

    raw_models: set[str] = set()
    for index, row in enumerate(raw, 1):
        model = str(row.get("model") or "").strip()
        company = str(row.get("company") or "").strip().casefold()
        if row.get("popularity_rank") != index:
            raise Top20PoolSelectionError("top-20 popularity ranks must be contiguous")
        if not model or model in raw_models or not company:
            raise Top20PoolSelectionError("top-20 pool contains an invalid identity")
        if row.get("reasoning_supported") is not True:
            raise Top20PoolSelectionError("top-20 pool contains a non-reasoning model")
        raw_models.add(model)

    eligible_models: set[str] = set()
    eligible_companies: set[str] = set()
    for index, row in enumerate(eligible):
        model = str(row.get("model") or "").strip()
        company = str(row.get("company") or "").strip().casefold()
        if model not in raw_models:
            raise Top20PoolSelectionError(
                f"selectable model is outside the frozen top-20 pool: {model}"
            )
        if not company or model in eligible_models or company in eligible_companies:
            raise Top20PoolSelectionError(
                "selectable candidates must use globally distinct models and companies"
            )
        if row.get("expert_center_selectable") is not True:
            raise Top20PoolSelectionError(
                f"candidate is not marked selectable: {model}"
            )
        providers = row.get("qualified_provider_count")
        if isinstance(providers, bool) or not isinstance(providers, int) or providers < 1:
            raise Top20PoolSelectionError(
                f"candidate has no qualified provider: {model}"
            )
        _finite_nonnegative(
            row.get("price_rank_usd_per_million"),
            f"expert_selectable_candidates[{index}].price_rank_usd_per_million",
        )
        eligible_models.add(model)
        eligible_companies.add(company)
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
    selected = ordered[: EXPECTED_PRIMARY_COUNT + EXPECTED_RECOVERY_COUNT]
    if len(selected) != EXPECTED_PRIMARY_COUNT + EXPECTED_RECOVERY_COUNT:
        raise Top20PoolSelectionError("unable to select four primary and four recovery models")
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


def materialize_top20_selection(packet: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    plan_value = packet.get("governance_model_plan")
    if not isinstance(plan_value, Mapping):
        raise Top20PoolSelectionError("governance_model_plan is missing")
    source_plan = dict(plan_value)
    _, eligible = _validate_pool(source_plan)
    selected_eight = _select_rows(eligible)
    primary_price_order = [dict(row) for row in selected_eight[:EXPECTED_PRIMARY_COUNT]]
    recovery_price_order = [dict(row) for row in selected_eight[EXPECTED_PRIMARY_COUNT:]]

    selected_models = _assign_primary_roles(primary_price_order)
    recovery_models = []
    for slot, row in enumerate(recovery_price_order, 1):
        record = _without_assignment(row)
        record["slot"] = slot
        recovery_models.append(record)

    price_ranked_models = []
    for rank, row in enumerate(selected_eight, 1):
        record = _without_assignment(row)
        record["slot"] = rank
        record["price_rank"] = rank
        price_ranked_models.append(record)

    source_plan_sha256 = str(source_plan.get("plan_sha256") or "")
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
    receipt = {
        "schema_version": "expert-center-top20-pool-selection-receipt-v1",
        "candidate_pool_plan_sha256": source_plan_sha256,
        "candidate_pool_sha256": source_plan["top20_reasoning_pool_sha256"],
        "selectable_candidates_sha256": source_plan[
            "expert_selectable_candidates_sha256"
        ],
        "selection_policy": (
            "top20-reasoning-only -> governance-qualified -> distinct-company -> "
            "combined-token-price-ascending -> four-primary-four-recovery"
        ),
        "selected_models": [row["model"] for row in selected_models],
        "recovery_models": [row["model"] for row in recovery_models],
        "price_ranked_models": [row["model"] for row in price_ranked_models],
        "model_calls": 0,
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    derived["expert_center_selection_receipt"] = receipt
    material = dict(derived)
    material.pop("plan_sha256", None)
    derived["plan_sha256"] = _sha256(material)

    materialized_packet = dict(packet)
    materialized_packet["governance_model_plan"] = derived
    return materialized_packet, receipt
