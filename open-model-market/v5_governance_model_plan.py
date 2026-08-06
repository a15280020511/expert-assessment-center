"""Validation contract for governance-frozen expert model plans.

Legacy governance-selected plans remain supported. New production plans freeze
an OpenRouter top-50 reasoning pool in governance and let the expert center use
OR-Tools CP-SAT to assign four active roles. Every remaining qualified model is
retained as ordered recovery inventory; ``recovery_count`` is the approved call
ceiling rather than the inventory length.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "governance-expert-model-plan-v1"
SELECTION_AUTHORITY = "decision-system-governance"
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._:/-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ROLE_KINDS = {"independent", "review", "synthesis"}
LIVE_FETCH_MODE = "live-per-task-no-cross-task-cache"
BENCHMARK_SOURCE = "artificial-analysis-via-openrouter"
FLAGSHIP_DEFINITION = "strict-product-tier-or-benchmarked-company-natural-top-layer"
COMPANY_MODEL_POLICY = (
    "one-highest-intelligence-verified-reasoning-flagship-per-company-then-price-rank"
)
TOP20_POOL_SCHEMA_VERSION = "governance-openrouter-top20-reasoning-pool-v1"
TOP50_POOL_SCHEMA_VERSION = "governance-openrouter-top50-reasoning-pool-v1"
POOL_SOURCE = "openrouter-most-popular-last-week-token-volume"
TOP20_POOL_SIZE = 20
TOP50_POOL_SIZE = 50
TOP_POOL_REQUIRED_EVIDENCE = (
    "openrouter-top-weekly-reasoning",
    "live-exact-endpoint-qualified",
    "authenticated-zdr-endpoint-qualified",
)
ALLOWED_FLAGSHIP_BASES = {
    "strict-product-tier",
    "company-local-natural-top-layer",
}
ECONOMY_MODEL_RE = re.compile(
    r"(?:^|[-_/:])(luna|flash|mini|nano|micro|small|lite|fast|instant|"
    r"turbo|haiku|spark)(?:$|[-_/:0-9])",
    re.IGNORECASE,
)
SPECIALIZED_MODEL_RE = re.compile(
    r"(?:^|[-_/:])(coder|code|safety|guard|embedding|embed|rerank|"
    r"moderation|search)(?:$|[-_/:0-9])",
    re.IGNORECASE,
)


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


def _finite_nonnegative(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise GovernanceModelPlanError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceModelPlanError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise GovernanceModelPlanError(f"{field} must be finite and nonnegative")
    return number


def _positive_int(value: Any, field: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GovernanceModelPlanError(f"{field} must be an integer")
    if value < 0 or (not allow_zero and value == 0):
        raise GovernanceModelPlanError(f"{field} is outside the allowed range")
    return value


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


def _is_top50(plan: Mapping[str, Any]) -> bool:
    return plan.get("selected_from_top50_reasoning_pool_only") is True


def _is_top20(plan: Mapping[str, Any]) -> bool:
    return plan.get("selected_from_top20_reasoning_pool_only") is True


def _validate_plan_envelope(
    ticket: Mapping[str, Any], plan: Mapping[str, Any]
) -> None:
    expected_reranking = True if _is_top50(plan) else False
    expected = {
        "schema_version": SCHEMA_VERSION,
        "selection_authority": SELECTION_AUTHORITY,
        "model_substitution_allowed": False,
        "expert_center_reranking_allowed": expected_reranking,
        "task_sha256": task_sha256(ticket),
        "plan_sha256": plan_sha256(plan),
    }
    messages = {
        "schema_version": f"governance_model_plan.schema_version must be {SCHEMA_VERSION}",
        "selection_authority": (
            "governance_model_plan.selection_authority must be decision-system-governance"
        ),
        "model_substitution_allowed": "model substitution must be explicitly disabled",
        "expert_center_reranking_allowed": (
            "expert-center reranking policy does not match the plan mode"
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
    expert_count = _positive_int(plan.get("expert_count"), "expert_count")
    recovery_count = _positive_int(
        plan.get("recovery_count"), "recovery_count", allow_zero=True
    )
    if not 3 <= expert_count <= 6 or expert_count != len(selected):
        raise GovernanceModelPlanError(
            "expert_count must equal 3-6 selected model entries"
        )
    if _is_top50(plan):
        if recovery_count > len(recoveries):
            raise GovernanceModelPlanError(
                "recovery call ceiling exceeds recovery inventory"
            )
        inventory_count = plan.get("recovery_inventory_count")
        if inventory_count != len(recoveries):
            raise GovernanceModelPlanError(
                "recovery_inventory_count must equal recovery model inventory length"
            )
    elif recovery_count != len(recoveries) or not 0 <= recovery_count <= 4:
        raise GovernanceModelPlanError(
            "recovery_count must equal 0-4 recovery model entries"
        )
    return expert_count, recovery_count


def _validate_model_row(
    row: Mapping[str, Any], *, field: str, index: int
) -> tuple[str, str, float]:
    model = str(row.get("model") or "").strip()
    company = str(row.get("company") or "").strip().casefold()
    if not MODEL_ID_RE.fullmatch(model):
        raise GovernanceModelPlanError(f"{field}[{index}].model is invalid")
    if not company:
        raise GovernanceModelPlanError(f"{field}[{index}].company is missing")
    cost = _finite_nonnegative(
        row.get("estimated_task_cost_usd"),
        f"{field}[{index}].estimated_task_cost_usd",
    )
    return model, company, cost


def _validate_selected_rows(
    selected: Sequence[Mapping[str, Any]],
) -> tuple[set[str], set[str]]:
    models: set[str] = set()
    companies: set[str] = set()
    for index, row in enumerate(selected):
        model, company, _ = _validate_model_row(
            row, field="selected_models", index=index
        )
        if model in models:
            raise GovernanceModelPlanError(
                f"duplicate model in selected_models: {model}"
            )
        if company in companies:
            raise GovernanceModelPlanError(
                f"duplicate or reused model company in selected_models: {company}"
            )
        models.add(model)
        companies.add(company)
    return models, companies


def _recovery_order_value(row: Mapping[str, Any], index: int) -> float:
    value = row.get("price_rank_usd_per_million")
    if value is None:
        value = row.get("estimated_task_cost_usd")
    return _finite_nonnegative(
        value, f"recovery_models[{index}].price_rank_usd_per_million"
    )


def _validate_recovery_rows(
    plan: Mapping[str, Any],
    recoveries: Sequence[Mapping[str, Any]],
    selected_models: set[str],
    selected_companies: set[str],
) -> set[str]:
    models = set(selected_models)
    companies = set(selected_companies)
    recovery_models: set[str] = set()
    previous_price: float | None = None
    top50 = _is_top50(plan)
    for index, row in enumerate(recoveries):
        model, company, _ = _validate_model_row(
            row, field="recovery_models", index=index
        )
        if row.get("slot") != index + 1:
            raise GovernanceModelPlanError(
                "recovery model slots must be contiguous"
            )
        if model in models:
            raise GovernanceModelPlanError(
                f"selected and recovery model sets overlap or repeat: {model}"
            )
        if top50:
            if row.get("recovery_priority") != index + 1:
                raise GovernanceModelPlanError(
                    "top-50 recovery priorities must be contiguous"
                )
        else:
            if company in companies:
                raise GovernanceModelPlanError(
                    "duplicate or reused model company across full ranking: "
                    f"{company}"
                )
            price = _recovery_order_value(row, index)
            if previous_price is not None and price < previous_price - 1e-12:
                raise GovernanceModelPlanError(
                    "recovery models must preserve governance price order"
                )
            previous_price = price
        models.add(model)
        companies.add(company)
        recovery_models.add(model)
    return recovery_models


def _validate_model_sets(
    plan: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> None:
    selected_models, selected_companies = _validate_selected_rows(selected)
    _validate_recovery_rows(
        plan,
        recoveries,
        selected_models,
        selected_companies,
    )


def _validate_price_ranking(
    plan: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> None:
    ranked_value = plan.get("price_ranked_models")
    live_contract = plan.get("catalog_fetch_mode") == LIVE_FETCH_MODE
    if ranked_value is None and not live_contract and not _is_top50(plan):
        return
    ranked = _model_rows(ranked_value, "price_ranked_models")
    expected_count = len(selected) + len(recoveries)
    if len(ranked) != expected_count:
        raise GovernanceModelPlanError(
            "price_ranked_models must cover every selected and recovery model"
        )
    expected_models = {
        str(row.get("model") or "") for row in [*selected, *recoveries]
    }
    seen_models: set[str] = set()
    seen_companies: set[str] = set()
    previous_price: float | None = None
    for index, row in enumerate(ranked):
        model, company, _ = _validate_model_row(
            row, field="price_ranked_models", index=index
        )
        if row.get("price_rank") != index + 1:
            raise GovernanceModelPlanError(
                "price_ranked_models ranks must be contiguous"
            )
        if model in seen_models:
            raise GovernanceModelPlanError(
                "price ranking contains a repeated model"
            )
        if not _is_top50(plan):
            if company in seen_companies:
                raise GovernanceModelPlanError(
                    "price ranking must contain one model per company"
                )
            price = _finite_nonnegative(
                row.get("price_rank_usd_per_million"),
                f"price_ranked_models[{index}].price_rank_usd_per_million",
            )
            if previous_price is not None and price < previous_price - 1e-12:
                raise GovernanceModelPlanError(
                    "price_ranked_models must preserve ascending price order"
                )
            previous_price = price
        seen_models.add(model)
        seen_companies.add(company)
    if seen_models != expected_models:
        raise GovernanceModelPlanError(
            "price ranking does not match selected and recovery model identities"
        )


def _validate_roles(selected: Sequence[Mapping[str, Any]]) -> None:
    role_kinds: list[str] = []
    role_ids: set[str] = set()
    for index, row in enumerate(selected):
        role_id = str(row.get("role_id") or "").strip()
        role_kind = str(row.get("role_kind") or "").strip()
        role = str(row.get("role") or "").strip()
        if row.get("slot") != index + 1:
            raise GovernanceModelPlanError(
                "selected model slots must be contiguous"
            )
        if not role_id or role_id in role_ids:
            raise GovernanceModelPlanError(
                "selected model role_ids must be unique"
            )
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
        raise GovernanceModelPlanError(
            "plan must contain an independent expert"
        )
    if role_kinds[-2:] != ["review", "synthesis"]:
        raise GovernanceModelPlanError(
            "review and synthesis must be the final two selected slots"
        )


def _validate_pool_source(
    plan: Mapping[str, Any], *, size: int, prefix: str, schema: str
) -> tuple[set[str], set[str]]:
    expected = {
        f"{prefix}_reasoning_pool_schema_version": schema,
        f"{prefix}_reasoning_pool_source": POOL_SOURCE,
        f"{prefix}_reasoning_pool_size": size,
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center",
        "expert_center_pool_selection_allowed": True,
        "expert_center_pool_selection_completed": True,
        f"selected_from_{prefix}_reasoning_pool_only": True,
        "model_calls": 0,
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise GovernanceModelPlanError(
                f"{prefix.replace('top', 'top-')} governance contract mismatch: {field}"
            )
    raw_field = f"{prefix}_reasoning_models"
    hash_field = f"{prefix}_reasoning_pool_sha256"
    raw = _model_rows(plan.get(raw_field), raw_field)
    eligible = _model_rows(
        plan.get("expert_selectable_candidates"),
        "expert_selectable_candidates",
    )
    if len(raw) != size:
        raise GovernanceModelPlanError(
            f"{prefix.replace('top', 'top-')} source pool must contain {size} models"
        )
    if plan.get(hash_field) != _sha256(raw, raw_field):
        raise GovernanceModelPlanError(
            f"{prefix.replace('top', 'top-')} source pool hash mismatch"
        )
    if plan.get("expert_selectable_candidates_sha256") != _sha256(
        eligible, "expert_selectable_candidates"
    ):
        raise GovernanceModelPlanError(
            f"{prefix.replace('top', 'top-')} selectable candidate hash mismatch"
        )
    raw_models: set[str] = set()
    raw_companies: set[str] = set()
    for index, row in enumerate(raw, 1):
        model = str(row.get("model") or "").strip()
        company = str(row.get("company") or "").strip().casefold()
        if row.get("popularity_rank") != index:
            raise GovernanceModelPlanError(
                f"{prefix.replace('top', 'top-')} source popularity ranks must be contiguous"
            )
        if not MODEL_ID_RE.fullmatch(model) or not company or model in raw_models:
            raise GovernanceModelPlanError(
                f"{prefix.replace('top', 'top-')} source pool identity is invalid"
            )
        if row.get("reasoning_supported") is not True:
            raise GovernanceModelPlanError(
                f"{prefix.replace('top', 'top-')} source pool contains a non-reasoning model"
            )
        raw_models.add(model)
        raw_companies.add(company)
    eligible_companies = {
        str(row.get("company") or "").strip().casefold()
        for row in eligible
        if str(row.get("company") or "").strip()
    }
    if len(eligible_companies) < 4:
        raise GovernanceModelPlanError(
            f"{prefix.replace('top', 'top-')} selectable pool has fewer than four distinct companies"
        )
    if plan.get("expert_selectable_distinct_company_count") != len(
        eligible_companies
    ):
        raise GovernanceModelPlanError(
            f"{prefix.replace('top', 'top-')} selectable distinct-company count mismatch"
        )
    return raw_models, raw_companies


def _validate_pool_execution_row(
    row: Mapping[str, Any],
    *,
    field: str,
    index: int,
    raw_models: set[str],
    size: int,
) -> None:
    model, _, _ = _validate_model_row(row, field=field, index=index)
    if model not in raw_models:
        raise GovernanceModelPlanError(
            f"{field}[{index}] is outside the frozen top-{size} source pool"
        )
    if row.get("reasoning_rank_verified") is not True:
        raise GovernanceModelPlanError(
            f"{field}[{index}] lacks top-weekly reasoning rank verification"
        )
    rank = row.get("popularity_rank")
    if isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= size:
        raise GovernanceModelPlanError(
            f"{field}[{index}].popularity_rank is invalid"
        )
    endpoint_hash = str(row.get("endpoint_inventory_sha256") or "")
    if not SHA256_RE.fullmatch(endpoint_hash):
        raise GovernanceModelPlanError(
            f"{field}[{index}].endpoint_inventory_sha256 is invalid"
        )
    providers = row.get("qualified_provider_count")
    if isinstance(providers, bool) or not isinstance(providers, int) or providers < 1:
        raise GovernanceModelPlanError(
            f"{field}[{index}].qualified_provider_count must be positive"
        )
    evidence = str(row.get("selection_evidence") or "")
    if any(fragment not in evidence for fragment in TOP_POOL_REQUIRED_EVIDENCE):
        raise GovernanceModelPlanError(
            f"{field}[{index}] lacks direct top-{size} endpoint evidence"
        )


def _validate_top50_contract(
    plan: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> None:
    raw_models, _ = _validate_pool_source(
        plan, size=50, prefix="top50", schema=TOP50_POOL_SCHEMA_VERSION
    )
    expected = {
        "optimizer_used": True,
        "optimizer_library": "ortools",
        "optimizer_algorithm": "cp-sat",
        "all_top50_models_received_by_expert_center": True,
        "expert_center_top50_inventory_count": 50,
        "expert_center_reranking_scope": "frozen-top50-pool-only",
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise GovernanceModelPlanError(
                f"top-50 optimizer contract mismatch: {field}"
            )
    optimizer = plan.get("optimizer_audit")
    if not isinstance(optimizer, Mapping) or optimizer.get("optimizer") != "ortools-cp-sat":
        raise GovernanceModelPlanError(
            "top-50 optimizer audit is missing"
        )
    if optimizer.get("solver_status") not in {"OPTIMAL", "FEASIBLE"}:
        raise GovernanceModelPlanError(
            "top-50 optimizer did not produce a feasible assignment"
        )
    inventory = _model_rows(
        plan.get("expert_center_top50_inventory"),
        "expert_center_top50_inventory",
    )
    if len(inventory) != 50:
        raise GovernanceModelPlanError(
            "expert center top-50 inventory is incomplete"
        )
    if plan.get("expert_center_top50_inventory_sha256") != _sha256(
        inventory, "expert_center_top50_inventory"
    ):
        raise GovernanceModelPlanError(
            "expert center top-50 inventory hash mismatch"
        )
    for field, rows in (
        ("selected_models", selected),
        ("recovery_models", recoveries),
    ):
        for index, row in enumerate(rows):
            _validate_pool_execution_row(
                row,
                field=field,
                index=index,
                raw_models=raw_models,
                size=50,
            )


def _validate_top20_contract(
    plan: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> None:
    raw_models, _ = _validate_pool_source(
        plan, size=20, prefix="top20", schema=TOP20_POOL_SCHEMA_VERSION
    )
    if len(selected) != 4 or len(recoveries) != 4:
        raise GovernanceModelPlanError(
            "top-20 execution plan must contain four active and four recovery models"
        )
    for field, rows in (
        ("selected_models", selected),
        ("recovery_models", recoveries),
    ):
        for index, row in enumerate(rows):
            _validate_pool_execution_row(
                row,
                field=field,
                index=index,
                raw_models=raw_models,
                size=20,
            )


def _validate_live_flagship_row(
    row: Mapping[str, Any], *, field: str, index: int
) -> None:
    model, _, _ = _validate_model_row(row, field=field, index=index)
    if ECONOMY_MODEL_RE.search(model):
        raise GovernanceModelPlanError(
            f"{field}[{index}] contains an economy-tier model"
        )
    if SPECIALIZED_MODEL_RE.search(model):
        raise GovernanceModelPlanError(
            f"{field}[{index}] contains a specialized model"
        )
    if row.get("flagship_verified") is not True:
        raise GovernanceModelPlanError(
            f"{field}[{index}] is not a governance-verified flagship"
        )
    basis = str(row.get("flagship_basis") or "").strip()
    if basis not in ALLOWED_FLAGSHIP_BASES:
        raise GovernanceModelPlanError(
            f"{field}[{index}].flagship_basis is invalid"
        )
    if row.get("benchmark_source") != BENCHMARK_SOURCE:
        raise GovernanceModelPlanError(
            f"{field}[{index}].benchmark_source is invalid"
        )
    for metric in (
        "intelligence_index",
        "coding_index",
        "agentic_index",
        "balanced_score",
    ):
        _finite_nonnegative(row.get(metric), f"{field}[{index}].{metric}")
    if not SHA256_RE.fullmatch(str(row.get("benchmark_evidence_sha256") or "")):
        raise GovernanceModelPlanError(
            f"{field}[{index}].benchmark_evidence_sha256 is invalid"
        )
    if not SHA256_RE.fullmatch(str(row.get("endpoint_inventory_sha256") or "")):
        raise GovernanceModelPlanError(
            f"{field}[{index}].endpoint_inventory_sha256 is invalid"
        )
    providers = row.get("qualified_provider_count")
    if isinstance(providers, bool) or not isinstance(providers, int) or providers < 1:
        raise GovernanceModelPlanError(
            f"{field}[{index}].qualified_provider_count must be positive"
        )
    evidence = str(row.get("selection_evidence") or "")
    required_evidence = (
        "non-search",
        "verified-company-flagship-reasoning",
        basis,
        "live-exact-endpoint-qualified",
        "authenticated-zdr-endpoint-qualified",
    )
    if any(fragment not in evidence for fragment in required_evidence):
        raise GovernanceModelPlanError(
            f"{field}[{index}] lacks verified reasoning flagship evidence"
        )


def _validate_live_flagship_contract(
    plan: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    recoveries: Sequence[Mapping[str, Any]],
) -> None:
    if plan.get("catalog_fetch_mode") != LIVE_FETCH_MODE:
        return
    expected = {
        "reasoning_model_required": True,
        "flagship_definition": FLAGSHIP_DEFINITION,
        "benchmark_source": BENCHMARK_SOURCE,
        "company_model_policy": COMPANY_MODEL_POLICY,
        "company_uniqueness_scope": "selected-and-recovery",
        "price_rank_basis": "prompt_usd_per_million + completion_usd_per_million",
        "endpoint_qualification_performed_by_governance": True,
        "model_calls": 0,
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise GovernanceModelPlanError(
                f"live governance flagship contract mismatch: {field}"
            )
    for field, rows in (
        ("selected_models", selected),
        ("recovery_models", recoveries),
    ):
        for index, row in enumerate(rows):
            _validate_live_flagship_row(row, field=field, index=index)


def _validate_budget(
    ticket: Mapping[str, Any], expert_count: int, recovery_count: int
) -> None:
    budget = ticket.get("approved_budget")
    budget = budget if isinstance(budget, Mapping) else {}
    total_calls = budget.get("calls")
    recovery_budget = budget.get("maximum_recovery_calls")
    if isinstance(total_calls, bool) or not isinstance(total_calls, int):
        raise GovernanceModelPlanError("model plan exceeds approved call capacity")
    if expert_count + recovery_count > total_calls:
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
    _validate_model_sets(plan_value, selected, recoveries)
    _validate_price_ranking(plan_value, selected, recoveries)
    if _is_top50(plan_value):
        _validate_top50_contract(plan_value, selected, recoveries)
    elif _is_top20(plan_value):
        _validate_top20_contract(plan_value, selected, recoveries)
    else:
        _validate_live_flagship_contract(plan_value, selected, recoveries)
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
