"""Top-50 soft materializer with unrestricted OpenRouter provider routing.

The legacy materializer still builds the selected-model graph. This layer strips
all provider routing preferences from expert and recovery request payloads so
OpenRouter can choose any currently available provider for the fixed model.
Model identity, company, role, graph topology and recovery model identity remain
unchanged.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import v5_soft_proposal_materializer_legacy as _legacy
from execution_graph import ExecutionGraph, SelectedNode

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


def _open_request(request: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(request)
    value.pop("provider", None)
    return value


def _open_node(node: SelectedNode) -> SelectedNode:
    return replace(node, request_config=_open_request(node.request_config))


def _open_recovery_row(row: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(row)
    request = value.get("request_config")
    if isinstance(request, Mapping):
        value["request_config"] = _open_request(request)
    return value


def _open_metadata(graph: ExecutionGraph) -> dict[str, Any]:
    metadata = dict(graph.metadata)
    raw_pool = metadata.get("recovery_pool")
    if isinstance(raw_pool, Mapping):
        metadata["recovery_pool"] = {
            str(node_id): [
                _open_recovery_row(row)
                for row in rows
                if isinstance(row, Mapping)
            ]
            for node_id, rows in raw_pool.items()
            if isinstance(rows, (list, tuple))
        }
    metadata["provider_routing_policy"] = {
        "mode": "unrestricted-openrouter",
        "provider_only_present": False,
        "provider_order_present": False,
        "zdr_filter_present": False,
        "data_collection_filter_present": False,
        "provider_price_filter_present": False,
        "openrouter_selects_provider": True,
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
    open_graph = replace(
        graph,
        nodes=tuple(_open_node(node) for node in graph.nodes),
        metadata=_open_metadata(graph),
    )
    telemetry = dict(audit)
    telemetry.update(
        {
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "provider_fallback_allowed": True,
            "unrestricted_provider_fallback_allowed": True,
            "model_substitution_allowed": False,
        }
    )
    return open_graph, limits, telemetry


__all__ = [
    *[name for name in dir(_legacy) if not name.startswith("__")],
    "materialize_proposal",
]
