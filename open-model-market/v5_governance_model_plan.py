"""Structural validation for task-dynamic governance expert plans.

Business qualification gates are intentionally absent. The validator checks
only that a plan is representable and contains executable model identities;
team size, company mix, role topology, pool membership, budget, ranking source,
optimizer status and recovery count are not admission constraints.

Plan integrity uses a canonical execution-plan hash. A small set of fields below
are validator annotations: they are deterministic consequences of the v9 policy,
not governance-authored execution content, so adding those annotations must not
change an already valid plan hash.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

import v5_governance_model_plan_legacy as _legacy

GovernanceModelPlanError = _legacy.GovernanceModelPlanError
SCHEMA_VERSION = _legacy.SCHEMA_VERSION
SELECTION_AUTHORITY = _legacy.SELECTION_AUTHORITY

_VALIDATOR_ANNOTATIONS = frozenset(
    {
        "company_uniqueness_required",
        "candidate_pool_membership_required",
        "optimizer_optimality_required",
        "budget_admission_gate_enabled",
    }
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def plan_sha256(plan: Mapping[str, Any]) -> str:
    """Hash governance/execution content, excluding deterministic annotations."""
    value = dict(plan)
    value.pop("plan_sha256", None)
    for field in _VALIDATOR_ANNOTATIONS:
        value.pop(field, None)
    return hashlib.sha256(_canonical(value)).hexdigest()


def task_sha256(value: Any) -> str:
    if isinstance(value, Mapping):
        task = value.get("task", value)
    else:
        task = value
    return hashlib.sha256(_canonical(task)).hexdigest()


def _rows(value: Any, field: str, *, required: bool) -> list[dict[str, Any]]:
    if value is None and not required:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise GovernanceModelPlanError(f"{field} must be an array")
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise GovernanceModelPlanError(f"{field}[{index}] must be an object")
        row = dict(raw)
        model = str(row.get("model") or row.get("id") or "").strip()
        if not model:
            raise GovernanceModelPlanError(f"{field}[{index}] model is missing")
        row["model"] = model
        row.setdefault(
            "company", model.split("/", 1)[0] if "/" in model else "unknown"
        )
        row.setdefault("slot", index + 1)
        rows.append(row)
    if required and not rows:
        raise GovernanceModelPlanError("selected_models must contain at least one expert")
    return rows


def validate_governance_model_plan(
    ticket: Mapping[str, Any],
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    value = plan
    if value is None:
        candidate = ticket.get("governance_model_plan")
        if not isinstance(candidate, Mapping):
            raise GovernanceModelPlanError("governance_model_plan is missing")
        value = candidate
    if not isinstance(value, Mapping):
        raise GovernanceModelPlanError("governance model plan must be an object")

    # Verify an existing execution-plan hash before normalization. This prevents
    # validation from silently replacing a tampered hash with a new valid hash.
    incoming_sha = str(value.get("plan_sha256") or "").strip()
    if incoming_sha:
        observed_sha = plan_sha256(value)
        if incoming_sha != observed_sha:
            raise GovernanceModelPlanError(
                "governance model plan sha256 mismatch: "
                f"expected {incoming_sha}, observed {observed_sha}"
            )

    normalized = dict(value)
    selected = _rows(normalized.get("selected_models"), "selected_models", required=True)
    recoveries = _rows(normalized.get("recovery_models"), "recovery_models", required=False)
    normalized["selected_models"] = selected
    normalized["recovery_models"] = recoveries
    normalized["expert_count"] = len(selected)
    normalized["recovery_count"] = len(recoveries)
    normalized.setdefault("selection_authority", "expert-assessment-center-dynamic")
    normalized["fixed_team_size_required"] = False
    normalized["fixed_role_topology_required"] = False
    normalized["company_uniqueness_required"] = False
    normalized["candidate_pool_membership_required"] = False
    normalized["optimizer_optimality_required"] = False
    normalized["budget_admission_gate_enabled"] = False
    normalized["provider_routing_mode"] = "unrestricted-openrouter"
    normalized["provider_restrictions_applied"] = False
    normalized["model_substitution_allowed"] = True
    normalized["plan_sha256"] = plan_sha256(normalized)
    return normalized


__all__ = [
    "GovernanceModelPlanError",
    "SCHEMA_VERSION",
    "SELECTION_AUTHORITY",
    "plan_sha256",
    "task_sha256",
    "validate_governance_model_plan",
]
