"""Production-ticket evidence facade for signed task-adaptive Top-50 OR-Tools plans."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_price_ranked_production_ticket_legacy as _legacy
from v5_paid_acceptance_free_first_guard import (
    PaidAcceptanceFreeFirstError,
    enforce_free_first,
)

PAID_ACCEPTANCE_APP_NAME = "top50-ortools-paid-candidate-acceptance"

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


def _fields(plan: Mapping[str, Any]) -> dict[str, Any]:
    top50 = plan.get("selected_from_top50_reasoning_pool_only") is True
    audit = plan.get("optimizer_audit")
    audit = dict(audit) if isinstance(audit, Mapping) else {}
    constraints = audit.get("constraints")
    constraints = dict(constraints) if isinstance(constraints, Mapping) else {}
    profile = plan.get("task_demand_profile")
    profile = dict(profile) if isinstance(profile, Mapping) else {}
    principles = plan.get("selection_principles")
    principles = list(principles) if isinstance(principles, list) else []
    return {
        "candidate_pool_authority": "decision-system-governance",
        "selection_authority": "expert-assessment-center-ortools" if top50 else "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center-ortools" if top50 else "decision-system-governance",
        "model_selection_performed_locally": top50,
        "candidate_pool_reranking_performed_locally": False,
        "model_reranking_performed_locally": False,
        "model_substitution_allowed": False,
        "optimizer_used": top50,
        "optimizer": plan.get("optimizer") if top50 else None,
        "optimizer_optimality_proven": bool(audit.get("optimality_proven")) if top50 else False,
        "task_adaptive_scoring_completed": bool(plan.get("task_adaptive_scoring_completed")) if top50 else False,
        "task_adaptive_scoring_schema_version": plan.get("task_adaptive_scoring_schema_version") if top50 else None,
        "selection_principles": principles if top50 else [],
        "task_demand_profile": profile if top50 else {},
        "dynamic_role_weights_used": constraints.get("dynamic_role_weights_used") is True if top50 else False,
        "marginal_return_used": constraints.get("marginal_return_used") is True if top50 else False,
        "task_role_native_capacity_compatibility": constraints.get("task_role_native_capacity_compatibility") is True if top50 else False,
        "semantic_keyword_routing_used": constraints.get("semantic_keyword_routing_used") is True if top50 else False,
        "cross_task_history_used": constraints.get("cross_task_history_used") is True if top50 else False,
        "provider_resolution_authority": "openrouter-unrestricted",
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "provider_fallback_allowed": True,
        "unrestricted_provider_fallback_allowed": True,
        "provider_only_allowed": False,
        "provider_order_allowed": False,
        "provider_zdr_filter_allowed": False,
        "provider_data_collection_filter_allowed": False,
        "provider_price_filter_allowed": False,
        "openrouter_selects_provider": True,
        "orchestration_library": "networkx",
        "optimizer_library": "ortools-cp-sat" if top50 else None,
    }


def _enforce_paid_acceptance_free_first(root: Path) -> None:
    if str(os.environ.get("OPENROUTER_APP_NAME") or "") != PAID_ACCEPTANCE_APP_NAME:
        return
    expected_sha = str(os.environ.get("AUTHORITATIVE_EXECUTION_SHA") or "").strip()
    try:
        verdict = enforce_free_first(
            output_dir=root,
            expected_sha=expected_sha,
        )
    except PaidAcceptanceFreeFirstError as exc:
        root.mkdir(parents=True, exist_ok=True)
        (root / "free-first-preflight-error.json").write_text(
            json.dumps(
                {
                    "schema_version": "v5-paid-acceptance-free-first-error-1",
                    "status": "FAIL",
                    "target_sha": expected_sha,
                    "model_calls": 0,
                    "paid_model_calls": 0,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise
    if verdict.get("status") != "PASS" or verdict.get("paid_acceptance_allowed") is not True:
        raise PaidAcceptanceFreeFirstError("paid acceptance is not authorized by free-first evidence")


def main(argv: Sequence[str] | None = None) -> int:
    args = _legacy.build_parser().parse_args(argv)
    root = Path(args.output_dir)
    _enforce_paid_acceptance_free_first(root)
    ticket_path = root / "ticket.json"
    plan: Mapping[str, Any] = {}
    if ticket_path.is_file():
        raw = json.loads(ticket_path.read_text(encoding="utf-8"))
        if isinstance(raw, Mapping) and isinstance(raw.get("governance_model_plan"), Mapping):
            plan = raw["governance_model_plan"]
    fields = _fields(plan)
    original_write = _legacy.write_json

    def write_json(path: Path, value: Any) -> None:
        if isinstance(value, Mapping) and path.name in {
            "planning-task.json",
            "production-runtime.json",
            "expert-team-error.json",
        }:
            document = dict(value)
            document.update(fields)
            if path.name == "production-runtime.json" and fields["optimizer_used"]:
                document["architecture"] = (
                    "governance-signed-weekly-top50 -> current-ticket structural demand "
                    "profile -> task-adaptive cost/intelligence/capacity/marginal-return "
                    "scoring -> expert-center OR-Tools CP-SAT 4-primary+4-warm-recovery "
                    "assignment -> unrestricted OpenRouter provider routing for each fixed "
                    "model -> NetworkX DAG -> parallel analysis -> cross-review -> final "
                    "synthesis"
                )
            original_write(path, document)
            return
        original_write(path, value)

    _legacy.write_json = write_json
    try:
        return _legacy.main(argv)
    finally:
        _legacy.write_json = original_write


if __name__ == "__main__":
    raise SystemExit(main())
