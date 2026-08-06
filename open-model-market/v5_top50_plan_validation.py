"""Validate task-adaptive expert-center OR-Tools plans from an open-provider Top-50 pool."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

POOL_SCHEMA_VERSION = "governance-openrouter-top50-reasoning-pool-v2-open-provider"
POOL_SOURCE = "openrouter-most-popular-last-week-token-volume"
TASK_SCORING_SCHEMA_VERSION = "v5-task-adaptive-value-scoring-1"
SELECTION_PRINCIPLES = [
    "concrete-problem-concrete-analysis",
    "dynamic-adaptation",
    "small-effort-large-return",
]
REQUIRED_EVIDENCE = (
    "openrouter-top-weekly-reasoning",
    "model-metadata-qualified",
    "unrestricted-openrouter-provider-routing",
)


class Top50PlanValidationError(RuntimeError):
    pass


def _sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rows(value: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise Top50PlanValidationError(f"{field} must be an array")
    rows = [row for row in value if isinstance(row, Mapping)]
    if len(rows) != len(value):
        raise Top50PlanValidationError(f"{field} contains a non-object entry")
    return rows


def _execution_row(row: Mapping[str, Any], field: str, index: int, raw_models: set[str]) -> None:
    model = str(row.get("model") or "").strip()
    if model not in raw_models:
        raise Top50PlanValidationError(f"{field}[{index}] is outside the frozen top-50 pool")
    rank = row.get("popularity_rank")
    if isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= 50:
        raise Top50PlanValidationError(f"{field}[{index}].popularity_rank is invalid")
    if row.get("reasoning_rank_verified") is not True:
        raise Top50PlanValidationError(f"{field}[{index}] lacks reasoning-rank verification")
    if row.get("provider_routing_mode") != "unrestricted-openrouter":
        raise Top50PlanValidationError(f"{field}[{index}] restricts provider routing")
    if row.get("provider_restrictions_applied") is not False:
        raise Top50PlanValidationError(f"{field}[{index}] applies provider restrictions")
    evidence = str(row.get("selection_evidence") or "")
    if any(fragment not in evidence for fragment in REQUIRED_EVIDENCE):
        raise Top50PlanValidationError(f"{field}[{index}] evidence is incomplete")
    if field in {"selected_models", "recovery_models"}:
        if row.get("task_adaptive_capacity_compatible") is not True:
            raise Top50PlanValidationError(f"{field}[{index}] is not task-capacity compatible")
        score = row.get("task_adaptive_objective_score")
        if isinstance(score, bool) or not isinstance(score, int) or score < 0:
            raise Top50PlanValidationError(f"{field}[{index}] lacks task-adaptive objective score")
        if not isinstance(row.get("task_adaptive_ranks"), Mapping):
            raise Top50PlanValidationError(f"{field}[{index}] lacks task-adaptive ranks")
        if not isinstance(row.get("task_adaptive_weights"), Mapping):
            raise Top50PlanValidationError(f"{field}[{index}] lacks task-adaptive weights")


def validate_top50_contract(
    plan: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> None:
    expected = {
        "top50_reasoning_pool_schema_version": POOL_SCHEMA_VERSION,
        "top50_reasoning_pool_source": POOL_SOURCE,
        "top50_reasoning_pool_period": "week",
        "top50_reasoning_pool_size": 50,
        "top50_candidate_pool_authority": "decision-system-governance",
        "top50_model_assignment_authority": "expert-assessment-center-ortools",
        "top50_task_adaptive_assignment_required": True,
        "top50_assignment_recomputed_from_current_task": True,
        "top50_cross_task_history_allowed": False,
        "top50_semantic_keyword_routing_allowed": False,
        "top50_domain_hardcoding_allowed": False,
        "top50_provider_metric_allowed_in_assignment": False,
        "model_assignment_authority": "expert-assessment-center-ortools",
        "expert_center_top50_pool_selection_allowed": True,
        "expert_center_top50_optimization_completed": True,
        "selected_from_top50_reasoning_pool_only": True,
        "all_top50_models_received_by_expert_center": True,
        "optimizer": "ortools-cp-sat",
        "task_adaptive_scoring_completed": True,
        "task_adaptive_scoring_schema_version": TASK_SCORING_SCHEMA_VERSION,
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise Top50PlanValidationError(f"top-50 execution contract mismatch: {field}")
    if plan.get("top50_model_assignment_principles") != SELECTION_PRINCIPLES:
        raise Top50PlanValidationError("signed governance assignment principles are missing")
    if plan.get("selection_principles") != SELECTION_PRINCIPLES:
        raise Top50PlanValidationError("task-adaptive selection principles are missing")
    if len(selected) != 4 or len(recoveries) != 4:
        raise Top50PlanValidationError("top-50 plan must contain four active and four warm recovery models")

    profile = plan.get("task_demand_profile")
    if not isinstance(profile, Mapping):
        raise Top50PlanValidationError("task demand profile is missing")
    if profile.get("schema_version") != TASK_SCORING_SCHEMA_VERSION:
        raise Top50PlanValidationError("task demand profile schema is invalid")
    if profile.get("principles") != SELECTION_PRINCIPLES:
        raise Top50PlanValidationError("task demand profile principles are invalid")
    for field in (
        "semantic_keyword_routing_used",
        "domain_hardcoding_used",
        "cross_task_history_used",
        "provider_metric_used",
    ):
        if profile.get(field) is not False:
            raise Top50PlanValidationError(f"task demand profile violates {field}")

    raw = _rows(plan.get("top50_reasoning_models"), "top50_reasoning_models")
    eligible = _rows(plan.get("top50_expert_selectable_candidates"), "top50_expert_selectable_candidates")
    if len(raw) != 50 or plan.get("top50_reasoning_pool_sha256") != _sha(raw):
        raise Top50PlanValidationError("top-50 raw pool is incomplete or corrupted")
    if plan.get("top50_expert_selectable_candidates_sha256") != _sha(eligible):
        raise Top50PlanValidationError("top-50 selectable pool hash mismatch")
    raw_models = {str(row.get("model") or "").strip() for row in raw}
    if len(raw_models) != 50:
        raise Top50PlanValidationError("top-50 raw model identities are not unique")

    ranked = _rows(plan.get("price_ranked_models"), "price_ranked_models")
    for field, rows in (("selected_models", selected), ("recovery_models", recoveries), ("price_ranked_models", ranked)):
        for index, row in enumerate(rows):
            _execution_row(row, field, index, raw_models)

    inventory = _rows(plan.get("expert_center_top50_inventory"), "expert_center_top50_inventory")
    if len(inventory) != 50:
        raise Top50PlanValidationError("top-50 standby inventory must retain all 50 models")
    if plan.get("expert_center_top50_inventory_sha256") != _sha(inventory):
        raise Top50PlanValidationError("top-50 standby inventory hash mismatch")
    states = [str(row.get("standby_state") or "") for row in inventory]
    if states.count("active") != 4 or states.count("warm-recovery") != 4:
        raise Top50PlanValidationError("top-50 inventory active/recovery state counts are invalid")

    audit = plan.get("optimizer_audit")
    if not isinstance(audit, Mapping):
        raise Top50PlanValidationError("OR-Tools audit is missing")
    constraints = audit.get("constraints") if isinstance(audit.get("constraints"), Mapping) else {}
    if audit.get("optimizer") != "ortools-cp-sat" or audit.get("optimality_proven") is not True:
        raise Top50PlanValidationError("OR-Tools optimality proof is missing")
    if audit.get("provider_routing_mode") != "unrestricted-openrouter":
        raise Top50PlanValidationError("optimizer provider routing mode is not open")
    if audit.get("selection_principles") != SELECTION_PRINCIPLES:
        raise Top50PlanValidationError("optimizer selection principles are missing")
    if audit.get("task_adaptive_scoring_schema_version") != TASK_SCORING_SCHEMA_VERSION:
        raise Top50PlanValidationError("optimizer task-adaptive schema is invalid")
    if constraints.get("provider_resilience_used") is not False:
        raise Top50PlanValidationError("provider resilience must not affect model assignment")
    for field in (
        "task_role_native_capacity_compatibility",
        "dynamic_role_weights_used",
        "marginal_return_used",
        "warm_recovery_priority_uses_same_objective",
    ):
        if constraints.get(field) is not True:
            raise Top50PlanValidationError(f"optimizer constraint evidence missing: {field}")
    if constraints.get("semantic_keyword_routing_used") is not False:
        raise Top50PlanValidationError("semantic keyword routing must remain disabled")
    if constraints.get("cross_task_history_used") is not False:
        raise Top50PlanValidationError("cross-task history must remain disabled")


__all__ = ["Top50PlanValidationError", "validate_top50_contract"]
