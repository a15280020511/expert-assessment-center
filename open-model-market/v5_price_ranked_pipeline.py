"""Production pipeline facade for signed governance plans + dynamic Expert assignment.

Governance supplies model candidates, the Expert Center assigns exact model identities,
and OpenRouter selects the actual Provider without any Provider routing filter. Catalog
validation therefore checks model-level existence/capacity only; Provider endpoint
inventory is compatibility metadata and never an eligibility gate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_catalog_view as catalog_view
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


def _planned_ids(plan: Mapping[str, Any]) -> list[str]:
    ids: list[str] = []
    for field in ("selected_models", "recovery_models"):
        rows = plan.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, Mapping):
                model = str(row.get("model") or "").strip()
                if model and model not in ids:
                    ids.append(model)
    return ids


def _candidate_map(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for field in (
        "expert_candidate_pool",
        "top50_expert_selectable_candidates",
        "selected_models",
        "recovery_models",
    ):
        rows = plan.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            model = str(row.get("model") or "").strip()
            if model and model not in result:
                result[model] = row
    return result


def _model_catalog_row(model: Any) -> dict[str, Any]:
    prompt = getattr(model, "prompt_price_per_million", None)
    completion = getattr(model, "completion_price_per_million", None)
    return {
        "id": str(getattr(model, "id", "") or ""),
        "context_length": int(getattr(model, "context_length", 0) or 0),
        "supported_parameters": list(getattr(model, "supported_parameters", []) or []),
        "pricing": {
            "prompt": (float(prompt) / 1_000_000 if prompt is not None else None),
            "completion": (
                float(completion) / 1_000_000 if completion is not None else None
            ),
        },
        "top_provider": {
            "max_completion_tokens": int(
                getattr(model, "max_completion_tokens", 0) or 0
            )
        },
        "architecture": {
            "input_modalities": list(getattr(model, "input_modalities", []) or []),
            "output_modalities": list(getattr(model, "output_modalities", []) or []),
        },
        "ranks": dict(getattr(model, "ranks", {}) or {}),
    }


def _open_endpoint_payloads(
    model_ids: Sequence[str],
    plan: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    """Build non-binding endpoint-shaped metadata from model-level facts only."""
    candidates = _candidate_map(plan)
    model_rows = catalog.get("data") if isinstance(catalog.get("data"), list) else []
    model_map = {
        str(row.get("id") or "").strip(): row
        for row in model_rows
        if isinstance(row, Mapping) and str(row.get("id") or "").strip()
    }
    payloads: dict[str, Mapping[str, Any]] = {}
    for model_id in model_ids:
        candidate = candidates.get(model_id, {})
        model_row = model_map.get(model_id, {})
        top_provider = (
            model_row.get("top_provider")
            if isinstance(model_row.get("top_provider"), Mapping)
            else {}
        )
        context_length = int(
            candidate.get("context_length")
            or model_row.get("context_length")
            or 0
        )
        max_completion_tokens = int(
            candidate.get("max_completion_tokens")
            or top_provider.get("max_completion_tokens")
            or 0
        )
        supported = model_row.get("supported_parameters")
        if not isinstance(supported, list):
            supported = ["reasoning", "max_tokens"]
        pricing = model_row.get("pricing")
        if not isinstance(pricing, Mapping):
            pricing = {
                "prompt": float(candidate.get("prompt_usd_per_million") or 0.0)
                / 1_000_000,
                "completion": float(
                    candidate.get("completion_usd_per_million") or 0.0
                )
                / 1_000_000,
            }
        payloads[model_id] = {
            "data": {
                "endpoints": [
                    {
                        "tag": "openrouter-unrestricted",
                        "provider_name": "OpenRouter unrestricted routing",
                        "context_length": context_length,
                        "max_completion_tokens": max_completion_tokens,
                        "supported_parameters": list(supported),
                        "pricing": dict(pricing),
                        "uptime": 1.0,
                        "is_free": False,
                        "is_quantized": False,
                        "routing_constraint": False,
                    }
                ]
            }
        }
    return payloads


def _nonbinding_catalog(
    model_ids: Sequence[str],
    planned_catalog: Mapping[str, Any],
    endpoint_payloads: Mapping[str, Mapping[str, Any]],
    task_envelope: Mapping[str, Any],
) -> dict[str, Any]:
    rows = planned_catalog.get("data") if isinstance(planned_catalog.get("data"), list) else []
    model_map = {
        str(row.get("id") or "").strip(): row
        for row in rows
        if isinstance(row, Mapping) and str(row.get("id") or "").strip()
    }
    metadata: list[dict[str, Any]] = []
    for model_id in model_ids:
        row = model_map[model_id]
        top_provider = row.get("top_provider") if isinstance(row.get("top_provider"), Mapping) else {}
        payload = endpoint_payloads.get(model_id, {})
        endpoint_rows = catalog_view._endpoint_rows(payload)  # noqa: SLF001
        endpoint_contexts = [
            int(value.get("context_length") or 0)
            for value in endpoint_rows
            if isinstance(value, Mapping)
        ]
        endpoint_completions = [
            int(value.get("max_completion_tokens") or 0)
            for value in endpoint_rows
            if isinstance(value, Mapping)
        ]
        metadata.append(
            {
                "model": model_id,
                "provider": "openrouter-auto",
                "provider_endpoint": f"{model_id}@openrouter-auto",
                "routing_constraint": False,
                "context_length": max(
                    [int(row.get("context_length") or 0), *endpoint_contexts]
                ),
                "max_completion_tokens": max(
                    [int(top_provider.get("max_completion_tokens") or 0), *endpoint_completions]
                ),
                "supported_parameters": list(row.get("supported_parameters") or []),
            }
        )
    return {
        "schema_version": "v5-unrestricted-planned-model-catalog-1",
        "catalog_scope": "governance-planned-models-only",
        "planned_model_ids": list(model_ids),
        "required_context_tokens": int(task_envelope.get("required_context_tokens") or 0),
        "model_level_existence_validated": True,
        "provider_endpoint_inventory_required": False,
        "provider_endpoint_inventory_used_as_gate": False,
        "endpoints": metadata,
    }


_original_task_state = _legacy._task_state
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


def _catalog_state(
    args: Any,
    run: Any,
    task_envelope: Mapping[str, Any],
    plan: Mapping[str, Any],
):
    model_ids = _planned_ids(plan)
    if not model_ids:
        raise RuntimeError("governance plan has no assigned models")

    models, catalog_source = model_market.fetch_catalog(run)
    missing = [model_id for model_id in model_ids if model_id not in models]
    if missing:
        raise catalog_view.CatalogViewError(
            "governance-planned models are absent from current model catalog: "
            + ", ".join(missing)
        )
    planned_catalog = {
        "data": [_model_catalog_row(models[model_id]) for model_id in model_ids]
    }

    if args.endpoint_file:
        endpoint_path = Path(args.endpoint_file)
        if not endpoint_path.is_file():
            raise RuntimeError(f"endpoint file does not exist: {endpoint_path}")
        endpoint_payloads = _load_mapping(endpoint_path)
        endpoint_source = str(endpoint_path)
    else:
        endpoint_payloads = _open_endpoint_payloads(
            model_ids,
            plan,
            planned_catalog,
        )
        endpoint_source = "model-metadata-derived-openrouter-unrestricted"

    catalog = _nonbinding_catalog(
        model_ids,
        planned_catalog,
        endpoint_payloads,
        task_envelope,
    )
    value = dict(catalog)
    value.update(_assignment_fields(plan))
    value.update(_provider_fields())
    value["catalog_scope"] = "governance-assigned-models-only"
    value["endpoint_catalog_role"] = "non-binding-availability-and-capacity-metadata-only"
    value["live_provider_endpoint_inventory_required"] = False
    return value, catalog_source, endpoint_source


def _catalog_snapshot(
    catalog: Mapping[str, Any],
    catalog_source: str,
    endpoint_source: str,
    plan: Mapping[str, Any],
):
    value = dict(
        _original_catalog_snapshot(catalog, catalog_source, endpoint_source, plan)
    )
    value.update(_assignment_fields(plan))
    value.update(_provider_fields())
    value["live_provider_endpoint_inventory_required"] = False
    return value


def _runtime_config(
    args: Any,
    *,
    total_calls: int,
    recovery_calls: int,
    plan: Mapping[str, Any],
):
    value = dict(
        _original_runtime_config(
            args,
            total_calls=total_calls,
            recovery_calls=recovery_calls,
            plan=plan,
        )
    )
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


def _zero_local_governance_artifacts(
    output: Path,
    plan: Mapping[str, Any],
    materialization_audit: Mapping[str, Any],
):
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
    restricted = [
        row
        for row in rows
        if isinstance(row, Mapping) and "provider" in row
    ]
    document.update(_provider_fields())
    document["provider_objects_present"] = len(restricted)
    document["provider_routing_open"] = not restricted
    document["status"] = (
        "PASS"
        if document.get("status") == "PASS" and not restricted
        else "FAIL"
    )
    write_json(path, document)
    if restricted:
        raise RuntimeError(
            "provider routing restriction detected in production request audit"
        )


def _finalize_result(
    result: dict[str, Any],
    *,
    total_calls: int,
    plan: Mapping[str, Any],
    selection_audit: Mapping[str, Any],
) -> None:
    _original_finalize_result(
        result,
        total_calls=total_calls,
        plan=plan,
        selection_audit=selection_audit,
    )
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


def main(
    argv: Sequence[str] | None = None,
    *,
    expert_call_fn: Any | None = None,
) -> int:
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
