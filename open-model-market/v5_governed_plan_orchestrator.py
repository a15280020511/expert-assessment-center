"""Build an expert DAG from an immutable governance-selected model plan.

This module does not rank or choose models. It only resolves a compatible exact
provider endpoint for each model named by governance and builds the finite DAG.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import networkx as nx

from v5_governance_model_plan import validate_governance_model_plan
from v5_model_company import canonical_model_company

MINIMUM_COMPLETION_TOKENS = 1_024
DEFAULT_OUTPUT_TOKENS = 4_096
SYNTHESIS_OUTPUT_TOKENS = 6_144


class GovernedPlanOrchestrationError(RuntimeError):
    """Raised when a governance-selected model cannot be executed exactly."""


def _positive_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _finite_nonnegative(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise GovernedPlanOrchestrationError(f"{field} is not numeric") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise GovernedPlanOrchestrationError(
            f"{field} must be finite and nonnegative"
        )
    return parsed


def _estimated_tokens(task_envelope: Mapping[str, Any]) -> tuple[int, int]:
    task_chars = _positive_int(task_envelope.get("task_characters"), 1)
    required_context = _positive_int(
        task_envelope.get("required_context_tokens"), 4_096
    )
    prompt_tokens = min(required_context, max(2_048, task_chars * 2 + 4_096))
    requested_output = _positive_int(
        task_envelope.get("completion_capacity_advisory_tokens"),
        DEFAULT_OUTPUT_TOKENS,
    )
    return prompt_tokens, max(1_024, min(requested_output, DEFAULT_OUTPUT_TOKENS))


def _endpoint_cost(
    row: Mapping[str, Any], prompt_tokens: int, completion_tokens: int
) -> float:
    prompt_price = _finite_nonnegative(
        row.get("prompt_price_per_million"),
        "prompt_price_per_million",
    )
    completion_price = _finite_nonnegative(
        row.get("completion_price_per_million"),
        "completion_price_per_million",
    )
    maximum = _positive_int(row.get("max_completion_tokens"))
    charged_completion = min(completion_tokens, maximum)
    return round(
        (
            prompt_tokens * prompt_price
            + charged_completion * completion_price
        )
        / 1_000_000,
        10,
    )


def _resolve_model_endpoint(
    catalog: Mapping[str, Any],
    model_record: Mapping[str, Any],
    task_envelope: Mapping[str, Any],
) -> dict[str, Any]:
    endpoints = catalog.get("endpoints")
    if not isinstance(endpoints, list):
        raise GovernedPlanOrchestrationError("exact endpoint catalog is missing")
    model = str(model_record.get("model") or "").strip()
    declared_company = canonical_model_company(
        str(model_record.get("company") or "").strip()
    )
    actual_company = canonical_model_company(model)
    if not model or actual_company == "unknown" or declared_company != actual_company:
        raise GovernedPlanOrchestrationError(
            f"governance model company mismatch: {model}"
        )

    required_context = _positive_int(task_envelope.get("required_context_tokens"))
    prompt_tokens, completion_tokens = _estimated_tokens(task_envelope)
    compatible: list[dict[str, Any]] = []
    for raw in endpoints:
        if not isinstance(raw, Mapping) or str(raw.get("model") or "") != model:
            continue
        provider = str(raw.get("provider") or "").strip()
        endpoint = str(raw.get("provider_endpoint") or "").strip()
        context = _positive_int(raw.get("context_length"))
        maximum = _positive_int(raw.get("max_completion_tokens"))
        if (
            not provider
            or endpoint != f"{model}@{provider}"
            or context < required_context
            or maximum < MINIMUM_COMPLETION_TOKENS
            or raw.get("synthetic_fixture_only") is True
        ):
            continue
        row = dict(raw)
        row["resolved_estimated_call_cost_usd"] = _endpoint_cost(
            row, prompt_tokens, completion_tokens
        )
        compatible.append(row)
    if not compatible:
        raise GovernedPlanOrchestrationError(
            f"no compatible exact provider endpoint for governance-selected model {model}"
        )
    compatible.sort(
        key=lambda row: (
            float(row["resolved_estimated_call_cost_usd"]),
            _positive_int(row.get("official_intelligence_rank"), 10**9),
            str(row.get("provider") or ""),
        )
    )
    return compatible[0]


def _role_functions(kind: str) -> list[str]:
    if kind == "review":
        return ["cross_review", "adversarial_testing", "conflict_resolution"]
    if kind == "synthesis":
        return [
            "final_synthesis",
            "decision_integration",
            "output_contract_completion",
        ]
    return [
        "independent_analysis",
        "evidence_assessment",
        "assumption_testing",
    ]


def _node_id(kind: str, independent_index: int) -> str:
    if kind == "review":
        return "expert-cross-review"
    if kind == "synthesis":
        return "expert-final-synthesis"
    return f"expert-independent-{independent_index}"


def _work_id(kind: str, independent_index: int) -> str:
    if kind == "review":
        return "work-cross-review"
    if kind == "synthesis":
        return "work-final-synthesis"
    return f"work-independent-{independent_index}"


def _build_work_items(selected: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    independent_ids = [
        f"work-independent-{index}"
        for index, row in enumerate(
            (row for row in selected if row.get("role_kind") == "independent"),
            1,
        )
    ]
    result: list[dict[str, Any]] = []
    independent_index = 0
    for row in selected:
        kind = str(row["role_kind"])
        if kind == "independent":
            independent_index += 1
            result.append(
                {
                    "work_id": _work_id(kind, independent_index),
                    "objective": str(row["role"]),
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
            result.append(
                {
                    "work_id": "work-cross-review",
                    "objective": str(row["role"]),
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
            result.append(
                {
                    "work_id": "work-final-synthesis",
                    "objective": str(row["role"]),
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
    return result


def _output_tokens(endpoint: Mapping[str, Any], kind: str) -> int:
    desired = SYNTHESIS_OUTPUT_TOKENS if kind == "synthesis" else DEFAULT_OUTPUT_TOKENS
    maximum = _positive_int(endpoint.get("max_completion_tokens"))
    return max(MINIMUM_COMPLETION_TOKENS, min(desired, maximum))


def _build_nodes(
    selected: Sequence[Mapping[str, Any]],
    endpoints: Sequence[Mapping[str, Any]],
    recovery_endpoints: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    independent_index = 0
    nodes: list[dict[str, Any]] = []
    for row, endpoint in zip(selected, endpoints, strict=True):
        kind = str(row["role_kind"])
        if kind == "independent":
            independent_index += 1
        node_id = _node_id(kind, independent_index)
        work_id = _work_id(kind, independent_index)
        nodes.append(
            {
                "node_id": node_id,
                "work_ids": [work_id],
                "role": str(row["role"]),
                "functions": _role_functions(kind),
                "model": str(row["model"]),
                "provider": str(endpoint["provider"]),
                "reasoning_effort": (
                    "high" if kind in {"review", "synthesis"} else "medium"
                ),
                "max_output_tokens": _output_tokens(endpoint, kind),
                "recovery": [],
            }
        )

    priority = [
        index
        for kind in ("synthesis", "review", "independent")
        for index, row in enumerate(selected)
        if row.get("role_kind") == kind
    ]
    for recovery_index, endpoint in enumerate(recovery_endpoints):
        node_index = priority[recovery_index % len(priority)]
        if _positive_int(endpoint.get("max_completion_tokens")) < int(
            nodes[node_index]["max_output_tokens"]
        ):
            raise GovernedPlanOrchestrationError(
                "governance recovery model lacks provider-native output capacity"
            )
        nodes[node_index]["recovery"].append(
            {
                "model": str(endpoint["model"]),
                "provider": str(endpoint["provider"]),
            }
        )
    return nodes


def _build_edges(selected: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    independent_nodes = [
        f"expert-independent-{index}"
        for index, _ in enumerate(
            (row for row in selected if row.get("role_kind") == "independent"),
            1,
        )
    ]
    edges = [
        {"source": node, "target": "expert-cross-review", "relation_type": "review"}
        for node in independent_nodes
    ]
    edges.extend(
        {
            "source": node,
            "target": "expert-final-synthesis",
            "relation_type": "synthesis",
        }
        for node in independent_nodes
    )
    edges.append(
        {
            "source": "expert-cross-review",
            "target": "expert-final-synthesis",
            "relation_type": "synthesis",
        }
    )
    return edges


def _validate_dag(nodes: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]) -> None:
    graph = nx.DiGraph()
    node_ids = [str(node["node_id"]) for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise GovernedPlanOrchestrationError("governance expert graph has duplicate nodes")
    graph.add_nodes_from(node_ids)
    graph.add_edges_from(
        (str(edge["source"]), str(edge["target"])) for edge in edges
    )
    final = "expert-final-synthesis"
    if not nx.is_directed_acyclic_graph(graph):
        raise GovernedPlanOrchestrationError("governance expert graph is cyclic")
    if final not in graph or graph.out_degree(final) != 0:
        raise GovernedPlanOrchestrationError("final synthesis node must be the sink")
    if any(
        node != final and not nx.has_path(graph, node, final)
        for node in node_ids
    ):
        raise GovernedPlanOrchestrationError(
            "every governance-selected expert must contribute to final synthesis"
        )


def build_governed_proposal(
    *,
    ticket: Mapping[str, Any],
    catalog: Mapping[str, Any],
    task_envelope: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = validate_governance_model_plan(ticket)
    selected = plan["selected_models"]
    recoveries = plan["recovery_models"]
    endpoints = [
        _resolve_model_endpoint(catalog, row, task_envelope) for row in selected
    ]
    recovery_endpoints = [
        _resolve_model_endpoint(catalog, row, task_envelope) for row in recoveries
    ]
    work_items = _build_work_items(selected)
    nodes = _build_nodes(selected, endpoints, recovery_endpoints)
    edges = _build_edges(selected)
    _validate_dag(nodes, edges)

    proposal = {
        "work_items": work_items,
        "nodes": nodes,
        "edges": edges,
        "final_nodes": ["expert-final-synthesis"],
    }
    audit = {
        "schema_version": "v5-governance-plan-materialization-v1",
        "status": "PASS",
        "selection_authority": plan["selection_authority"],
        "plan_sha256": plan["plan_sha256"],
        "model_selection_performed_locally": False,
        "model_reranking_performed_locally": False,
        "model_substitution_performed_locally": False,
        "provider_resolution_performed_locally": True,
        "provider_resolution_policy": (
            "exact governance-selected model -> cheapest compatible exact endpoint"
        ),
        "selected_models": [str(row["model"]) for row in selected],
        "resolved_endpoints": [
            {
                "model": str(endpoint["model"]),
                "provider": str(endpoint["provider"]),
                "provider_endpoint": str(endpoint["provider_endpoint"]),
                "resolved_estimated_call_cost_usd": endpoint[
                    "resolved_estimated_call_cost_usd"
                ],
            }
            for endpoint in endpoints
        ],
        "recovery_models": [str(row["model"]) for row in recoveries],
        "resolved_recovery_endpoints": [
            {
                "model": str(endpoint["model"]),
                "provider": str(endpoint["provider"]),
                "provider_endpoint": str(endpoint["provider_endpoint"]),
            }
            for endpoint in recovery_endpoints
        ],
        "networkx_used_for_dag_validation": True,
        "cross_task_history_used": False,
    }
    return proposal, audit


__all__ = [
    "GovernedPlanOrchestrationError",
    "build_governed_proposal",
]
