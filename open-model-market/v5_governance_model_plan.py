"""Validation contract for governance-owned expert-model plans."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "governance-expert-model-plan-v1"
SELECTION_AUTHORITY = "decision-system-governance"
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._:/-]+$")
ROLE_KINDS = {"independent", "review", "synthesis"}


class GovernanceModelPlanError(RuntimeError):
    """Raised when the governance model plan is missing or invalid."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any, field: str) -> str:
    try:
        payload = _canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceModelPlanError(
            f"{field} contains a non-canonical JSON value"
        ) from exc
    return hashlib.sha256(payload).hexdigest()


def task_sha256(ticket: Mapping[str, Any]) -> str:
    task = ticket.get("task")
    if not isinstance(task, Mapping):
        raise GovernanceModelPlanError("ticket task object is missing")
    return _sha256(task, "ticket task")


def plan_sha256(plan: Mapping[str, Any]) -> str:
    material = dict(plan)
    material.pop("plan_sha256", None)
    return _sha256(material, "governance model plan")


def _positive_finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise GovernanceModelPlanError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceModelPlanError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise GovernanceModelPlanError(f"{field} must be finite and nonnegative")
    return number


def _model_rows(value: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise GovernanceModelPlanError(f"{field} must be an array")
    rows = [row for row in value if isinstance(row, Mapping)]
    if len(rows) != len(value):
        raise GovernanceModelPlanError(f"{field} contains a non-object entry")
    return rows


def _require_plan(
    ticket: Mapping[str, Any], plan: Mapping[str, Any] | None
) -> dict[str, Any]:
    value = plan if plan is not None else ticket.get("governance_model_plan")
    if not isinstance(value, Mapping):
        raise GovernanceModelPlanError(
            "governance_model_plan is required; expert-center local selection is disabled"
        )
    return dict(value)


def _validate_plan_envelope(
    ticket: Mapping[str, Any], plan: Mapping[str, Any]
) -> None:
    expected = {
        "schema_version": SCHEMA_VERSION,
        "selection_authority": SELECTION_AUTHORITY,
        "model_substitution_allowed": False,
        "expert_center_reranking_allowed": False,
        "task_sha256": task_sha256(ticket),
        "plan_sha256": plan_sha256(plan),
    }
    messages = {
        "schema_version": f"governance_model_plan.schema_version must be {SCHEMA_VERSION}",
        "selection_authority": (
            "governance_model_plan.selection_authority must be "
            "decision-system-governance"
        ),
        "model_substitution_allowed": (
            "model substitution must be explicitly disabled"
        ),
        "expert_center_reranking_allowed": (
            "expert-center reranking must be explicitly disabled"
        ),
        "task_sha256": "governance model plan task hash mismatch",
        "plan_sha256": "governance model plan digest mismatch",
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise GovernanceModelPlanError(messages[field])


def _validated_counts(
    plan: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> tuple[int, int]:
    expert_count = plan.get("expert_count")
    recovery_count = plan.get("recovery_count")
    if isinstance(expert_count, bool) or not isinstance(expert_count, int):
        raise GovernanceModelPlanError("expert_count must be an integer")
    if isinstance(recovery_count, bool) or not isinstance(recovery_count, int):
        raise GovernanceModelPlanError("recovery_count must be an integer")
    if not 3 <= expert_count <= 6 or expert_count != len(selected):
        raise GovernanceModelPlanError(
            "expert_count must equal 3-6 selected model entries"
        )
    if not 0 <= recovery_count <= 4 or recovery_count != len(recoveries):
        raise GovernanceModelPlanError(
            "recovery_count must equal 0-4 recovery model entries"
        )
    return expert_count, recovery_count


def _validate_identity_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    selected_companies: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    models: set[str] = set()
    companies = set(selected_companies or ())
    new_companies: set[str] = set()
    for index, row in enumerate(rows):
        model = str(row.get("model") or "").strip()
        company = str(row.get("company") or "").strip()
        if not MODEL_ID_RE.fullmatch(model):
            raise GovernanceModelPlanError(f"{field}[{index}].model is invalid")
        if not company:
            raise GovernanceModelPlanError(f"{field}[{index}].company is missing")
        if model in models:
            raise GovernanceModelPlanError(f"duplicate model in {field}: {model}")
        if company in companies:
            raise GovernanceModelPlanError(
                f"duplicate or reused model company in {field}: {company}"
            )
        _positive_finite(
            row.get("estimated_task_cost_usd"),
            f"{field}[{index}].estimated_task_cost_usd",
        )
        models.add(model)
        companies.add(company)
        new_companies.add(company)
    return models, new_companies


def _validate_model_sets(
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> None:
    selected_models, selected_companies = _validate_identity_rows(
        selected,
        field="selected_models",
    )
    recovery_models, _ = _validate_identity_rows(
        recoveries,
        field="recovery_models",
        selected_companies=selected_companies,
    )
    if selected_models & recovery_models:
        raise GovernanceModelPlanError("selected and recovery model sets overlap")


def _validate_roles(selected: Sequence[Mapping[str, Any]]) -> None:
    role_kinds: list[str] = []
    role_ids: set[str] = set()
    for index, row in enumerate(selected):
        role_id = str(row.get("role_id") or "").strip()
        role_kind = str(row.get("role_kind") or "").strip()
        role = str(row.get("role") or "").strip()
        if row.get("slot") != index + 1:
            raise GovernanceModelPlanError("selected model slots must be contiguous")
        if not role_id or role_id in role_ids:
            raise GovernanceModelPlanError("selected model role_ids must be unique")
        if role_kind not in ROLE_KINDS or not role:
            raise GovernanceModelPlanError(
                f"selected_models[{index}] has an invalid role contract"
            )
        role_ids.add(role_id)
        role_kinds.append(role_kind)
    if role_kinds.count("review") != 1 or role_kinds.count("synthesis") != 1:
        raise GovernanceModelPlanError(
            "plan must contain exactly one review and one synthesis role"
        )
    if role_kinds.count("independent") < 1:
        raise GovernanceModelPlanError("plan must contain an independent expert")
    if role_kinds[-2:] != ["review", "synthesis"]:
        raise GovernanceModelPlanError(
            "review and synthesis must be the final two selected slots"
        )


def _validate_budget(
    ticket: Mapping[str, Any], expert_count: int, recovery_count: int
) -> None:
    budget = ticket.get("approved_budget")
    budget = budget if isinstance(budget, Mapping) else {}
    total_calls = budget.get("calls")
    recovery_budget = budget.get("maximum_recovery_calls")
    invalid_total = isinstance(total_calls, bool) or not isinstance(total_calls, int)
    if invalid_total or expert_count + recovery_count > int(total_calls or 0):
        raise GovernanceModelPlanError("model plan exceeds approved call capacity")
    if recovery_budget != recovery_count:
        raise GovernanceModelPlanError(
            "governance recovery model count must equal the approved recovery reserve"
        )


def validate_governance_model_plan(
    ticket: Mapping[str, Any],
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    plan_value = _require_plan(ticket, plan)
    _validate_plan_envelope(ticket, plan_value)
    selected = _model_rows(plan_value.get("selected_models"), "selected_models")
    recoveries = _model_rows(plan_value.get("recovery_models"), "recovery_models")
    expert_count, recovery_count = _validated_counts(
        plan_value, selected, recoveries
    )
    _validate_model_sets(selected, recoveries)
    _validate_roles(selected)
    _validate_budget(ticket, expert_count, recovery_count)
    return plan_value


__all__ = [
    "GovernanceModelPlanError",
    "SCHEMA_VERSION",
    "SELECTION_AUTHORITY",
    "plan_sha256",
    "task_sha256",
    "validate_governance_model_plan",
]
