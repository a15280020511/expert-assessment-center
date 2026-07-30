"""Provider-diversity repair for R8 preflight without sacrificing availability."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import v5_r8_executor as runtime
from execution_graph import ExecutionGraph, GraphLimits

_INSTALLED = False
_ORIGINAL_PREFLIGHT = runtime._preflight


def diversity_aware_preflight(
    graph: ExecutionGraph,
    limits: GraphLimits,
) -> tuple[ExecutionGraph, dict[str, Any]]:
    adjusted, report = _ORIGINAL_PREFLIGHT(graph, limits)
    blockers = [
        value for value in report.get("blockers", [])
        if value != "provider-concentration-above-production-limit"
    ]
    warnings: list[str] = []
    recovery = graph.metadata.get("recovery_pool", {}) if isinstance(graph.metadata, Mapping) else {}
    nodes = {node.node_id: node for node in adjusted.nodes}
    substitutions = list(report.get("substitutions", []))

    def counts() -> dict[str, int]:
        result: dict[str, int] = {}
        for node in nodes.values():
            provider = runtime._provider(node)
            result[provider] = result.get(provider, 0) + 1
        return result

    if len(nodes) >= 3 and limits.max_provider_share < 1.0:
        for _ in range(len(nodes)):
            current = counts()
            overloaded, count = max(current.items(), key=lambda item: item[1])
            if count / len(nodes) <= limits.max_provider_share + 1e-12:
                break
            candidates: list[tuple[float, str, Any]] = []
            for node_id, node in nodes.items():
                if runtime._provider(node) != overloaded:
                    continue
                rows = recovery.get(node_id, []) if isinstance(recovery, Mapping) else []
                for row in rows:
                    alternative = runtime._candidate(row, node)
                    if (
                        runtime._provider(alternative) != overloaded
                        and alternative.failure_probability <= limits.max_node_failure_probability
                    ):
                        penalty = (
                            max(0.0, alternative.estimated_cost - node.estimated_cost)
                            + max(0.0, node.estimated_quality - alternative.estimated_quality)
                        )
                        candidates.append((penalty, node_id, alternative))
            if not candidates:
                break
            candidates.sort(key=lambda value: (
                value[0],
                value[2].failure_probability,
                value[2].estimated_cost,
                -value[2].estimated_quality,
            ))
            _, node_id, alternative = candidates[0]
            previous = nodes[node_id]
            nodes[node_id] = alternative
            substitutions.append({
                "node_id": node_id,
                "reason": "provider-concentration-rebalance",
                "from": previous.provider_endpoint,
                "to": alternative.provider_endpoint,
            })

    provider_counts = counts()
    max_share = max(provider_counts.values(), default=0) / max(1, len(nodes))
    strict = bool(
        isinstance(graph.metadata, Mapping)
        and graph.metadata.get("provider_diversity_required")
    )
    if len(nodes) >= 3 and max_share > limits.max_provider_share + 1e-12:
        if strict:
            blockers.append("provider-concentration-above-production-limit")
        else:
            warnings.append("provider-concentration-above-target-no-safe-alternative")

    rebuilt = replace(
        adjusted,
        nodes=tuple(nodes[node.node_id] for node in adjusted.nodes),
        estimated_total_cost=round(sum(node.estimated_cost for node in nodes.values()), 8),
    )
    risk_cost = rebuilt.estimated_total_cost * max(1.0, limits.cost_risk_multiplier)
    blockers = [
        value for value in blockers
        if value != "preflight-risk-adjusted-cost-above-hard-budget"
    ]
    if limits.max_budget_usd is not None and risk_cost > limits.max_budget_usd + 1e-12:
        blockers.append("preflight-risk-adjusted-cost-above-hard-budget")

    report.update({
        "status": "rejected" if blockers else "pass",
        "estimated_initial_cost_usd": rebuilt.estimated_total_cost,
        "risk_adjusted_cost_upper_usd": round(risk_cost, 8),
        "provider_counts": provider_counts,
        "provider_max_share": round(max_share, 6),
        "substitutions": substitutions,
        "warnings": warnings,
        "blockers": sorted(set(blockers)),
    })
    return rebuilt, report


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    runtime._preflight = diversity_aware_preflight
    _INSTALLED = True
