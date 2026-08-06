"""Top-50 soft materializer with audited same-model provider fallback pools.

The legacy soft materializer remains the structural authority. This compatibility
layer only expands each already-selected model's single primary provider into a
deterministic whitelist of all exact endpoint rows that survived the same live
catalog qualification. It does not change model identity, company, role, graph,
or recovery priority.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import v5_soft_proposal_materializer_legacy as _legacy
from execution_graph import ExecutionGraph, SelectedNode

# Preserve the complete public/private compatibility surface for existing tests
# and callers, then override only materialize_proposal below.
for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


def _provider_order(
    catalog: Mapping[str, Any],
    model: str,
    primary: str,
) -> list[str]:
    rows = [
        row
        for row in catalog.get("endpoints", [])
        if isinstance(row, Mapping)
        and str(row.get("model") or "").strip() == model
        and str(row.get("provider") or "").strip()
    ]
    rows.sort(
        key=lambda row: (
            0 if str(row.get("provider")) == primary else 1,
            float(row.get("prompt_price_per_million") or 0.0)
            + float(row.get("completion_price_per_million") or 0.0),
            str(row.get("provider") or ""),
        )
    )
    order: list[str] = []
    for row in rows:
        provider = str(row.get("provider") or "").strip()
        if provider and provider not in order:
            order.append(provider)
    if primary not in order:
        raise _legacy.structural.ProposalValidationError(
            f"primary provider is outside qualified endpoint catalog: {model}@{primary}"
        )
    return order


def _pooled_request(
    request: Mapping[str, Any],
    order: list[str],
) -> dict[str, Any]:
    value = dict(request)
    value["provider"] = {
        "only": list(order),
        "order": list(order),
        "allow_fallbacks": True,
        "require_parameters": True,
    }
    return value


def _pooled_node(
    node: SelectedNode,
    catalog: Mapping[str, Any],
) -> SelectedNode:
    primary = node.provider_endpoint.rsplit("@", 1)[-1].strip()
    order = _provider_order(catalog, node.model, primary)
    return replace(
        node,
        request_config=_pooled_request(node.request_config, order),
    )


def _pooled_recovery_row(
    row: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> dict[str, Any]:
    value = dict(row)
    model = str(value.get("model") or "").strip()
    endpoint = str(value.get("provider_endpoint") or "").strip()
    request = value.get("request_config")
    if not model or not endpoint or not isinstance(request, Mapping):
        return value
    primary = endpoint.rsplit("@", 1)[-1].strip()
    order = _provider_order(catalog, model, primary)
    value["request_config"] = _pooled_request(request, order)
    return value


def _pooled_metadata(
    graph: ExecutionGraph,
    catalog: Mapping[str, Any],
) -> dict[str, Any]:
    metadata = dict(graph.metadata)
    raw_pool = metadata.get("recovery_pool")
    if isinstance(raw_pool, Mapping):
        metadata["recovery_pool"] = {
            str(node_id): [
                _pooled_recovery_row(row, catalog)
                for row in rows
                if isinstance(row, Mapping)
            ]
            for node_id, rows in raw_pool.items()
            if isinstance(rows, (list, tuple))
        }
    metadata["provider_routing_policy"] = {
        "mode": "same-model-audited-qualified-provider-whitelist",
        "provider_only_and_order_identical": True,
        "primary_provider_first": True,
        "unrestricted_fallback_allowed": False,
        "model_substitution_allowed": False,
    }
    return metadata


def materialize_proposal(
    proposal: Mapping[str, Any],
    task: str,
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
):
    graph, limits, audit = _legacy.materialize_proposal(
        proposal,
        task,
        task_envelope,
        catalog,
        approved_total_calls=approved_total_calls,
        governance_calls_reserved=governance_calls_reserved,
        approved_recovery_calls=approved_recovery_calls,
        cost_anomaly_usd=cost_anomaly_usd,
    )
    pooled_graph = replace(
        graph,
        nodes=tuple(_pooled_node(node, catalog) for node in graph.nodes),
        metadata=_pooled_metadata(graph, catalog),
    )
    telemetry = dict(audit)
    telemetry.update(
        {
            "provider_fallback_allowed": True,
            "provider_fallback_scope": (
                "same-model-audited-qualified-provider-whitelist"
            ),
            "unrestricted_provider_fallback_allowed": False,
        }
    )
    return pooled_graph, limits, telemetry
