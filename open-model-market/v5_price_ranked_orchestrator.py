"""Deterministic low-price expert-team selection and DAG orchestration.

The selector reads the already validated exact endpoint catalog, ranks eligible
endpoints by estimated task cost, keeps model companies globally distinct, and
builds a bounded expert graph. It performs no model call and uses no cross-task
history.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import networkx as nx

from v5_catalog_view import GOVERNANCE_COMPANIES
from v5_model_company import canonical_model_company

MIN_EXPERT_COUNT = 3
MAX_EXPERT_COUNT = 6
DEFAULT_EXPERT_COUNT = 4
DEFAULT_OUTPUT_TOKENS = 4096


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
        return 0.0
    return parsed if parsed >= 0 else 0.0


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
    completion_tokens = max(1024, min(requested_output, DEFAULT_OUTPUT_TOKENS))
    return prompt_tokens, completion_tokens


def _endpoint_from_row(
    row: Mapping[str, Any],
    *,
    prompt_tokens: int,
    completion_tokens: int,
) -> RankedEndpoint:
    model = str(row.get("model") or "").strip()
    provider = str(row.get("provider") or "").strip()
    company = str(row.get("company") or "").strip()
    if not company and model:
        company = canonical_model_company(model)
    provider_endpoint = str(row.get("provider_endpoint") or "").strip()
    official_rank = _positive_int(row.get("official_intelligence_rank"))
    context_length = _positive_int(row.get("context_length"))
    max_completion = _positive_int(row.get("max_completion_tokens"))
    prompt_price = _non_negative_float(row.get("prompt_price_per_million"))
    completion_price = _non_negative_float(
        row.get("completion_price_per_million")
    )
    if (
        not model
        or not provider
        or not company
        or company == "unknown"
        or official_rank <= 0
        or context_length <= 0
        or max_completion <= 0
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
    prompt_tokens, completion_tokens = _estimated_tokens(task_envelope)
    endpoints = [
        _endpoint_from_row(
            row,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
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
            f"expert_count must be between {MIN_EXPERT_COUNT} and {MAX_EXPERT_COUNT}"
        )
    independent_count = max(0, expert_count - 2)
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
    roles.append(
        {
            "kind": "review",
            "lens_id": "review",
            "role": "交叉审查专家：比较前序分析，找出冲突、遗漏、薄弱证据和失败模式",
            "functions": [
                "cross_review",
                "adversarial_testing",
                "conflict_resolution",
            ],
        }
    )
    roles.append(
        {
            "kind": "synthesis",
            "lens_id": "synthesis",
            "role": "最终综合专家：依据原始任务和全部前序结果形成唯一完整交付",
            "functions": [
                "final_synthesis",
                "decision_integration",
                "output_contract_completion",
            ],
        }
    )
    return roles


def _assign_endpoints(
    chosen: Sequence[RankedEndpoint],
    roles: Sequence[Mapping[str, Any]],
) -> list[RankedEndpoint]:
    if len(chosen) != len(roles):
        raise PriceRankedOrchestrationError("endpoint and role counts disagree")
    by_quality = sorted(
        chosen,
        key=lambda item: (
            item.official_rank,
            item.estimated_call_cost_usd,
            item.model,
        ),
    )
    synthesis = by_quality[0]
    review = by_quality[1] if len(by_quality) > 1 else by_quality[0]
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
    desired = 6144 if kind == "synthesis" else 4096
    return max(256, min(desired, endpoint.max_completion_tokens))


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
        elif kind == "synthesis":
            items.append(
                {
                    "work_id": "work-final-synthesis",
                    "objective": str(role["role"]),
                    "dependencies": [*independent_ids, "work-cross-review"],
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
    nodes: list[dict[str, Any]] = []
    independent_index = 0
    recovery_by_index: dict[int, RankedEndpoint] = {}
    priority = [
        index
        for kind in ("synthesis", "review", "independent")
        for index, role in enumerate(roles)
        if role.get("kind") == kind
    ]
    for index, recovery in zip(priority, recoveries, strict=False):
        recovery_by_index[index] = recovery
    for index, (role, endpoint) in enumerate(zip(roles, assigned, strict=True)):
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
        recovery_rows: list[dict[str, str]] = []
        fallback = recovery_by_index.get(index)
        if fallback is not None:
            recovery_rows.append(
                {"model": fallback.model, "provider": fallback.provider}
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
                    "high" if kind in {"review", "synthesis"} else "medium"
                ),
                "max_output_tokens": _output_tokens(endpoint, kind),
                "recovery": recovery_rows,
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
    graph = nx.DiGraph()
    graph.add_nodes_from(str(node["node_id"]) for node in nodes)
    graph.add_edges_from(
        (str(edge["source"]), str(edge["target"])) for edge in edges
    )
    if not nx.is_directed_acyclic_graph(graph):
        raise PriceRankedOrchestrationError("generated expert graph is cyclic")
    if "expert-final-synthesis" not in graph:
        raise PriceRankedOrchestrationError("final synthesis node is missing")


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
    distinct = _distinct_company_endpoints(ranked)
    expert_count = int(expert_count)
    recovery_calls = max(0, int(recovery_calls))
    required = expert_count + recovery_calls
    if len(distinct) < required:
        raise PriceRankedOrchestrationError(
            "not enough eligible endpoints from distinct model companies: "
            f"need {required}, found {len(distinct)}"
        )
    chosen = distinct[:expert_count]
    recovery_pool = distinct[expert_count:required]
    roles = _role_blueprint(expert_count)
    assigned = _assign_endpoints(chosen, roles)
    work_items = _work_items(roles)
    nodes = _nodes(roles, assigned, recovery_pool)
    edges = _edges(roles)
    _validate_dag(nodes, edges)
    proposal = {
        "work_items": work_items,
        "nodes": nodes,
        "edges": edges,
        "final_nodes": ["expert-final-synthesis"],
    }
    audit = {
        "schema_version": "v5-price-ranked-selection-1",
        "status": "PASS",
        "selection_authority": "python-price-ranked-orchestrator",
        "selection_policy": (
            "eligible-top-intelligence-catalog -> estimated-task-cost-ascending "
            "-> distinct-model-companies"
        ),
        "expert_count": expert_count,
        "recovery_count": recovery_calls,
        "ranked_endpoint_count": len(ranked),
        "distinct_company_endpoint_count": len(distinct),
        "cheapest_candidate_set": [
            endpoint.audit_row() for endpoint in chosen
        ],
        "selected_endpoints": [endpoint.audit_row() for endpoint in assigned],
        "recovery_endpoints": [
            endpoint.audit_row() for endpoint in recovery_pool
        ],
        "selected_total_estimated_cost_usd": round(
            sum(endpoint.estimated_call_cost_usd for endpoint in assigned),
            10,
        ),
        "local_model_calls": 0,
        "claude_calls": 0,
        "gpt_selection_calls": 0,
        "optimizer_used": False,
        "agent_framework_used": False,
        "compatibility_excluded_companies": sorted(GOVERNANCE_COMPANIES),
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
