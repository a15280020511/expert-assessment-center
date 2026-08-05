"""Deterministic low-price expert-team selection and DAG orchestration.

The selector reads the validated exact endpoint catalog, ranks eligible
endpoints by estimated task cost, keeps model companies globally distinct, and
builds a bounded expert graph. It performs no model call and uses no cross-task
history.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

import networkx as nx

from v5_catalog_view import (
    GOVERNANCE_COMPANIES,
    MAX_VISIBLE_MODELS,
    MINIMUM_EXPERT_COMPLETION_TOKENS,
    stable_model_id,
)
from v5_model_company import canonical_model_company

MIN_EXPERT_COUNT = 3
MAX_EXPERT_COUNT = 6
DEFAULT_EXPERT_COUNT = 4
DEFAULT_OUTPUT_TOKENS = 4096
SYNTHESIS_OUTPUT_TOKENS = 6144


class PriceRankedOrchestrationError(RuntimeError):
    """Raised when a valid price-ranked expert graph cannot be built."""


@dataclass(frozen=True)
class RankedEndpoint:
    model: str
    provider: str
    company: str
    provider_endpoint: str
    official_rank: int
    context_length: int
    max_completion_tokens: int
    prompt_price_per_million: float
    completion_price_per_million: float
    estimated_call_cost_usd: float
    supported_parameters: tuple[str, ...]
    source: Mapping[str, Any]

    def audit_row(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "company": self.company,
            "provider_endpoint": self.provider_endpoint,
            "official_intelligence_rank": self.official_rank,
            "context_length": self.context_length,
            "max_completion_tokens": self.max_completion_tokens,
            "prompt_price_per_million": self.prompt_price_per_million,
            "completion_price_per_million": self.completion_price_per_million,
            "estimated_call_cost_usd": self.estimated_call_cost_usd,
        }


def _positive_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _non_negative_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise PriceRankedOrchestrationError(
            "catalog endpoint has invalid pricing"
        ) from None
    if not math.isfinite(parsed) or parsed < 0:
        raise PriceRankedOrchestrationError(
            "catalog endpoint has invalid pricing"
        )
    return parsed


def _estimated_tokens(task_envelope: Mapping[str, Any]) -> tuple[int, int]:
    task_chars = _positive_int(task_envelope.get("task_characters"), 1)
    required_context = _positive_int(
        task_envelope.get("required_context_tokens"),
        4096,
    )
    prompt_tokens = min(
        required_context,
        max(2048, task_chars * 2 + 4096),
    )
    requested_output = _positive_int(
        task_envelope.get("completion_capacity_advisory_tokens"),
        DEFAULT_OUTPUT_TOKENS,
    )
    completion_tokens = max(
        1024,
        min(requested_output, DEFAULT_OUTPUT_TOKENS),
    )
    return prompt_tokens, completion_tokens


def _endpoint_from_row(
    row: Mapping[str, Any],
    *,
    prompt_tokens: int,
    completion_tokens: int,
    required_context_tokens: int,
) -> RankedEndpoint:
    model = str(row.get("model") or "").strip()
    provider = str(row.get("provider") or "").strip()
    raw_company = str(row.get("company") or "").strip()
    company = canonical_model_company(model)
    declared_company = (
        canonical_model_company(raw_company) if raw_company else company
    )
    provider_endpoint = str(row.get("provider_endpoint") or "").strip()
    official_rank = _positive_int(row.get("official_intelligence_rank"))
    context_length = _positive_int(row.get("context_length"))
    max_completion = _positive_int(row.get("max_completion_tokens"))
    prompt_price = _non_negative_float(row.get("prompt_price_per_million"))
    completion_price = _non_negative_float(
        row.get("completion_price_per_million")
    )
    expected_endpoint = f"{model}@{provider}"
    if (
        not stable_model_id(model)
        or not provider
        or provider_endpoint != expected_endpoint
        or not company
        or company == "unknown"
        or declared_company != company
        or official_rank <= 0
        or official_rank > MAX_VISIBLE_MODELS
        or context_length < required_context_tokens
        or max_completion < MINIMUM_EXPERT_COMPLETION_TOKENS
    ):
        raise PriceRankedOrchestrationError(
            "catalog endpoint has incomplete identity or capacity"
        )
    estimated_completion = min(completion_tokens, max_completion)
    estimated_cost = (
        prompt_tokens * prompt_price
        + estimated_completion * completion_price
    ) / 1_000_000
    supported = tuple(
        str(value)
        for value in row.get("supported_parameters", [])
        if str(value)
    )
    return RankedEndpoint(
        model=model,
        provider=provider,
        company=company,
        provider_endpoint=provider_endpoint,
        official_rank=official_rank,
        context_length=context_length,
        max_completion_tokens=max_completion,
        prompt_price_per_million=prompt_price,
        completion_price_per_million=completion_price,
        estimated_call_cost_usd=round(estimated_cost, 10),
        supported_parameters=supported,
        source=dict(row),
    )


def rank_endpoints(
    catalog: Mapping[str, Any],
    task_envelope: Mapping[str, Any],
    *,
    allow_synthetic_fixture: bool = False,
) -> list[RankedEndpoint]:
    rows = catalog.get("endpoints")
    if not isinstance(rows, list) or not rows:
        raise PriceRankedOrchestrationError("eligible endpoint catalog is empty")
    required_context_tokens = _positive_int(
        task_envelope.get("required_context_tokens")
    )
    if required_context_tokens <= 0:
        raise PriceRankedOrchestrationError(
            "task envelope lacks required context capacity"
        )
    prompt_tokens, completion_tokens = _estimated_tokens(task_envelope)
    endpoints = [
        _endpoint_from_row(
            row,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            required_context_tokens=required_context_tokens,
        )
        for row in rows
        if isinstance(row, Mapping)
        and (
            allow_synthetic_fixture
            or row.get("synthetic_fixture_only") is not True
        )
    ]
    endpoints = [
        endpoint
        for endpoint in endpoints
        if endpoint.company not in GOVERNANCE_COMPANIES
    ]
    if not endpoints:
        raise PriceRankedOrchestrationError(
            "catalog has no eligible endpoint after compatibility exclusions"
        )
    endpoints.sort(
        key=lambda item: (
            item.estimated_call_cost_usd,
            item.prompt_price_per_million
            + item.completion_price_per_million,
            item.official_rank,
            item.model,
            item.provider,
        )
    )
    return endpoints


def _distinct_company_endpoints(
    ranked: Sequence[RankedEndpoint],
) -> list[RankedEndpoint]:
    selected: list[RankedEndpoint] = []
    companies: set[str] = set()
    for endpoint in ranked:
        if endpoint.company in companies:
            continue
        selected.append(endpoint)
        companies.add(endpoint.company)
    return selected


def _role_blueprint(expert_count: int) -> list[dict[str, Any]]:
    if not MIN_EXPERT_COUNT <= expert_count <= MAX_EXPERT_COUNT:
        raise PriceRankedOrchestrationError(
            f"expert_count must be between {MIN_EXPERT_COUNT} and "
            f"{MAX_EXPERT_COUNT}"
        )
    independent_count = expert_count - 2
    lenses = (
        ("evidence", "证据、事实、数据质量、关键假设与不确定性"),
        ("options", "备选方案、机制、因果链与反事实"),
        ("risk", "风险、失败模式、约束、边界与实施条件"),
        ("stakeholders", "利益相关方、激励、二阶效应与现实扰动"),
    )
    roles: list[dict[str, Any]] = []
    for index in range(independent_count):
        lens_id, lens = lenses[index % len(lenses)]
        roles.append(
            {
                "kind": "independent",
                "lens_id": lens_id,
                "role": f"独立分析专家：重点检查{lens}",
                "functions": [
                    "independent_analysis",
                    "evidence_assessment",
                    "assumption_testing",
                ],
            }
        )
    roles.extend(
        [
            {
                "kind": "review",
                "lens_id": "review",
                "role": (
                    "交叉审查专家：比较前序分析，找出冲突、遗漏、"
                    "薄弱证据和失败模式"
                ),
                "functions": [
                    "cross_review",
                    "adversarial_testing",
                    "conflict_resolution",
                ],
            },
            {
                "kind": "synthesis",
                "lens_id": "synthesis",
                "role": (
                    "最终综合专家：依据原始任务和全部前序结果形成"
                    "唯一完整交付"
                ),
                "functions": [
                    "final_synthesis",
                    "decision_integration",
                    "output_contract_completion",
                ],
            },
        ]
    )
    return roles


def _assign_endpoints(
    chosen: Sequence[RankedEndpoint],
    roles: Sequence[Mapping[str, Any]],
) -> list[RankedEndpoint]:
    if len(chosen) != len(roles):
        raise PriceRankedOrchestrationError(
            "endpoint and role counts disagree"
        )
    by_quality = sorted(
        chosen,
        key=lambda item: (
            item.official_rank,
            item.estimated_call_cost_usd,
            item.model,
        ),
    )
    synthesis = by_quality[0]
    review = by_quality[1]
    remaining = [
        endpoint
        for endpoint in chosen
        if endpoint is not synthesis and endpoint is not review
    ]
    remaining.sort(
        key=lambda item: (
            item.estimated_call_cost_usd,
            item.official_rank,
            item.model,
        )
    )
    assigned: list[RankedEndpoint] = []
    cursor = 0
    for role in roles:
        kind = str(role.get("kind") or "")
        if kind == "synthesis":
            assigned.append(synthesis)
        elif kind == "review":
            assigned.append(review)
        else:
            assigned.append(remaining[cursor])
            cursor += 1
    return assigned


def _output_tokens(endpoint: RankedEndpoint, kind: str) -> int:
    desired = (
        SYNTHESIS_OUTPUT_TOKENS
        if kind == "synthesis"
        else DEFAULT_OUTPUT_TOKENS
    )
    return max(
        MINIMUM_EXPERT_COMPLETION_TOKENS,
        min(desired, endpoint.max_completion_tokens),
    )


def _recovery_pool(
    ranked: Sequence[RankedEndpoint],
    chosen: Sequence[RankedEndpoint],
    roles: Sequence[Mapping[str, Any]],
    assigned: Sequence[RankedEndpoint],
    recovery_calls: int,
) -> tuple[list[RankedEndpoint], int]:
    if recovery_calls == 0:
        return [], 0
    output_floor = max(
        _output_tokens(endpoint, str(role.get("kind") or ""))
        for role, endpoint in zip(roles, assigned, strict=True)
    )
    selected_companies = {endpoint.company for endpoint in chosen}
    compatible = [
        endpoint
        for endpoint in ranked
        if endpoint.company not in selected_companies
        and endpoint.max_completion_tokens >= output_floor
    ]
    candidates = _distinct_company_endpoints(compatible)
    if len(candidates) < recovery_calls:
        raise PriceRankedOrchestrationError(
            "not enough distinct recovery endpoints with provider-native "
            f"output capacity: need {recovery_calls}, found {len(candidates)}, "
            f"floor {output_floor}"
        )
    return candidates[:recovery_calls], output_floor


def _work_items(roles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    independent_ids = [
        f"work-independent-{index + 1}"
        for index, role in enumerate(roles)
        if role.get("kind") == "independent"
    ]
    items: list[dict[str, Any]] = []
    independent_cursor = 0
    for role in roles:
        kind = str(role.get("kind") or "")
        if kind == "independent":
            work_id = independent_ids[independent_cursor]
            independent_cursor += 1
            items.append(
                {
                    "work_id": work_id,
                    "objective": str(role["role"]),
                    "dependencies": [],
                    "required_outputs": [
                        "核心判断",
                        "关键证据与依据",
                        "不确定性与反例",
                        "可执行建议",
                    ],
                }
            )
        elif kind == "review":
            items.append(
                {
                    "work_id": "work-cross-review",
                    "objective": str(role["role"]),
                    "dependencies": independent_ids,
                    "required_outputs": [
                        "一致结论",
                        "主要冲突",
                        "证据薄弱点",
                        "必须修正事项",
                    ],
                }
            )
        else:
            items.append(
                {
                    "work_id": "work-final-synthesis",
                    "objective": str(role["role"]),
                    "dependencies": [
                        *independent_ids,
                        "work-cross-review",
                    ],
                    "required_outputs": [
                        "直接结论",
                        "推理链",
                        "关键证据",
                        "风险与不确定性",
                        "行动方案",
                        "否决条件",
                    ],
                }
            )
    return items


def _nodes(
    roles: Sequence[Mapping[str, Any]],
    assigned: Sequence[RankedEndpoint],
    recoveries: Sequence[RankedEndpoint],
) -> list[dict[str, Any]]:
    priority = [
        index
        for kind in ("synthesis", "review", "independent")
        for index, role in enumerate(roles)
        if role.get("kind") == kind
    ]
    recovery_by_index: dict[int, list[RankedEndpoint]] = {}
    for recovery_index, recovery in enumerate(recoveries):
        node_index = priority[recovery_index % len(priority)]
        recovery_by_index.setdefault(node_index, []).append(recovery)

    nodes: list[dict[str, Any]] = []
    independent_index = 0
    for index, (role, endpoint) in enumerate(
        zip(roles, assigned, strict=True)
    ):
        kind = str(role.get("kind") or "")
        if kind == "independent":
            independent_index += 1
            node_id = f"expert-independent-{independent_index}"
            work_ids = [f"work-independent-{independent_index}"]
        elif kind == "review":
            node_id = "expert-cross-review"
            work_ids = ["work-cross-review"]
        else:
            node_id = "expert-final-synthesis"
            work_ids = ["work-final-synthesis"]
        output_tokens = _output_tokens(endpoint, kind)
        fallbacks = recovery_by_index.get(index, [])
        if any(
            fallback.max_completion_tokens < output_tokens
            for fallback in fallbacks
        ):
            raise PriceRankedOrchestrationError(
                "recovery endpoint lacks provider-native output capacity"
            )
        nodes.append(
            {
                "node_id": node_id,
                "work_ids": work_ids,
                "role": str(role["role"]),
                "functions": list(role["functions"]),
                "model": endpoint.model,
                "provider": endpoint.provider,
                "reasoning_effort": (
                    "high"
                    if kind in {"review", "synthesis"}
                    else "medium"
                ),
                "max_output_tokens": output_tokens,
                "recovery": [
                    {
                        "model": fallback.model,
                        "provider": fallback.provider,
                    }
                    for fallback in fallbacks
                ],
            }
        )
    return nodes


def _edges(roles: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    independent_nodes = [
        f"expert-independent-{index + 1}"
        for index, role in enumerate(roles)
        if role.get("kind") == "independent"
    ]
    edges = [
        {
            "source": node_id,
            "target": "expert-cross-review",
            "relation_type": "review",
        }
        for node_id in independent_nodes
    ]
    edges.extend(
        {
            "source": node_id,
            "target": "expert-final-synthesis",
            "relation_type": "synthesis",
        }
        for node_id in independent_nodes
    )
    edges.append(
        {
            "source": "expert-cross-review",
            "target": "expert-final-synthesis",
            "relation_type": "synthesis",
        }
    )
    return edges


def _validate_dag(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> None:
    node_ids = [str(node["node_id"]) for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise PriceRankedOrchestrationError(
            "generated expert graph has duplicate nodes"
        )
    known = set(node_ids)
    if any(
        str(edge["source"]) not in known
        or str(edge["target"]) not in known
        for edge in edges
    ):
        raise PriceRankedOrchestrationError(
            "generated expert graph references an unknown node"
        )
    graph = nx.DiGraph()
    graph.add_nodes_from(node_ids)
    graph.add_edges_from(
        (str(edge["source"]), str(edge["target"]))
        for edge in edges
    )
    final = "expert-final-synthesis"
    if not nx.is_directed_acyclic_graph(graph):
        raise PriceRankedOrchestrationError(
            "generated expert graph is cyclic"
        )
    if final not in graph or graph.out_degree(final) != 0:
        raise PriceRankedOrchestrationError(
            "final synthesis node must exist and be a sink"
        )
    if any(
        node_id != final and not nx.has_path(graph, node_id, final)
        for node_id in node_ids
    ):
        raise PriceRankedOrchestrationError(
            "every expert node must contribute to final synthesis"
        )


def build_price_ranked_proposal(
    *,
    catalog: Mapping[str, Any],
    task_envelope: Mapping[str, Any],
    expert_count: int = DEFAULT_EXPERT_COUNT,
    recovery_calls: int = 0,
    allow_synthetic_fixture: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ranked = rank_endpoints(
        catalog,
        task_envelope,
        allow_synthetic_fixture=allow_synthetic_fixture,
    )
    expert_count = int(expert_count)
    recovery_calls = int(recovery_calls)
    if recovery_calls < 0:
        raise PriceRankedOrchestrationError(
            "recovery_calls must be non-negative"
        )
    roles = _role_blueprint(expert_count)
    distinct = _distinct_company_endpoints(ranked)
    if len(distinct) < expert_count:
        raise PriceRankedOrchestrationError(
            "not enough eligible endpoints from distinct model companies: "
            f"need {expert_count}, found {len(distinct)}"
        )
    chosen = distinct[:expert_count]
    assigned = _assign_endpoints(chosen, roles)
    recovery_pool, recovery_output_floor = _recovery_pool(
        ranked,
        chosen,
        roles,
        assigned,
        recovery_calls,
    )
    work_items = _work_items(roles)
    nodes = _nodes(roles, assigned, recovery_pool)
    attached_recovery_count = sum(
        len(node.get("recovery", [])) for node in nodes
    )
    if attached_recovery_count != recovery_calls:
        raise PriceRankedOrchestrationError(
            "recovery endpoint attachment count disagrees with reserve"
        )
    edges = _edges(roles)
    _validate_dag(nodes, edges)
    proposal = {
        "work_items": work_items,
        "nodes": nodes,
        "edges": edges,
        "final_nodes": ["expert-final-synthesis"],
    }
    audit = {
        "schema_version": "v5-price-ranked-selection-2",
        "status": "PASS",
        "selection_authority": "python-price-ranked-orchestrator",
        "selection_policy": (
            "eligible-top-intelligence-catalog -> estimated-task-cost-ascending "
            "-> distinct-model-companies -> recovery-native-capacity"
        ),
        "expert_count": expert_count,
        "recovery_count": recovery_calls,
        "attached_recovery_count": attached_recovery_count,
        "recovery_native_output_floor_tokens": recovery_output_floor,
        "recovery_distribution": {
            str(node["node_id"]): len(node.get("recovery", []))
            for node in nodes
        },
        "ranked_endpoint_count": len(ranked),
        "distinct_company_endpoint_count": len(distinct),
        "cheapest_candidate_set": [
            endpoint.audit_row() for endpoint in chosen
        ],
        "selected_endpoints": [
            endpoint.audit_row() for endpoint in assigned
        ],
        "recovery_endpoints": [
            endpoint.audit_row() for endpoint in recovery_pool
        ],
        "selected_total_estimated_cost_usd": round(
            sum(
                endpoint.estimated_call_cost_usd
                for endpoint in assigned
            ),
            10,
        ),
        "local_model_calls": 0,
        "claude_calls": 0,
        "gpt_selection_calls": 0,
        "optimizer_used": False,
        "agent_framework_used": False,
        "compatibility_excluded_companies": sorted(
            GOVERNANCE_COMPANIES
        ),
        "synthetic_fixture_allowed": bool(allow_synthetic_fixture),
        "networkx_used_for_dag_validation": True,
        "cross_task_history_used": False,
    }
    return proposal, audit


__all__ = [
    "DEFAULT_EXPERT_COUNT",
    "MAX_EXPERT_COUNT",
    "MIN_EXPERT_COUNT",
    "PriceRankedOrchestrationError",
    "build_price_ranked_proposal",
    "rank_endpoints",
]
