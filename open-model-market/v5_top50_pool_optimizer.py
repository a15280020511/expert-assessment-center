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
    if len(companies) < _legacy.PRIMARY_COUNT + _legacy.RECOVERY_COUNT:
        raise Top50PoolOptimizationError("top-50 pool has fewer than eight distinct executable companies")
    if plan.get("top50_expert_selectable_distinct_company_count") != len(companies):
        raise Top50PoolOptimizationError("top-50 distinct-company count mismatch")
    return raw, eligible


def _open_metrics(row: Mapping[str, Any], prices: Mapping[str, int]) -> tuple[int, int, int, int]:
    price = prices[str(row["model"])]
    popularity = min(10_000, int(row.get("popularity_rank") or 10_000))
    intelligence = min(10_000, int(row.get("official_intelligence_rank") or 10_000))
    return price, popularity, intelligence, 0


_legacy.POOL_SCHEMA_VERSION = POOL_SCHEMA_VERSION
_legacy.REQUIRED_EVIDENCE = REQUIRED_EVIDENCE
_legacy._validate_pool = _open_validate_pool
_legacy._metrics = _open_metrics


def materialize_top50_selection(
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    materialized, receipt = _legacy.materialize_top50_selection(packet)
    plan = dict(materialized["governance_model_plan"])
    audit = dict(plan.get("optimizer_audit") or {})
    constraints = dict(audit.get("constraints") or {})
    constraints["provider_resilience_used"] = False
    constraints["provider_routing_unrestricted"] = True
    audit["constraints"] = constraints
    audit["provider_objective_weight"] = 0
    audit["provider_routing_mode"] = "unrestricted-openrouter"
    plan["optimizer_audit"] = audit
    plan["provider_routing_mode"] = "unrestricted-openrouter"
    plan["provider_restrictions_applied"] = False
    plan["selection_policy"] = (
        "weekly-top50-reasoning -> model-metadata-qualified -> "
        "ortools-four-active-four-warm-recovery -> all-extra-qualified-models-"
        "as-ordered-standby -> unrestricted-openrouter-provider-routing"
    )
    updated_receipt = dict(plan.get("expert_center_selection_receipt") or receipt)
    updated_receipt["optimizer_audit"] = audit
    updated_receipt["provider_routing_mode"] = "unrestricted-openrouter"
    updated_receipt["provider_restrictions_applied"] = False
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
