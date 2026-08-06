"""Top-50 validation compatibility layer for governance model plans."""
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

_legacy_live_validator = _legacy._validate_live_flagship_contract


def _validate_live_or_top50_contract(
    plan: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> None:
    if plan.get("selected_from_top50_reasoning_pool_only") is True:
        try:
            validate_top50_contract(plan, selected, recoveries)
        except Top50PlanValidationError as exc:
            raise _legacy.GovernanceModelPlanError(str(exc)) from exc
        return
    _legacy_live_validator(plan, selected, recoveries)


_legacy._validate_live_flagship_contract = _validate_live_or_top50_contract

GovernanceModelPlanError = _legacy.GovernanceModelPlanError
SCHEMA_VERSION = _legacy.SCHEMA_VERSION
SELECTION_AUTHORITY = _legacy.SELECTION_AUTHORITY
plan_sha256 = _legacy.plan_sha256
task_sha256 = _legacy.task_sha256
validate_governance_model_plan = _legacy.validate_governance_model_plan

__all__ = [
    "GovernanceModelPlanError",
    "SCHEMA_VERSION",
    "SELECTION_AUTHORITY",
    "plan_sha256",
    "task_sha256",
    "validate_governance_model_plan",
]
