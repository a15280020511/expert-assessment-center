"""Protocol validation for task-dynamic governance expert plans.

Business qualification gates are intentionally absent. The validator preserves
only protocol/integrity invariants: an accepted governance schema, governance
selection authority, task binding, canonical plan integrity and executable model
identities. Team size, company mix, role topology, pool membership, price,
flagship labels, budget, ranking source, optimizer status and recovery count are
not admission constraints.

Execution-plan integrity hashes every plan field except ``plan_sha256`` itself.
The validator may add deterministic v9 annotations during first normalization;
it then emits a new hash covering that normalized result. Re-validating that
normalized result is therefore idempotent and fail-closed.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

import v5_governance_model_plan_legacy as _legacy

GovernanceModelPlanError = _legacy.GovernanceModelPlanError
SCHEMA_VERSION = _legacy.SCHEMA_VERSION
DYNAMIC_SCHEMA_VERSION = "governance-expert-dynamic-candidate-plan-v1"
ALLOWED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION, DYNAMIC_SCHEMA_VERSION})
SELECTION_AUTHORITY = _legacy.SELECTION_AUTHORITY


def _canonical(value: Any, field: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GovernanceModelPlanError(
            f"{field} contains a non-canonical JSON value"
        ) from exc


def plan_sha256(plan: Mapping[str, Any]) -> str:
    """Hash the complete execution plan except the digest field itself."""
    value = dict(plan)
    value.pop("plan_sha256", None)
    return hashlib.sha256(_canonical(value, "governance model plan")).hexdigest()


def task_sha256(value: Any) -> str:
    if not isinstance(value, Mapping):
        raise GovernanceModelPlanError("ticket must be an object")
    task = value.get("task")
    if not isinstance(task, Mapping):
        raise GovernanceModelPlanError("ticket task object is missing")
    return hashlib.sha256(_canonical(task, "ticket task")).hexdigest()


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


def _validate_protocol_envelope(
    ticket: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> None:
    schema = str(plan.get("schema_version") or "").strip()
    if schema not in ALLOWED_SCHEMA_VERSIONS:
        allowed = ", ".join(sorted(ALLOWED_SCHEMA_VERSIONS))
        raise GovernanceModelPlanError(
            f"governance model plan schema_version is unsupported; allowed: {allowed}"
        )
    if str(plan.get("selection_authority") or "").strip() != SELECTION_AUTHORITY:
        raise GovernanceModelPlanError(
            "governance model plan selection_authority must be decision-system-governance"
        )
    expected_task_sha = str(plan.get("task_sha256") or "").strip()
    observed_task_sha = task_sha256(ticket)
    if expected_task_sha != observed_task_sha:
        raise GovernanceModelPlanError("governance model plan task hash mismatch")


def _validate_unique_model_identities(
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> None:
    seen: set[str] = set()
    for field, rows in (("selected_models", selected), ("recovery_models", recoveries)):
        for index, row in enumerate(rows):
            model = str(row.get("model") or "").strip()
            if model in seen:
                raise GovernanceModelPlanError(
                    f"duplicate model identity across execution graph: {field}[{index}]={model}"
                )
            seen.add(model)


def validate_governance_model_plan(
    ticket: Mapping[str, Any],
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    value = plan
    if value is None:
        candidate = ticket.get("governance_model_plan")
        if not isinstance(candidate, Mapping):
            raise GovernanceModelPlanError(
                "governance_model_plan is required; local governance-authority substitution is disabled"
            )
        value = candidate
    if not isinstance(value, Mapping):
        raise GovernanceModelPlanError("governance model plan must be an object")

    _validate_protocol_envelope(ticket, value)

    # Verify the producer's exact canonical execution plan before normalization.
    incoming_sha = str(value.get("plan_sha256") or "").strip()
    if not incoming_sha:
        raise GovernanceModelPlanError("governance model plan sha256 is missing")
    observed_sha = plan_sha256(value)
    if incoming_sha != observed_sha:
        raise GovernanceModelPlanError(
            "governance model plan sha256 mismatch: "
            f"expected {incoming_sha}, observed {observed_sha}"
        )

    normalized = dict(value)
    selected = _rows(normalized.get("selected_models"), "selected_models", required=True)
    recoveries = _rows(normalized.get("recovery_models"), "recovery_models", required=False)
    _validate_unique_model_identities(selected, recoveries)
    normalized["selected_models"] = selected
    normalized["recovery_models"] = recoveries
    normalized["expert_count"] = len(selected)
    normalized["recovery_count"] = len(recoveries)

    # v9 policy annotations. These are descriptive/non-admission controls, not
    # eligibility gates. Provider remains unrestricted and the Expert Center may
    # dynamically compose from governance-supplied candidates.
    normalized["fixed_team_size_required"] = False
    normalized["fixed_role_topology_required"] = False
    normalized["company_uniqueness_required"] = False
    normalized["candidate_pool_membership_required"] = False
    normalized["optimizer_optimality_required"] = False
    normalized["budget_admission_gate_enabled"] = False
    normalized["provider_routing_mode"] = "unrestricted-openrouter"
    normalized["provider_restrictions_applied"] = False
    normalized["model_substitution_allowed"] = True
    normalized["expert_center_reranking_allowed"] = True
    normalized["plan_sha256"] = plan_sha256(normalized)
    return normalized


__all__ = [
    "ALLOWED_SCHEMA_VERSIONS",
    "DYNAMIC_SCHEMA_VERSION",
    "GovernanceModelPlanError",
    "SCHEMA_VERSION",
    "SELECTION_AUTHORITY",
    "plan_sha256",
    "task_sha256",
    "validate_governance_model_plan",
]
