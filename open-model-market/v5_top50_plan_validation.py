"""Validate expert-center OR-Tools plans derived from a frozen top-50 pool."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

POOL_SCHEMA_VERSION = "governance-openrouter-top50-reasoning-pool-v1"
POOL_SOURCE = "openrouter-most-popular-last-week-token-volume"
REQUIRED_EVIDENCE = (
    "openrouter-top-weekly-reasoning",
    "live-exact-endpoint-qualified",
    "authenticated-zdr-endpoint-qualified",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class Top50PlanValidationError(RuntimeError):
    """Raised when a materialized top-50 plan violates its signed contract."""


def _sha(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rows(value: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise Top50PlanValidationError(f"{field} must be an array")
    rows = [row for row in value if isinstance(row, Mapping)]
    if len(rows) != len(value):
        raise Top50PlanValidationError(f"{field} contains a non-object entry")
    return rows


def _execution_row(
    row: Mapping[str, Any], field: str, index: int, raw_models: set[str]
) -> None:
    model = str(row.get("model") or "").strip()
    if model not in raw_models:
        raise Top50PlanValidationError(
            f"{field}[{index}] is outside the frozen top-50 pool"
        )
    rank = row.get("popularity_rank")
    if isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= 50:
        raise Top50PlanValidationError(
            f"{field}[{index}].popularity_rank is invalid"
        )
    if row.get("reasoning_rank_verified") is not True:
        raise Top50PlanValidationError(
            f"{field}[{index}] lacks reasoning-rank verification"
        )
    providers = row.get("qualified_provider_count")
    if isinstance(providers, bool) or not isinstance(providers, int) or providers < 1:
        raise Top50PlanValidationError(
            f"{field}[{index}] has no qualified provider"
        )
    endpoint_hash = str(row.get("endpoint_inventory_sha256") or "")
    if not SHA256_RE.fullmatch(endpoint_hash):
        raise Top50PlanValidationError(
            f"{field}[{index}] endpoint hash is invalid"
        )
    evidence = str(row.get("selection_evidence") or "")
    if any(fragment not in evidence for fragment in REQUIRED_EVIDENCE):
        raise Top50PlanValidationError(
            f"{field}[{index}] evidence is incomplete"
        )


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
        "model_assignment_authority": "expert-assessment-center-ortools",
        "expert_center_top50_pool_selection_allowed": True,
        "expert_center_top50_optimization_completed": True,
        "selected_from_top50_reasoning_pool_only": True,
        "all_top50_models_received_by_expert_center": True,
        "optimizer": "ortools-cp-sat",
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise Top50PlanValidationError(
                f"top-50 execution contract mismatch: {field}"
            )
    if len(selected) != 4 or len(recoveries) != 4:
        raise Top50PlanValidationError(
            "top-50 plan must contain four active and four warm recovery models"
        )

    raw = _rows(plan.get("top50_reasoning_models"), "top50_reasoning_models")
    eligible = _rows(
        plan.get("top50_expert_selectable_candidates"),
        "top50_expert_selectable_candidates",
    )
    if len(raw) != 50 or plan.get("top50_reasoning_pool_sha256") != _sha(raw):
        raise Top50PlanValidationError(
            "top-50 raw pool is incomplete or corrupted"
        )
    if plan.get("top50_expert_selectable_candidates_sha256") != _sha(eligible):
        raise Top50PlanValidationError(
            "top-50 selectable pool hash mismatch"
        )
    raw_models = {str(row.get("model") or "").strip() for row in raw}
    if len(raw_models) != 50:
        raise Top50PlanValidationError(
            "top-50 raw model identities are not unique"
        )

    ranked = _rows(plan.get("price_ranked_models"), "price_ranked_models")
    for field, rows in (
        ("selected_models", selected),
        ("recovery_models", recoveries),
        ("price_ranked_models", ranked),
    ):
        for index, row in enumerate(rows):
            _execution_row(row, field, index, raw_models)

    inventory = _rows(
        plan.get("expert_center_top50_inventory"),
        "expert_center_top50_inventory",
    )
    if len(inventory) != 50:
        raise Top50PlanValidationError(
            "top-50 standby inventory must retain all 50 models"
        )
    if plan.get("expert_center_top50_inventory_sha256") != _sha(inventory):
        raise Top50PlanValidationError(
            "top-50 standby inventory hash mismatch"
        )
    states = [str(row.get("standby_state") or "") for row in inventory]
    if states.count("active") != 4 or states.count("warm-recovery") != 4:
        raise Top50PlanValidationError(
            "top-50 inventory active/recovery state counts are invalid"
        )

    audit = plan.get("optimizer_audit")
    if not isinstance(audit, Mapping):
        raise Top50PlanValidationError("OR-Tools audit is missing")
    if (
        audit.get("optimizer") != "ortools-cp-sat"
        or audit.get("optimality_proven") is not True
    ):
        raise Top50PlanValidationError(
            "OR-Tools optimality proof is missing"
        )


__all__ = [
    "Top50PlanValidationError",
    "validate_top50_contract",
]
