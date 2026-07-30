"""Provider-diversity repair for R8 preflight without sacrificing availability.

Provider concentration is a soft preference unless the graph explicitly marks it
as required. A soft rebalance must not turn an otherwise executable graph into a
hard budget rejection. Runtime recovery and circuit breaking remain available
when concentration cannot be reduced within the task budget.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import v5_r8_executor as runtime
from execution_graph import ExecutionGraph, GraphLimits

_INSTALLED = False
_ORIGINAL_PREFLIGHT = runtime._preflight


def _risk_cost(nodes: Mapping[str, Any], limits: GraphLimits) -> float:
    return round(
        sum(max(0.0, float(node.estimated_cost)) for node in nodes.values())
        * max(1.0, float(limits.cost_risk_multiplier)),
        8,
    )


def diversity_aware_preflight(
    graph: ExecutionGraph,
    limits: GraphLimits,
) -> tuple[ExecutionGraph, dict[str, Any]]:
    """Run failure/cost preflight first, then budget-safe diversity repair.

    The original preflight is invoked with concentration repair disabled so it
    can still replace over-risk nodes and enforce all non-diversity hard gates.
    This wrapper then considers provider substitutions only when they improve
    concentration without breaking the execution budget, or when they reduce an
    existing budget overrun.
    """
    base_limits = replace(
        limits,
        max_provider_share=1.0,
        max_provider_rebalance_substitutions=0,
    )
    adjusted, report = _ORIGINAL_PREFLIGHT(graph, base_limits)
    blockers = [
        value
        for value in report.get("blockers", [])
        if value not in {
            "provider-concentration-above-production-limit",
            "preflight-risk-adjusted-cost-above-hard-budget",
        }
    ]
    warnings: list[str] = list(report.get("warnings", []))
    recovery = (
        graph.metadata.get("recovery_pool", {})
        if isinstance(graph.metadata, Mapping)
        else {}
    )
    nodes = {node.node_id: node for node in adjusted.nodes}
    substitutions = list(report.get("substitutions", []))
    budget_protected_skips: list[dict[str, Any]] = []

    def counts() -> dict[str, int]:
        result: dict[str, int] = {}
        for node in nodes.values():
            provider = runtime._provider(node)
            result[provider] = result.get(provider, 0) + 1
        return result

    maximum_substitutions = max(0, int(limits.max_provider_rebalance_substitutions))
    if len(nodes) >= 3 and limits.max_provider_share < 1.0:
        for _ in range(maximum_substitutions):
            current = counts()
            overloaded, count = max(current.items(), key=lambda item: item[1])
            current_share = count / len(nodes)
            if current_share <= limits.max_provider_share + 1e-12:
                break
            current_risk_cost = _risk_cost(nodes, limits)
            candidates: list[tuple[float, float, float, str, Any]] = []
            for node_id, node in nodes.items():
                if runtime._provider(node) != overloaded:
                    continue
                rows = recovery.get(node_id, []) if isinstance(recovery, Mapping) else []
                for row in rows:
                    alternative = runtime._candidate(row, node)
                    if (
                        runtime._provider(alternative) == overloaded
                        or alternative.failure_probability
                        > limits.max_node_failure_probability
                    ):
                        continue
                    candidate_nodes = dict(nodes)
                    candidate_nodes[node_id] = alternative
                    candidate_counts: dict[str, int] = {}
                    for candidate_node in candidate_nodes.values():
                        provider = runtime._provider(candidate_node)
                        candidate_counts[provider] = candidate_counts.get(provider, 0) + 1
                    candidate_share = max(candidate_counts.values()) / len(candidate_nodes)
                    if candidate_share >= current_share - 1e-12:
                        continue
                    candidate_risk_cost = _risk_cost(candidate_nodes, limits)
                    budget = limits.max_budget_usd
                    budget_safe = (
                        budget is None
                        or candidate_risk_cost <= float(budget) + 1e-12
                    )
                    reduces_existing_overrun = (
                        budget is not None
                        and current_risk_cost > float(budget) + 1e-12
                        and candidate_risk_cost < current_risk_cost - 1e-12
                    )
                    if not budget_safe and not reduces_existing_overrun:
                        budget_protected_skips.append(
                            {
                                "node_id": node_id,
                                "from": node.provider_endpoint,
                                "to": alternative.provider_endpoint,
                                "candidate_risk_adjusted_cost_usd": candidate_risk_cost,
                                "execution_budget_usd": budget,
                                "reason": "soft-provider-rebalance-would-break-budget",
                            }
                        )
                        continue
                    candidates.append(
                        (
                            candidate_share,
                            candidate_risk_cost,
                            alternative.failure_probability,
                            node_id,
                            alternative,
                        )
                    )
            if not candidates:
                if budget_protected_skips:
                    warnings.append("provider-rebalance-skipped-to-protect-budget")
                break
            candidates.sort(
                key=lambda value: (
                    value[0],
                    value[1],
                    value[2],
                    value[4].estimated_cost,
                    -value[4].estimated_quality,
                )
            )
            candidate_share, candidate_risk_cost, _, node_id, alternative = candidates[0]
            previous = nodes[node_id]
            previous_risk_cost = current_risk_cost
            nodes[node_id] = alternative
            substitutions.append(
                {
                    "node_id": node_id,
                    "reason": "budget-safe-provider-concentration-rebalance",
                    "from": previous.provider_endpoint,
                    "to": alternative.provider_endpoint,
                    "risk_adjusted_graph_cost_before_usd": previous_risk_cost,
                    "risk_adjusted_graph_cost_after_usd": candidate_risk_cost,
                    "max_provider_share_before": current_share,
                    "max_provider_share_after": candidate_share,
                }
            )

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
            warnings.append("provider-concentration-above-target-no-budget-safe-alternative")

    rebuilt = replace(
        adjusted,
        nodes=tuple(nodes[node.node_id] for node in adjusted.nodes),
        estimated_total_cost=round(
            sum(node.estimated_cost for node in nodes.values()), 8
        ),
    )
    risk_cost = rebuilt.estimated_total_cost * max(1.0, limits.cost_risk_multiplier)
    if limits.max_budget_usd is not None and risk_cost > limits.max_budget_usd + 1e-12:
        blockers.append("preflight-risk-adjusted-cost-above-hard-budget")

    report.update(
        {
            "status": "rejected" if blockers else "pass",
            "estimated_initial_cost_usd": rebuilt.estimated_total_cost,
            "risk_adjusted_cost_upper_usd": round(risk_cost, 8),
            "provider_counts": provider_counts,
            "provider_max_share": round(max_share, 6),
            "substitutions": substitutions,
            "budget_protected_skips": budget_protected_skips,
            "warnings": sorted(set(warnings)),
            "blockers": sorted(set(blockers)),
            "policy": (
                "R8 risk-aligned planning plus budget-safe soft provider rebalancing"
            ),
        }
    )
    return rebuilt, report


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    runtime._preflight = diversity_aware_preflight
    _INSTALLED = True
