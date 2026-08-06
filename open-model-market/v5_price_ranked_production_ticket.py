"""Production-ticket evidence facade for signed top-50 OR-Tools plans."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_price_ranked_production_ticket_legacy as _legacy

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


def _fields(plan: Mapping[str, Any]) -> dict[str, Any]:
    top50 = plan.get("selected_from_top50_reasoning_pool_only") is True
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
        "optimizer_optimality_proven": bool(plan.get("optimizer_audit", {}).get("optimality_proven")) if top50 else False,
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _legacy.build_parser().parse_args(argv)
    root = Path(args.output_dir)
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
                    "governance-signed-weekly-top50 -> expert-center OR-Tools CP-SAT "
                    "4-primary+4-warm-recovery assignment -> unrestricted OpenRouter "
                    "provider routing for each fixed model -> NetworkX DAG -> parallel "
                    "analysis -> cross-review -> final synthesis"
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
