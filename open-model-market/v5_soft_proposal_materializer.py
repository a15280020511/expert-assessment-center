"""Soft-resource facade for proposal materialization.

The structural materializer remains the native-capacity and graph validator.
This facade prevents local Token and cost advice from becoming request or
rejection gates while preserving structural validation failures unchanged.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import v5_proposal_materializer as structural
from execution_graph import ExecutionGraph, GraphLimits, SelectedNode

_TOKEN_KEYS = frozenset(
    {
        "max_tokens",
        "max_completion_tokens",
        "budget_tokens",
        "token_budget",
    }
)


def _soft_request_config(value: Mapping[str, Any]) -> dict[str, Any]:
    softened = {
        str(key): child
        for key, child in value.items()
        if str(key).casefold() not in _TOKEN_KEYS
    }
    reasoning = softened.get("reasoning")
    if isinstance(reasoning, Mapping):
        soft_reasoning = {
            str(key): child
            for key, child in reasoning.items()
            if str(key).casefold() not in _TOKEN_KEYS
        }
        if soft_reasoning:
            softened["reasoning"] = soft_reasoning
        else:
            softened.pop("reasoning", None)
    return softened


def _soft_node(node: SelectedNode) -> SelectedNode:
    profile = dict(node.parameter_profile)
    profile.update(
        {
            "recommended_output_allowance_is_advisory": True,
            "local_token_ceiling_enforced": False,
        }
    )
    return replace(
        node,
        parameter_profile=profile,
        request_config=_soft_request_config(node.request_config),
    )


def _soft_recovery_pool(
    value: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for node_id, rows in value.items():
        softened_rows: list[dict[str, Any]] = []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            softened = dict(row)
            request = row.get("request_config")
            if isinstance(request, Mapping):
                softened["request_config"] = _soft_request_config(request)
            profile = row.get("parameter_profile")
            if isinstance(profile, Mapping):
                softened["parameter_profile"] = {
                    **dict(profile),
                    "recommended_output_allowance_is_advisory": True,
                    "local_token_ceiling_enforced": False,
                }
            softened_rows.append(softened)
        result[str(node_id)] = softened_rows
    return result


def _soft_graph(graph: ExecutionGraph) -> ExecutionGraph:
    metadata = dict(graph.metadata)
    recovery_pool = metadata.get("recovery_pool")
    if isinstance(recovery_pool, Mapping):
        metadata["recovery_pool"] = _soft_recovery_pool(recovery_pool)
    metadata["resource_governance"] = {
        "mode": "prompt-led-soft-governance",
        "local_token_ceiling_enforced": False,
        "cost_threshold_can_reject_plan": False,
    }
    return replace(
        graph,
        nodes=tuple(_soft_node(node) for node in graph.nodes),
        metadata=metadata,
    )


def _soft_limits(limits: GraphLimits) -> GraphLimits:
    return replace(
        limits,
        max_budget_usd=None,
        max_output_allowance_tokens=None,
    )


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
) -> tuple[ExecutionGraph, GraphLimits, dict[str, Any]]:
    try:
        graph, limits, audit = structural.materialize_proposal(
            proposal,
            task,
            task_envelope,
            catalog,
            approved_total_calls=approved_total_calls,
            governance_calls_reserved=governance_calls_reserved,
            approved_recovery_calls=approved_recovery_calls,
            cost_anomaly_usd=None,
        )
    except structural.ProposalValidationError:
        raise
    softened_graph = _soft_graph(graph)
    softened_limits = _soft_limits(limits)
    telemetry = dict(audit)
    risk_cost = float(
        telemetry.get("risk_adjusted_reserved_cost_usd") or 0.0
    )
    advisory = (
        None if cost_anomaly_usd is None else float(cost_anomaly_usd)
    )
    telemetry.update(
        {
            "resource_governance_mode": "prompt-led-soft-governance",
            "cost_advisory_usd": advisory,
            "cost_advisory_exceeded": bool(
                advisory is not None and risk_cost > advisory + 1e-12
            ),
            "cost_threshold_can_reject_materialization": False,
            "local_token_ceiling_enforced": False,
            "request_token_fields_removed_before_artifact": True,
        }
    )
    return softened_graph, softened_limits, telemetry


def deterministic_violations(
    proposal: Mapping[str, Any],
    task: str,
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    **limits: Any,
) -> list[str]:
    violation: str | None = None
    try:
        materialize_proposal(
            proposal,
            task,
            task_envelope,
            catalog,
            **limits,
        )
    except structural.ProposalValidationError as exc:
        violation = str(exc)
    except (TypeError, ValueError) as exc:
        violation = f"invalid soft materialization input: {exc}"
    return [] if violation is None else [violation]


def claude_unified_review_payload(
    proposal: Mapping[str, Any],
    task: str,
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    task_digest: str,
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    payload = structural.claude_unified_review_payload(
        proposal,
        task,
        task_envelope,
        catalog,
        task_digest=task_digest,
        approved_total_calls=approved_total_calls,
        governance_calls_reserved=governance_calls_reserved,
        approved_recovery_calls=approved_recovery_calls,
        cost_anomaly_usd=None,
    )
    payload["cost_anomaly_usd"] = None
    payload["cost_advisory_usd"] = cost_anomaly_usd
    payload["cost_threshold_can_reject_plan"] = False
    payload["local_token_ceiling_enforced"] = False
    return payload


graph_sha256 = structural.graph_sha256


__all__ = [
    "claude_unified_review_payload",
    "deterministic_violations",
    "graph_sha256",
    "materialize_proposal",
]
