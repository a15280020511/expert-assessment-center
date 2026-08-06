"""Production pipeline facade for signed top-50 + expert OR-Tools assignment.

Governance signs model candidates, the expert center assigns models with CP-SAT,
and OpenRouter selects the actual provider without any provider routing filter.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_price_ranked_pipeline_legacy as _legacy
from v5_json_io import load_json_or_default, write_json

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


def _top50(plan: Mapping[str, Any]) -> bool:
    return plan.get("selected_from_top50_reasoning_pool_only") is True


def _assignment_fields(plan: Mapping[str, Any]) -> dict[str, Any]:
    active = _top50(plan)
    return {
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center-ortools" if active else "decision-system-governance",
        "selection_authority": "expert-assessment-center-ortools" if active else "decision-system-governance",
        "expert_center_model_selection_allowed": active,
        "expert_center_pool_assignment_performed": active,
        "model_selection_performed_locally": active,
        "candidate_pool_reranking_performed_locally": False,
        "model_reranking_performed_locally": False,
        "model_substitution_allowed": False,
        "optimizer_present": active,
        "optimizer_used": active,
        "optimizer": plan.get("optimizer") if active else None,
        "optimizer_optimality_proven": bool(plan.get("optimizer_audit", {}).get("optimality_proven")) if active else False,
    }


def _provider_fields() -> dict[str, Any]:
    return {
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_resolution_only": False,
        "provider_resolution_performed_locally": False,
        "provider_restrictions_applied": False,
        "provider_fallback_allowed": True,
        "unrestricted_provider_fallback_allowed": True,
        "provider_only_allowed": False,
        "provider_order_allowed": False,
        "provider_zdr_filter_allowed": False,
        "provider_data_collection_filter_allowed": False,
        "provider_price_filter_allowed": False,
        "openrouter_selects_provider": True,
        "model_substitution_allowed": False,
    }


_original_task_state = _legacy._task_state
_original_catalog_state = _legacy._catalog_state
_original_catalog_snapshot = _legacy._catalog_snapshot
_original_runtime_config = _legacy._runtime_config
_original_zero_governance = _legacy._zero_local_governance_artifacts
_original_request_audit = _legacy._request_audit
_original_finalize_result = _legacy._finalize_result


def _task_state(args: Any, run: Any, output: Path, plan: Mapping[str, Any]):
    task, digest, envelope = _original_task_state(args, run, output, plan)
    value = dict(envelope)
    value.update(_assignment_fields(plan))
    value.update(_provider_fields())
    write_json(output / "v5-task-envelope.json", value)
    return task, digest, value


def _catalog_state(args: Any, run: Any, task_envelope: Mapping[str, Any], plan: Mapping[str, Any]):
    catalog, catalog_source, endpoint_source = _original_catalog_state(args, run, task_envelope, plan)
    value = dict(catalog)
    value.update(_assignment_fields(plan))
    value.update(_provider_fields())
    value["catalog_scope"] = "governance-signed-top50-assigned-models-only"
    value["endpoint_catalog_role"] = "availability-and-telemetry-only-not-routing-restriction"
    return value, catalog_source, endpoint_source


def _catalog_snapshot(catalog: Mapping[str, Any], catalog_source: str, endpoint_source: str, plan: Mapping[str, Any]):
    value = dict(_original_catalog_snapshot(catalog, catalog_source, endpoint_source, plan))
    value.update(_assignment_fields(plan))
    value.update(_provider_fields())
    return value


def _runtime_config(args: Any, *, total_calls: int, recovery_calls: int, plan: Mapping[str, Any]):
    value = dict(_original_runtime_config(args, total_calls=total_calls, recovery_calls=recovery_calls, plan=plan))
    value.update(_assignment_fields(plan))
    value.update(_provider_fields())
    value.update(
        {
            "provider_resolution_authority": "openrouter-unrestricted",
            "provider_lock_required": False,
            "orchestration_library": "networkx",
            "optimizer_library": "ortools-cp-sat" if _top50(plan) else None,
        }
    )
    return value


def _zero_local_governance_artifacts(output: Path, plan: Mapping[str, Any], materialization_audit: Mapping[str, Any]):
    ledger = dict(_original_zero_governance(output, plan, materialization_audit))
    fields = _assignment_fields(plan)
    ledger.update(fields)
    ledger.update(_provider_fields())
    ledger["selection_performed_in_expert_center"] = _top50(plan)
    write_json(output / "v5-governance-calls.json", ledger)

    result_raw = load_json_or_default(output / "v5-governance-result.json", {})
    result = dict(result_raw) if isinstance(result_raw, Mapping) else {}
    result.update(fields)
    result.update(_provider_fields())
    result["selection_performed_in_expert_center"] = _top50(plan)
    result["provider_materialization"] = dict(materialization_audit)
    write_json(output / "v5-governance-result.json", result)
    return ledger


def _execute(
    *,
    args: Any,
    run: Any,
    output: Path,
    task: str,
    graph: Any,
    limits: Any,
    total_calls: int,
    recovery_calls: int,
    expert_call_fn: Any | None,
) -> dict[str, Any]:
    config = _legacy.RuntimeConfig(
        total_call_limit=total_calls,
        recovery_call_limit=recovery_calls,
        cost_anomaly_usd=args.cost_anomaly_usd,
        tools_allowed=False,
        live_catalog_required=bool(args.require_live_catalog),
        provider_lock_required=False,
    )
    runtime = _legacy.build_production_runtime(config)
    return runtime.execute_graph(
        graph,
        run,
        task,
        call_fn=expert_call_fn,
        output_dir=output,
        limits=limits,
    )


def _request_audit(output: Path, *, approved_total_calls: int) -> None:
    _original_request_audit(output, approved_total_calls=approved_total_calls)
    path = output / "v5-request-audit.json"
    raw = load_json_or_default(path, {})
    document = dict(raw) if isinstance(raw, Mapping) else {}
    rows = document.get("requests") if isinstance(document.get("requests"), list) else []
    restricted = [row for row in rows if isinstance(row, Mapping) and "provider" in row]
    document.update(_provider_fields())
    document["provider_objects_present"] = len(restricted)
    document["provider_routing_open"] = not restricted
    document["status"] = "PASS" if document.get("status") == "PASS" and not restricted else "FAIL"
    write_json(path, document)
    if restricted:
        raise RuntimeError("provider routing restriction detected in production request audit")


def _finalize_result(result: dict[str, Any], *, total_calls: int, plan: Mapping[str, Any], selection_audit: Mapping[str, Any]) -> None:
    _original_finalize_result(result, total_calls=total_calls, plan=plan, selection_audit=selection_audit)
    result.update(_assignment_fields(plan))
    result.update(_provider_fields())
    result["selection_audit"] = dict(selection_audit)


def _rewrite_static_artifacts(output: Path) -> None:
    plan_raw = load_json_or_default(output / "governance-model-plan.json", {})
    plan = dict(plan_raw) if isinstance(plan_raw, Mapping) else {}
    if not plan:
        return
    fields = _assignment_fields(plan)
    provider = _provider_fields()

    selection_path = output / "v5-selection.json"
    selection_raw = load_json_or_default(selection_path, {})
    if isinstance(selection_raw, Mapping) and selection_raw:
        selection = dict(selection_raw)
        selection.update(fields)
        selection.update(provider)
        write_json(selection_path, selection)

    graph_path = output / "v5-execution-graph.json"
    graph_raw = load_json_or_default(graph_path, {})
    if isinstance(graph_raw, Mapping) and graph_raw:
        graph = dict(graph_raw)
        metadata_raw = graph.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        metadata.update(fields)
        metadata.update(provider)
        graph["metadata"] = metadata
        write_json(graph_path, graph)


def main(argv: Sequence[str] | None = None, *, expert_call_fn: Any | None = None) -> int:
    args = _legacy.build_parser().parse_args(argv)
    result = _legacy.main(argv, expert_call_fn=expert_call_fn)
    _rewrite_static_artifacts(Path(args.output_dir))
    return result


_legacy._task_state = _task_state
_legacy._catalog_state = _catalog_state
_legacy._catalog_snapshot = _catalog_snapshot
_legacy._runtime_config = _runtime_config
_legacy._zero_local_governance_artifacts = _zero_local_governance_artifacts
_legacy._execute = _execute
_legacy._request_audit = _request_audit
_legacy._finalize_result = _finalize_result


if __name__ == "__main__":
    raise SystemExit(main())
