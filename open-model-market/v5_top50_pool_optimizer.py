"""Top-50 OR-Tools optimizer with unrestricted provider routing."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_top50_pool_optimizer_legacy as _legacy
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

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

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


def _open_metrics(row: Mapping[str, Any], prices: Mapping[str, int]) -> tuple[int, int, int, int]:
    price = prices[str(row["model"])]
    popularity = min(10_000, int(row.get("popularity_rank") or 10_000))
    intelligence = min(10_000, int(row.get("official_intelligence_rank") or 10_000))
    return price, popularity, intelligence, 0


def _apply_recovery_priority(plan: dict[str, Any]) -> dict[str, Any]:
    """Order warm recoveries by the same objective used to select recoveries."""
    candidates = _legacy._rows(
        plan.get("top50_expert_selectable_candidates"),
        "top50_expert_selectable_candidates",
    )
    recoveries = _legacy._rows(plan.get("recovery_models"), "recovery_models")
    prices = _legacy._price_ranks(candidates)
    recoveries.sort(
        key=lambda row: (
            _legacy._recovery_cost(row, prices),
            int(row.get("popularity_rank") or 1_000_000),
            int(row.get("official_intelligence_rank") or 1_000_000),
            str(row.get("model") or ""),
        )
    )
    priorities: list[dict[str, Any]] = []
    priority_by_model: dict[str, int] = {}
    for slot, row in enumerate(recoveries, 1):
        row["slot"] = slot
        model_id = str(row["model"])
        score = int(_legacy._recovery_cost(row, prices))
        priority_by_model[model_id] = slot
        priorities.append(
            {
                "priority": slot,
                "model": model_id,
                "recovery_objective_score": score,
            }
        )
    plan["recovery_models"] = recoveries
    plan["warm_recovery_order_basis"] = "same-recovery-objective-as-ortools-selection"

    inventory_value = plan.get("expert_center_top50_inventory")
    if isinstance(inventory_value, Sequence) and not isinstance(inventory_value, (str, bytes)):
        inventory: list[dict[str, Any]] = []
        for row in inventory_value:
            record = dict(row) if isinstance(row, Mapping) else {}
            model_id = str(record.get("model") or "")
            if record.get("standby_state") == "warm-recovery":
                record["warm_recovery_priority"] = priority_by_model.get(model_id)
            inventory.append(record)
        plan["expert_center_top50_inventory"] = inventory
        plan["expert_center_top50_inventory_sha256"] = _legacy._sha(inventory)

    audit = dict(plan.get("optimizer_audit") or {})
    audit["warm_recovery_order_basis"] = "same-recovery-objective"
    audit["warm_recovery_priority"] = priorities
    plan["optimizer_audit"] = audit
    return plan


_legacy.POOL_SCHEMA_VERSION = POOL_SCHEMA_VERSION
_legacy.REQUIRED_EVIDENCE = REQUIRED_EVIDENCE
_legacy._validate_pool = _open_validate_pool
_legacy._metrics = _open_metrics


def materialize_top50_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _validate_top50_budget(packet)
    materialized, receipt = _legacy.materialize_top50_selection(packet)
    plan = _apply_recovery_priority(dict(materialized["governance_model_plan"]))
    audit = dict(plan.get("optimizer_audit") or {})
    constraints = dict(audit.get("constraints") or {})
    constraints["provider_resilience_used"] = False
    constraints["provider_routing_unrestricted"] = True
    constraints["four_primary_calls_reserved"] = True
    constraints["four_warm_recovery_calls_reserved"] = True
    constraints["warm_recovery_priority_uses_same_objective"] = True
    audit["constraints"] = constraints
    audit["provider_objective_weight"] = 0
    audit["provider_routing_mode"] = "unrestricted-openrouter"
    audit["approved_total_calls"] = int(packet["approved_budget"]["calls"])
    audit["approved_recovery_calls"] = WARM_RECOVERY_COUNT
    plan["optimizer_audit"] = audit
    plan["provider_routing_mode"] = "unrestricted-openrouter"
    plan["provider_restrictions_applied"] = False
    plan["selection_policy"] = (
        "weekly-top50-reasoning -> model-metadata-qualified -> "
        "ortools-four-active-four-warm-recovery -> warm-recovery-order-by-same-objective -> "
        "all-extra-qualified-models-as-ordered-standby -> unrestricted-openrouter-provider-routing"
    )
    updated_receipt = dict(plan.get("expert_center_selection_receipt") or receipt)
    updated_receipt["optimizer_audit"] = audit
    updated_receipt["recovery_models"] = [row["model"] for row in plan["recovery_models"]]
    updated_receipt["inventory_sha256"] = plan.get("expert_center_top50_inventory_sha256", "")
    updated_receipt["provider_routing_mode"] = "unrestricted-openrouter"
    updated_receipt["provider_restrictions_applied"] = False
    updated_receipt["approved_total_calls"] = int(packet["approved_budget"]["calls"])
    updated_receipt["approved_recovery_calls"] = WARM_RECOVERY_COUNT
    updated_receipt["warm_recovery_order_basis"] = "same-recovery-objective"
    updated_receipt.pop("receipt_sha256", None)
    updated_receipt["receipt_sha256"] = _legacy._sha(updated_receipt)
    plan["expert_center_selection_receipt"] = updated_receipt
    plan["plan_sha256"] = _legacy._plan_sha(plan)
    value = dict(materialized)
    value["governance_model_plan"] = plan
    return value, updated_receipt


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
