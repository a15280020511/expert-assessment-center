"""Task-adaptive Top-50 validation compatibility layer for governance model plans."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_governance_model_plan_legacy as _legacy
from v5_top50_plan_validation import (
    Top50PlanValidationError,
    validate_top50_contract,
)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


GovernanceModelPlanError = _legacy.GovernanceModelPlanError
SCHEMA_VERSION = _legacy.SCHEMA_VERSION
SELECTION_AUTHORITY = _legacy.SELECTION_AUTHORITY
plan_sha256 = _legacy.plan_sha256
task_sha256 = _legacy.task_sha256


def _validate_top50_identity_sets(
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> None:
    """Keep global model/company uniqueness without legacy static-price ordering."""
    models: set[str] = set()
    companies: set[str] = set()
    for field, rows in (("selected_models", selected), ("recovery_models", recoveries)):
        for index, row in enumerate(rows):
            model, company, _ = _legacy._validate_model_row(  # noqa: SLF001
                row,
                field=field,
                index=index,
            )
            if model in models:
                raise GovernanceModelPlanError(
                    f"duplicate model across task-global Top-50 assignment: {model}"
                )
            if company in companies:
                raise GovernanceModelPlanError(
                    f"duplicate model company across task-global Top-50 assignment: {company}"
                )
            if field == "recovery_models" and row.get("slot") != index + 1:
                raise GovernanceModelPlanError(
                    "recovery model slots must be contiguous"
                )
            models.add(model)
            companies.add(company)


def _validate_top50_plan(
    ticket: Mapping[str, Any],
    plan_value: Mapping[str, Any],
) -> dict[str, Any]:
    plan = dict(plan_value)
    _legacy._validate_plan_envelope(ticket, plan)  # noqa: SLF001
    selected = _legacy._model_rows(plan.get("selected_models"), "selected_models")  # noqa: SLF001
    recoveries = _legacy._model_rows(plan.get("recovery_models"), "recovery_models")  # noqa: SLF001
    expert_count, recovery_count = _legacy._validated_counts(  # noqa: SLF001
        plan,
        selected,
        recoveries,
    )
    _validate_top50_identity_sets(selected, recoveries)
    try:
        validate_top50_contract(plan, selected, recoveries)
    except Top50PlanValidationError as exc:
        raise GovernanceModelPlanError(str(exc)) from exc
    _legacy._validate_roles(selected)  # noqa: SLF001
    _legacy._validate_budget(ticket, expert_count, recovery_count)  # noqa: SLF001
    return plan


def validate_governance_model_plan(
    ticket: Mapping[str, Any],
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    plan_value = _legacy._require_plan(ticket, plan)  # noqa: SLF001
    if plan_value.get("selected_from_top50_reasoning_pool_only") is True:
        return _validate_top50_plan(ticket, plan_value)
    return _legacy.validate_governance_model_plan(ticket, plan_value)


__all__ = [
    "GovernanceModelPlanError",
    "SCHEMA_VERSION",
    "SELECTION_AUTHORITY",
    "plan_sha256",
    "task_sha256",
    "validate_governance_model_plan",
]
