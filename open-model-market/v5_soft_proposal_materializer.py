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


def _recovery_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("model") or "").strip(),
        str(row.get("provider_endpoint") or "").strip(),
    )


def _recovery_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(row for row in value if isinstance(row, Mapping))


def _unique_recovery_candidates(
    value: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for rows in value.values():
        for row in _recovery_rows(rows):
            identity = _recovery_identity(row)
            if not all(identity) or identity in seen:
                continue
            seen.add(identity)
            candidates.append(dict(row))
    return candidates


def _soft_recovery_placeholder(row: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve non-executable compatibility metadata only at its source node."""
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
    return softened


def _local_recovery_placeholders(
    value: Mapping[str, Any],
    node_id: str,
) -> list[dict[str, Any]]:
    return [
        _soft_recovery_placeholder(row)
        for row in _recovery_rows(value.get(node_id))
        if not all(_recovery_identity(row))
    ]


def _adapt_recovery_candidate(
    row: Mapping[str, Any],
    node: SelectedNode,
) -> dict[str, Any]:
    """Bind one approved recovery model to a node without changing its role."""
    adapted = dict(row)
    model, endpoint = _recovery_identity(row)
    adapted.update(
        {
            "candidate_id": f"recovery:{node.node_id}:{endpoint}",
            "assigned_work": list(node.assigned_work),
            "professional_capabilities": dict(node.professional_capabilities),
            "functions": list(node.functions),
            "prompt_profile": dict(node.prompt_profile),
            "reasoning_profile": dict(node.reasoning_profile),
            "model": model,
            "provider_endpoint": endpoint,
            "output_contract": dict(node.output_contract),
        }
    )

    recovery_profile = row.get("parameter_profile")
    profile = dict(recovery_profile) if isinstance(recovery_profile, Mapping) else {}
    for key, child in node.parameter_profile.items():
        if str(key) != "supported_parameters":
            profile[str(key)] = child
    profile.update(
        {
            "recommended_output_allowance_is_advisory": True,
            "local_token_ceiling_enforced": False,
            "shared_recovery_pool": True,
        }
    )
    adapted["parameter_profile"] = profile

    recovery_request = row.get("request_config")
    request = dict(recovery_request) if isinstance(recovery_request, Mapping) else {}
    selected_request = node.request_config
    selected_reasoning = (
        selected_request.get("reasoning")
        if isinstance(selected_request, Mapping)
        else None
    )
    if isinstance(selected_reasoning, Mapping):
        request["reasoning"] = dict(selected_reasoning)
    else:
        request.pop("reasoning", None)
    adapted["request_config"] = _soft_request_config(request)
    return adapted


def _shared_recovery_pool(
    graph: ExecutionGraph,
    value: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Expose approved recovery candidates to every node as one global pool.

    The runtime budget controller remains the sole authority for how many
    recovery calls may actually be reserved. Repeating candidate metadata per
    node changes availability only; it does not increase the admitted call
    ceiling or create local model selection. Incomplete compatibility rows are
    softened but remain local and cannot become executable shared candidates.
    """
    candidates = _unique_recovery_candidates(value)
    return {
        node.node_id: [
            *(
                _adapt_recovery_candidate(candidate, node)
                for candidate in candidates
            ),
            *_local_recovery_placeholders(value, node.node_id),
        ]
        for node in graph.nodes
    }


def _soft_graph(graph: ExecutionGraph) -> ExecutionGraph:
    metadata = dict(graph.metadata)
    recovery_pool = metadata.get("recovery_pool")
    if isinstance(recovery_pool, Mapping):
        shared = _shared_recovery_pool(graph, recovery_pool)
        metadata["recovery_pool"] = shared
        metadata["recovery_pool_policy"] = {
            "mode": "shared-governance-approved-candidates",
            "candidate_count": len(_unique_recovery_candidates(recovery_pool)),
            "call_ceiling_authority": "runtime-global-recovery-budget",
            "local_model_selection_performed": False,
            "incomplete_placeholders_shared": False,
        }
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
            "recovery_pool_mode": "shared-governance-approved-candidates",
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
