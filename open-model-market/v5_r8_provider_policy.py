"""Budget-safe reliability and provider-diversity preflight for R8."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import v5_r8_executor as runtime
from execution_graph import ExecutionGraph, GraphLimits
from execution_graph_validator import validate_execution_graph
from v5_budget_runtime_parity import planning_raw_budget_usd

_INSTALLED = False


def diversity_aware_preflight(
    graph: ExecutionGraph,
    limits: GraphLimits,
) -> tuple[ExecutionGraph, dict[str, Any]]:
    """Apply runtime substitutions without invalidating any hard graph invariant.

    Reliability is a hard delivery constraint for required nodes. Provider diversity
    is a soft target unless the graph explicitly marks it required. A substitution
    is eligible only when it preserves the raw cost budget *and* the independence
    contract of the selected graph. The rebuilt graph is fully validated again
    before the first model call.
    """
    recovery = graph.metadata.get("recovery_pool", {}) if isinstance(graph.metadata, Mapping) else {}
    nodes = {node.node_id: node for node in graph.nodes}
    substitutions: list[dict[str, Any]] = []
    rejected_substitutions: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    raw_budget = planning_raw_budget_usd(limits)

    def raw_cost() -> float:
        return sum(node.estimated_cost for node in nodes.values())

    def budget_safe(node_id: str, alternative: Any) -> bool:
        if raw_budget is None:
            return True
        projected = raw_cost() - nodes[node_id].estimated_cost + alternative.estimated_cost
        return projected <= raw_budget + 1e-12

    def independence_safe(node_id: str, alternative: Any) -> bool:
        """Do not collapse independent replicas onto the same model."""
        selected = nodes[node_id]
        group = selected.independence_group
        if not group:
            return True
        return all(
            peer.node_id == node_id
            or peer.independence_group != group
            or peer.model != alternative.model
            for peer in nodes.values()
        )

    # First satisfy the hard per-node reliability policy. Only an alternative that
    # also preserves the runtime budget and independent-model contract may be used.
    for selected in graph.nodes:
        active = nodes[selected.node_id]
        if active.failure_probability <= limits.max_node_failure_probability:
            continue
        rows = recovery.get(selected.node_id, []) if isinstance(recovery, Mapping) else []
        alternatives = [runtime._candidate(row, selected) for row in rows]
        alternatives = [
            item for item in alternatives
            if item.failure_probability < active.failure_probability
            and item.failure_probability <= limits.max_node_failure_probability
        ]
        alternatives.sort(key=lambda item: (
            item.failure_probability,
            item.estimated_cost,
            -item.estimated_quality,
        ))
        safe = next((
            item for item in alternatives
            if budget_safe(selected.node_id, item)
            and independence_safe(selected.node_id, item)
        ), None)
        if safe is not None:
            previous = nodes[selected.node_id]
            nodes[selected.node_id] = safe
            substitutions.append({
                "node_id": selected.node_id,
                "reason": "failure-probability-above-production-threshold",
                "from": previous.provider_endpoint,
                "to": safe.provider_endpoint,
            })
            continue
        for alternative in alternatives:
            if not independence_safe(selected.node_id, alternative):
                reason = "reliability-replacement-would-break-independent-model-diversity"
            elif not budget_safe(selected.node_id, alternative):
                reason = "reliability-replacement-would-exceed-raw-budget"
            else:
                reason = "reliability-replacement-rejected"
            rejected_substitutions.append({
                "node_id": selected.node_id,
                "reason": reason,
                "candidate": alternative.provider_endpoint,
            })
        if selected.node_id in graph.final_nodes or "synthesis" not in selected.functions:
            blockers.append(f"required-node-risk-above-threshold:{selected.node_id}")
        else:
            warnings.append(f"optional-node-risk-above-threshold:{selected.node_id}")

    def counts() -> dict[str, int]:
        result: dict[str, int] = {}
        for node in nodes.values():
            provider = runtime._provider(node)
            result[provider] = result.get(provider, 0) + 1
        return result

    # Rebalance only when a different-provider candidate remains inside the raw
    # budget and does not collapse an independence group onto one model.
    if len(nodes) >= 3 and limits.max_provider_share < 1.0:
        for _ in range(len(nodes)):
            current = counts()
            overloaded, count = max(current.items(), key=lambda item: item[1])
            if count / len(nodes) <= limits.max_provider_share + 1e-12:
                break
            candidates: list[tuple[float, str, Any]] = []
            unsafe: list[tuple[str, Any, str]] = []
            for node_id, node in nodes.items():
                if runtime._provider(node) != overloaded:
                    continue
                rows = recovery.get(node_id, []) if isinstance(recovery, Mapping) else []
                for row in rows:
                    alternative = runtime._candidate(row, node)
                    if (
                        runtime._provider(alternative) == overloaded
                        or alternative.failure_probability > limits.max_node_failure_probability
                    ):
                        continue
                    if not independence_safe(node_id, alternative):
                        unsafe.append((
                            node_id,
                            alternative,
                            "provider-rebalance-would-break-independent-model-diversity",
                        ))
                        continue
                    if not budget_safe(node_id, alternative):
                        unsafe.append((
                            node_id,
                            alternative,
                            "provider-rebalance-would-exceed-raw-budget",
                        ))
                        continue
                    penalty = (
                        max(0.0, alternative.estimated_cost - node.estimated_cost)
                        + max(0.0, node.estimated_quality - alternative.estimated_quality)
                    )
                    candidates.append((penalty, node_id, alternative))
            if not candidates:
                rejected_substitutions.extend({
                    "node_id": node_id,
                    "reason": reason,
                    "candidate": alternative.provider_endpoint,
                } for node_id, alternative, reason in unsafe)
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
            warnings.append("provider-concentration-above-target-no-budget-safe-alternative")

    rebuilt = replace(
        graph,
        nodes=tuple(nodes[node.node_id] for node in graph.nodes),
        estimated_total_cost=round(raw_cost(), 8),
    )
    risk_cost = rebuilt.estimated_total_cost * max(1.0, limits.cost_risk_multiplier)
    if limits.max_budget_usd is not None and risk_cost > limits.max_budget_usd + 1e-12:
        blockers.append("preflight-risk-adjusted-cost-above-hard-budget")

    # Runtime substitution is a graph mutation. Re-run the complete deterministic
    # validator rather than assuming that cost/provider checks imply structural safety.
    structural_issues = [
        issue for issue in validate_execution_graph(rebuilt, limits)
        if issue.code != "budget_limit"
    ]
    blockers.extend(
        f"post-substitution-structural:{issue.code}"
        for issue in structural_issues
    )

    blockers = sorted(set(blockers))
    report = {
        "status": "rejected" if blockers else "pass",
        "estimated_initial_cost_usd": rebuilt.estimated_total_cost,
        "planning_raw_budget_usd": raw_budget,
        "risk_adjusted_cost_upper_usd": round(risk_cost, 8),
        "max_budget_usd": limits.max_budget_usd,
        "cost_risk_multiplier": max(1.0, limits.cost_risk_multiplier),
        "provider_counts": provider_counts,
        "provider_max_share": round(max_share, 6),
        "provider_diversity_required": strict,
        "substitutions": substitutions,
        "rejected_substitutions": rejected_substitutions,
        "post_substitution_validation": {
            "status": "PASS" if not structural_issues else "FAIL",
            "issues": [
                {"code": issue.code, "message": issue.message, "path": issue.path}
                for issue in structural_issues
            ],
        },
        "warnings": sorted(set(warnings)),
        "blockers": blockers,
        "policy": "R8 budget-safe reliability/provider preflight with post-substitution structural validation",
    }
    return rebuilt, report


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    runtime._preflight = diversity_aware_preflight
    _INSTALLED = True
