"""Production hardening for the V5 dynamic execution graph.

This layer is deliberately isolated from V3. It adds:
- conservative real-provider qualification;
- strict JSON-schema delivery for machine-readable nodes;
- a real 10,000-token maximum allowance;
- worst-case pre-execution cost envelopes;
- coverage-weighted cost-performance optimization;
- provider-diverse recovery candidates;
- work-quorum execution with deterministic degraded synthesis.
"""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ortools.sat.python import cp_model

import v5_executor
import v5_planner
import v5_value_optimizer
from execution_graph import ExecutionGraph, GraphLimits, SelectedNode
from execution_graph_validator import validate_execution_graph

OUTPUT_ALLOWANCE_TOKENS = 10_000
MINIMUM_ENDPOINT_RELIABILITY = 0.85
MINIMUM_CRITICAL_ENDPOINT_RELIABILITY = 0.90
MINIMUM_USABLE_WORK_COVERAGE = 0.80
COST_SAFETY_MULTIPLIER = 1.10
P95_TOKEN_MULTIPLIER = 1.35
CALL_OVERHEAD_USD = v5_value_optimizer.CALL_OVERHEAD_USD
COST_SCALE = v5_value_optimizer.COST_SCALE
QUALITY_SCALE = v5_value_optimizer.QUALITY_SCALE

_INSTALLED = False
_ORIGINAL_QUALITY_GATE: Callable[..., tuple[bool, float, list[str]]] | None = None


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _endpoint_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if isinstance(data, Mapping) and isinstance(data.get("endpoints"), list):
        return [row for row in data["endpoints"] if isinstance(row, dict)]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(payload.get("endpoints"), list):
        return [row for row in payload["endpoints"] if isinstance(row, dict)]
    return []


def _sanitize_endpoint_payloads(
    payloads: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    """Treat missing uptime as unknown, never as the old optimistic 0.97."""
    result: dict[str, Mapping[str, Any]] = {}
    for model_id, payload in dict(payloads or {}).items():
        cloned = _json_copy(payload)
        for row in _endpoint_rows(cloned):
            if not any(
                row.get(key) not in {None, ""}
                for key in ("uptime_last_30m", "uptime", "availability")
            ):
                row["uptime"] = 0.0
                row["v5_reliability_evidence"] = "missing-rejected"
        result[str(model_id)] = cloned
    return result


def _work_metadata(resource_bundle: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for interpretation in resource_bundle.get("task_semantics", {}).get("interpretations", []):
        if not isinstance(interpretation, Mapping):
            continue
        interpretation_id = str(interpretation.get("interpretation_id") or "")
        rows: dict[str, dict[str, Any]] = {}
        for work in interpretation.get("atomic_work", []):
            if not isinstance(work, Mapping):
                continue
            work_id = str(work.get("work_id") or "")
            operations = {
                str(key): float(value)
                for key, value in dict(work.get("operation_requirements", {})).items()
            }
            importance = float(work.get("importance", 0.5))
            error_cost = float(work.get("error_cost", 0.5))
            synthesis = "synthesis" in operations
            rows[work_id] = {
                "importance": importance,
                "error_cost": error_cost,
                "critical": bool(
                    not synthesis and (importance >= 0.85 or error_cost >= 0.80)
                ),
                "synthesis": synthesis,
                "operations": sorted(operations),
                "context_requirements": dict(work.get("context_requirements", {})),
                "minimum_successful_copies": 1,
            }
        result[interpretation_id] = rows
    return result


def _strict_json_schema(required_fields: Sequence[str]) -> dict[str, Any]:
    fields = [str(field) for field in required_fields if str(field)]
    return {
        "name": "v5_node_delivery",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                field: {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 8,
                }
                for field in fields
            },
            "required": fields,
            "additionalProperties": False,
        },
    }


def _candidate_cost_envelope(
    candidate: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    works: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    assigned = [works[work_id] for work_id in candidate.get("assigned_work", []) if work_id in works]
    prompt_tokens = sum(
        int(row.get("context_requirements", {}).get("system_prompt_tokens", 0))
        + int(row.get("context_requirements", {}).get("original_task_tokens", 0))
        + int(row.get("context_requirements", {}).get("visible_upstream_tokens", 0))
        for row in assigned
    )
    if len(assigned) > 1:
        prompt_tokens = int(prompt_tokens * 0.85)
    expected_completion = sum(
        int(row.get("context_requirements", {}).get("expected_output_tokens", 0))
        + int(row.get("context_requirements", {}).get("expected_reasoning_tokens", 0))
        for row in assigned
    )
    p95_completion = min(
        OUTPUT_ALLOWANCE_TOKENS,
        max(1, int(math.ceil(expected_completion * P95_TOKEN_MULTIPLIER))),
    )
    prompt_ppm = float(endpoint.get("prompt_price_per_million", 0.0))
    completion_ppm = float(endpoint.get("completion_price_per_million", 0.0))
    expected = (
        prompt_tokens * prompt_ppm + expected_completion * completion_ppm
    ) / 1_000_000
    p95 = (
        prompt_tokens * prompt_ppm + p95_completion * completion_ppm
    ) / 1_000_000
    worst = (
        prompt_tokens * prompt_ppm + OUTPUT_ALLOWANCE_TOKENS * completion_ppm
    ) / 1_000_000
    return {
        "prompt_tokens_estimated": prompt_tokens,
        "expected_completion_and_reasoning_tokens": expected_completion,
        "p95_completion_and_reasoning_tokens": p95_completion,
        "max_completion_tokens_sent": OUTPUT_ALLOWANCE_TOKENS,
        "expected_cost_usd": round(expected, 8),
        "p95_cost_usd": round(p95 * COST_SAFETY_MULTIPLIER, 8),
        "worst_case_cost_usd": round(worst * COST_SAFETY_MULTIPLIER, 8),
        "safety_multiplier": COST_SAFETY_MULTIPLIER,
        "pricing_basis": "real-provider-endpoint",
    }


def _prepare_candidate_bundle(
    resource_bundle: Mapping[str, Any],
    market: Mapping[str, Any],
    *,
    maximum_per_group: int,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    candidate_bundle = v5_planner.generate_candidate_graph(
        resource_bundle,
        market,
        maximum_per_group=maximum_per_group,
    )
    work_meta = _work_metadata(resource_bundle)
    endpoint_by_name = {
        str(row.get("provider_endpoint")): row
        for row in market.get("endpoints", [])
        if isinstance(row, Mapping)
    }
    envelopes: dict[str, dict[str, Any]] = {}
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []

    for raw in candidate_bundle.get("candidates", []):
        candidate = dict(raw)
        interpretation_id = str(candidate.get("interpretation_id") or "")
        works = work_meta.get(interpretation_id, {})
        endpoint = endpoint_by_name.get(str(candidate.get("provider_endpoint") or ""))
        if endpoint is None:
            rejected.append({"candidate_id": str(candidate.get("candidate_id")), "reason": "endpoint-not-found"})
            continue
        reliability = float(endpoint.get("reliability", 0.0))
        critical = any(
            bool(works.get(str(work_id), {}).get("critical"))
            for work_id in candidate.get("assigned_work", [])
        )
        minimum_reliability = (
            MINIMUM_CRITICAL_ENDPOINT_RELIABILITY
            if critical
            else MINIMUM_ENDPOINT_RELIABILITY
        )
        if reliability + 1e-12 < minimum_reliability:
            rejected.append({
                "candidate_id": str(candidate.get("candidate_id")),
                "reason": f"endpoint-reliability<{minimum_reliability:.2f}",
            })
            continue

        contract = dict(candidate.get("output_contract", {}))
        supported = {
            str(value).casefold()
            for value in candidate.get("parameter_profile", {}).get("supported_parameters", [])
        }
        machine = bool(contract.get("machine_readable_required"))
        if machine and not supported.intersection(
            {"structured_outputs", "response_format", "json_schema"}
        ):
            rejected.append({
                "candidate_id": str(candidate.get("candidate_id")),
                "reason": "machine-readable-node-without-structured-output-support",
            })
            continue
        if int(endpoint.get("max_completion_tokens", 0) or 0) < OUTPUT_ALLOWANCE_TOKENS:
            rejected.append({
                "candidate_id": str(candidate.get("candidate_id")),
                "reason": "endpoint-output-allowance-below-10000",
            })
            continue

        request_config = dict(candidate.get("request_config", {}))
        request_config["max_tokens"] = OUTPUT_ALLOWANCE_TOKENS
        if machine:
            request_config["response_format"] = {
                "type": "json_schema",
                "json_schema": _strict_json_schema(
                    contract.get("required_fields", [])
                ),
            }
            if "reasoning" in request_config:
                request_config["reasoning"] = {
                    "effort": "low",
                    "exclude": True,
                }
        candidate["request_config"] = request_config
        envelope = _candidate_cost_envelope(candidate, endpoint, works)
        candidate["estimated_cost"] = envelope["worst_case_cost_usd"]
        candidate_id = str(candidate.get("candidate_id") or "")
        envelopes[candidate_id] = envelope
        kept.append(candidate)

    if not kept:
        raise v5_planner.V5PlanningError(
            "Production hardening removed every candidate: no endpoint simultaneously "
            "satisfies reliability, 10k allowance, structured-output, and cost-safety requirements."
        )
    candidate_bundle = dict(candidate_bundle)
    candidate_bundle["candidates"] = kept
    candidate_bundle["candidate_count_after_production_hardening"] = len(kept)
    candidate_bundle["production_hardening_rejected"] = rejected
    for interpretation_id, meta in candidate_bundle.get("interpretations", {}).items():
        if isinstance(meta, dict):
            meta["work_policy"] = work_meta.get(str(interpretation_id), {})
    return candidate_bundle, work_meta, envelopes


def _coverage_weight(
    candidate: v5_planner.CandidateNode,
    interpretation_meta: Mapping[str, Any],
) -> float:
    work_policy = interpretation_meta.get("work_policy", {})
    copies_by_work = interpretation_meta.get("copies_by_work", {})
    weight = 0.0
    for key in candidate.coverage_keys:
        work_id = str(key).split("#", 1)[0]
        policy = work_policy.get(work_id, {}) if isinstance(work_policy, Mapping) else {}
        copies = max(1, int(copies_by_work.get(work_id, 1)))
        importance = float(policy.get("importance", 0.5))
        error_cost = float(policy.get("error_cost", 0.5))
        weight += (0.58 * importance + 0.42 * error_cost) / copies
    return max(0.05, weight)


def optimize_execution_graph(
    candidate_bundle: Mapping[str, Any],
    *,
    limits: GraphLimits | None = None,
    quality_tolerance_pct: float = 2.0,
    solver_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Maximize coverage-weighted, failure-adjusted utility per worst-case dollar."""
    del quality_tolerance_pct
    limits = limits or GraphLimits()
    candidates = v5_planner._candidate_objects(candidate_bundle)
    interpretations = dict(candidate_bundle.get("interpretations", {}))
    if not candidates or not interpretations:
        raise v5_planner.V5PlanningError("Candidate bundle is empty.")

    model = cp_model.CpModel()
    y = {
        interpretation_id: model.NewBoolVar(f"interpretation_{index}")
        for index, interpretation_id in enumerate(sorted(interpretations))
    }
    x = [model.NewBoolVar(f"candidate_{index}") for index in range(len(candidates))]
    model.Add(sum(y.values()) == 1)
    for index, candidate in enumerate(candidates):
        model.Add(x[index] <= y[candidate.interpretation_id])

    for interpretation_id, meta in interpretations.items():
        coverage_keys = [
            f"{work_id}#{copy_index}"
            for work_id, copies in meta["copies_by_work"].items()
            for copy_index in range(int(copies))
        ]
        for key in coverage_keys:
            terms = [
                x[index]
                for index, candidate in enumerate(candidates)
                if candidate.interpretation_id == interpretation_id
                and key in candidate.coverage_keys
            ]
            if not terms:
                model.Add(y[interpretation_id] == 0)
            else:
                model.Add(sum(terms) == y[interpretation_id])

    model.Add(sum(x) <= limits.max_nodes)
    cost_terms = [
        int(round(candidate.estimated_cost * COST_SCALE)) * x[index]
        for index, candidate in enumerate(candidates)
    ]
    worst_case_cost = sum(cost_terms)
    if limits.max_budget_usd is not None:
        model.Add(
            worst_case_cost
            <= int(round(float(limits.max_budget_usd) * COST_SCALE))
        )

    for interpretation_id, meta in interpretations.items():
        for work_id, copies in meta["copies_by_work"].items():
            if int(copies) < 2:
                continue
            copy_candidates: dict[int, list[int]] = {}
            for copy_index in range(int(copies)):
                key = f"{work_id}#{copy_index}"
                copy_candidates[copy_index] = [
                    index
                    for index, candidate in enumerate(candidates)
                    if candidate.interpretation_id == interpretation_id
                    and key in candidate.coverage_keys
                ]
            for left_copy in range(int(copies)):
                for right_copy in range(left_copy + 1, int(copies)):
                    for left in copy_candidates[left_copy]:
                        for right in copy_candidates[right_copy]:
                            if (
                                candidates[left].model == candidates[right].model
                                or candidates[left].provider_endpoint
                                == candidates[right].provider_endpoint
                            ):
                                model.Add(x[left] + x[right] <= 1)

    quality_terms = []
    for index, candidate in enumerate(candidates):
        meta = interpretations[candidate.interpretation_id]
        coverage = _coverage_weight(candidate, meta)
        score = (
            candidate.estimated_quality
            * (1.0 - 0.65 * candidate.failure_probability)
            - 0.15 * candidate.quality_uncertainty
        )
        quality_terms.append(
            int(round(max(0.0, score) * coverage * QUALITY_SCALE)) * x[index]
        )
    for interpretation_id, variable in y.items():
        interpretation_score = float(
            interpretations[interpretation_id]
            .get("metrics", {})
            .get("interpretation_score", 0.5)
        )
        quality_terms.append(
            int(round(interpretation_score * QUALITY_SCALE * 0.15)) * variable
        )
    quality_expr = sum(quality_terms)
    effective_cost = worst_case_cost + sum(x) * max(
        1, int(round(CALL_OVERHEAD_USD * COST_SCALE))
    )
    solver, status, phase_status = v5_value_optimizer._solve_cost_performance(
        model,
        quality_expr,
        effective_cost,
        solver_timeout_seconds,
    )

    selected_indices = [
        index for index, variable in enumerate(x) if solver.Value(variable)
    ]
    selected_interpretations = [
        interpretation_id
        for interpretation_id, variable in y.items()
        if solver.Value(variable)
    ]
    if len(selected_interpretations) != 1:
        raise v5_planner.V5PlanningError(
            "Solver did not select exactly one interpretation."
        )
    selected_interpretation = selected_interpretations[0]
    normalized_quality = v5_planner._clamp(
        sum(
            candidates[index].estimated_quality
            * _coverage_weight(
                candidates[index],
                interpretations[selected_interpretation],
            )
            for index in selected_indices
        )
        / max(
            0.001,
            sum(
                _coverage_weight(
                    candidates[index],
                    interpretations[selected_interpretation],
                )
                for index in selected_indices
            ),
        )
    )
    graph = v5_planner._selected_graph(
        candidates,
        selected_indices,
        candidate_bundle,
        selected_interpretation,
        normalized_quality,
        normalized_quality,
        limits,
    )
    graph_data = graph.to_dict()

    selected = [candidates[index] for index in selected_indices]
    node_coverage = {
        candidate.candidate_id: list(candidate.coverage_keys)
        for candidate in selected
    }
    selected_ids = {candidate.candidate_id for candidate in selected}
    recovery_pool: dict[str, list[dict[str, Any]]] = {}
    for chosen in selected:
        alternatives = [
            row
            for row in candidates
            if row.interpretation_id == selected_interpretation
            and row.coverage_keys == chosen.coverage_keys
            and row.candidate_id not in selected_ids
            and row.provider_endpoint != chosen.provider_endpoint
        ]
        alternatives.sort(
            key=lambda row: (
                row.model != chosen.model,
                row.failure_probability,
                row.estimated_cost,
                -row.estimated_quality,
                row.candidate_id,
            )
        )
        recovery_pool[chosen.candidate_id] = [
            row.to_dict()
            for row in alternatives[: max(2, limits.max_replacements + 1)]
        ]

    graph_data.setdefault("metadata", {}).update({
        "highest_principle": "maximum_cost_performance",
        "objective_order": [
            "hard_constraints",
            "minimum_worst_case_cost",
            "maximum_coverage_weighted_cost_performance",
        ],
        "cost_basis": "worst-case-10000-token-provider-endpoint-envelope",
        "output_allowance_tokens": OUTPUT_ALLOWANCE_TOKENS,
        "minimum_usable_work_coverage": MINIMUM_USABLE_WORK_COVERAGE,
        "node_coverage_keys": node_coverage,
        "work_policy": interpretations[selected_interpretation].get(
            "work_policy", {}
        ),
        "recovery_pool": recovery_pool,
        "degraded_synthesis_allowed": True,
    })
    graph_data["estimated_total_cost"] = round(
        sum(candidate.estimated_cost for candidate in selected),
        8,
    )
    selected_quality = max(0, int(solver.Value(quality_expr)))
    selected_effective_cost = max(1, int(solver.Value(effective_cost)))
    return {
        "version": 5,
        "optimizer": "google-or-tools-cp-sat",
        "selection_method": "coverage-weighted-production-cost-performance-v5",
        "solver_status": solver.StatusName(status),
        "phase_status": phase_status,
        "selected_interpretation": selected_interpretation,
        "highest_principle": "maximum_cost_performance",
        "objective_order": [
            "hard_constraints",
            "minimum_worst_case_cost",
            "maximum_coverage_weighted_cost_performance",
        ],
        "selected_quality_objective_scaled": selected_quality,
        "selected_effective_cost_scaled": selected_effective_cost,
        "cost_performance_ratio": round(
            selected_quality / selected_effective_cost,
            9,
        ),
        "selected_candidate_ids": [
            candidates[index].candidate_id for index in selected_indices
        ],
        "execution_graph": graph_data,
        "fallback_used": False,
    }


def compile_and_optimize_v5(
    ranked: Sequence[Any],
    resource_bundle: Mapping[str, Any],
    *,
    endpoint_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    allow_synthetic_fixture: bool = False,
    ranking_limit: int = 50,
    limits: GraphLimits | None = None,
    maximum_per_group: int = 12,
    quality_tolerance_pct: float = 2.0,
    solver_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    del quality_tolerance_pct
    sanitized = _sanitize_endpoint_payloads(endpoint_payloads)
    market = v5_value_optimizer.compile_model_endpoint_market(
        ranked,
        resource_bundle,
        endpoint_payloads=sanitized,
        ranking_limit=ranking_limit,
        allow_synthetic_fixture=allow_synthetic_fixture,
    )
    endpoints = [
        dict(row)
        for row in market.get("endpoints", [])
        if isinstance(row, Mapping)
        and float(row.get("reliability", 0.0))
        >= MINIMUM_ENDPOINT_RELIABILITY
        and int(row.get("max_completion_tokens", 0) or 0)
        >= OUTPUT_ALLOWANCE_TOKENS
    ]
    if not endpoints:
        raise v5_planner.V5PlanningError(
            "No provider endpoint meets the production reliability and 10k-output allowance floor."
        )
    market = dict(market)
    market["endpoints"] = endpoints
    market["endpoint_count"] = len(endpoints)
    market["real_endpoint_count"] = sum(
        not bool(row.get("synthetic_fixture_only")) for row in endpoints
    )
    market["production_endpoint_policy"] = {
        "minimum_reliability": MINIMUM_ENDPOINT_RELIABILITY,
        "minimum_critical_reliability": MINIMUM_CRITICAL_ENDPOINT_RELIABILITY,
        "missing_reliability_is_rejected": True,
        "minimum_max_completion_tokens": OUTPUT_ALLOWANCE_TOKENS,
        "strict_provider_endpoint": True,
    }

    candidate_bundle, _, envelopes = _prepare_candidate_bundle(
        resource_bundle,
        market,
        maximum_per_group=maximum_per_group,
    )
    optimization = optimize_execution_graph(
        candidate_bundle,
        limits=limits,
        solver_timeout_seconds=solver_timeout_seconds,
    )
    selected = set(optimization.get("selected_candidate_ids", []))
    optimization["cost_envelopes"] = {
        candidate_id: envelope
        for candidate_id, envelope in envelopes.items()
        if candidate_id in selected
    }
    optimization["execution_graph"].setdefault("metadata", {})[
        "cost_envelopes"
    ] = optimization["cost_envelopes"]
    return {
        "version": 5,
        "market": market,
        "candidate_graph": candidate_bundle,
        "optimization": optimization,
    }


def _system_prompt(node: SelectedNode) -> str:
    modules = list(node.prompt_profile.get("modules", []))
    rules = "".join(
        v5_executor.PROMPT_MODULES.get(
            str(name), f"执行提示模块：{name}。"
        )
        for name in modules
    )
    functions = "、".join(node.functions)
    required = [
        str(value)
        for value in node.output_contract.get("required_fields", [])
        if str(value)
    ]
    if node.output_contract.get("machine_readable_required"):
        delivery = (
            "响应由严格JSON Schema约束。只填写各字段的实际内容；"
            "每个字段必须是至少含一项的字符串数组。不得输出Markdown、前后缀、契约元数据或额外键。"
            "先完成全部键和闭合结构，再补充细节。"
        )
    else:
        delivery = (
            f"直接交付以下内容：{'、'.join(required)}。"
            "不得复述字段定义；优先给出可验证结论、关键假设、风险和行动。"
        )
    return (
        "你是V5动态专家执行图中的一个严格隔离节点。"
        f"本节点功能：{functions}。负责原子工作：{', '.join(node.assigned_work)}。"
        "禁止调用、请求或假装使用网页、搜索、插件、文件、代码执行、数据库、API、浏览器、工具或其他模型。"
        "只能依据原始任务和系统显式传入的上游节点结果。不得读取未声明节点，不得与同独立组节点交换结果。"
        f"{rules}{delivery}"
        "不要展示隐藏思维过程。"
    )


def build_node_payload(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    upstream_text = "\n\n".join(
        f"### 上游节点 {row.get('node_id')}\n{row.get('answer')}"
        for row in upstream
        if row.get("answer")
    ) or "[无可用上游结果；请在明确缺口的前提下独立完成本节点。]"
    payload: dict[str, Any] = {
        "model": node.model,
        "messages": [
            {"role": "system", "content": _system_prompt(node)},
            {
                "role": "user",
                "content": (
                    f"原始任务：\n{original_task}\n\n"
                    f"本节点工作ID：{', '.join(node.assigned_work)}\n\n"
                    f"可用上游结果：\n{upstream_text}"
                ),
            },
        ],
        "stream": False,
    }
    payload.update(_json_copy(dict(node.request_config)))
    forbidden = sorted(v5_executor.FORBIDDEN_FIELDS.intersection(payload))
    if forbidden:
        raise v5_executor.V5ExecutionError(
            f"Forbidden request fields for node {node.node_id}: {forbidden}"
        )
    model = str(payload.get("model") or "").casefold()
    if model.startswith("openrouter/") or ":online" in model or ":batch" in model:
        raise v5_executor.V5ExecutionError(
            f"Forbidden routed model for node {node.node_id}: {payload.get('model')}"
        )
    maximum = int(payload.get("max_tokens", 0) or 0)
    if maximum != OUTPUT_ALLOWANCE_TOKENS:
        raise v5_executor.V5ExecutionError(
            f"Node {node.node_id} must send exactly the audited "
            f"{OUTPUT_ALLOWANCE_TOKENS}-token maximum allowance."
        )
    return payload


def _install_quality_gate() -> None:
    global _ORIGINAL_QUALITY_GATE
    if _ORIGINAL_QUALITY_GATE is not None:
        return
    _ORIGINAL_QUALITY_GATE = v5_executor.quality_gate

    def hardened_quality_gate(
        node: SelectedNode,
        response: Mapping[str, Any],
        answer: str,
    ) -> tuple[bool, float, list[str]]:
        assert _ORIGINAL_QUALITY_GATE is not None
        passed, score, reasons = _ORIGINAL_QUALITY_GATE(
            node, response, answer
        )
        if not node.output_contract.get("machine_readable_required"):
            return passed, score, reasons
        try:
            parsed = json.loads(answer)
        except json.JSONDecodeError:
            return passed, score, reasons
        if not isinstance(parsed, Mapping):
            return passed, score, reasons
        empty = [
            str(field)
            for field in node.output_contract.get("required_fields", [])
            if field not in parsed
            or not isinstance(parsed.get(field), list)
            or not parsed.get(field)
            or any(
                not isinstance(item, str) or not item.strip()
                for item in parsed.get(field, [])
            )
        ]
        if empty:
            reason = "empty-or-invalid-required-json-fields:" + ",".join(empty)
            if reason not in reasons:
                reasons.append(reason)
            return False, min(float(score), 0.35), reasons
        return passed, score, reasons

    v5_executor.quality_gate = hardened_quality_gate


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_quality_gate()
    v5_executor.build_node_payload = build_node_payload
    _INSTALLED = True


def _successful(result: Any) -> bool:
    return str(result.status).startswith("success") and bool(result.answer)


def _coverage_summary(
    graph: ExecutionGraph,
    outputs: Mapping[str, Any],
) -> dict[str, Any]:
    metadata = graph.metadata if isinstance(graph.metadata, Mapping) else {}
    node_coverage = metadata.get("node_coverage_keys", {})
    work_policy = metadata.get("work_policy", {})
    successful_keys: set[str] = set()
    for node_id, result in outputs.items():
        if not _successful(result):
            continue
        keys = (
            node_coverage.get(node_id, [])
            if isinstance(node_coverage, Mapping)
            else []
        )
        if not keys:
            keys = [
                f"{work_id}#0"
                for work_id in getattr(result, "assigned_work", ())
            ]
        successful_keys.update(str(key) for key in keys)

    covered_work = {
        key.split("#", 1)[0]
        for key in successful_keys
    }
    required = set(graph.required_work)
    non_synthesis = {
        work_id
        for work_id in required
        if not bool(
            work_policy.get(work_id, {}).get("synthesis")
            if isinstance(work_policy, Mapping)
            else False
        )
    }
    critical = {
        work_id
        for work_id in non_synthesis
        if bool(
            work_policy.get(work_id, {}).get("critical")
            if isinstance(work_policy, Mapping)
            else False
        )
    }
    covered_non_synthesis = covered_work.intersection(non_synthesis)
    ratio = len(covered_non_synthesis) / max(1, len(non_synthesis))
    return {
        "required_work": sorted(required),
        "covered_work": sorted(covered_work),
        "covered_non_synthesis_work": sorted(covered_non_synthesis),
        "missing_work": sorted(required - covered_work),
        "critical_work": sorted(critical),
        "missing_critical_work": sorted(critical - covered_work),
        "non_synthesis_coverage_ratio": round(ratio, 6),
    }


def _degraded_answer(
    graph: ExecutionGraph,
    outputs: Mapping[str, Any],
    coverage: Mapping[str, Any],
) -> str:
    nodes = {node.node_id: node for node in graph.nodes}
    rows = [
        result
        for result in outputs.values()
        if _successful(result)
    ]
    priority = {
        "synthesis": 0,
        "decision_comparison": 1,
        "implementation": 2,
        "adversarial_reasoning": 3,
        "quantitative_modeling": 4,
        "evidence_validation": 5,
        "analysis": 6,
    }

    def order_key(result: Any) -> tuple[int, float, str]:
        functions = set(nodes[result.node_id].functions)
        rank = min(
            (priority.get(function, 9) for function in functions),
            default=9,
        )
        return rank, -float(result.quality_score), result.node_id

    rows.sort(key=order_key)
    if not rows:
        return ""
    parts = [
        "# V5降级合成结果",
        "",
        "以下内容由已通过质量门的节点确定性合并；未成功节点未参与合成。",
        "",
    ]
    for index, result in enumerate(rows, 1):
        functions = "、".join(nodes[result.node_id].functions)
        parts.extend([
            f"## 通过节点{index}：{functions}",
            "",
            str(result.answer).strip(),
            "",
        ])
    missing = list(coverage.get("missing_work", []))
    if missing:
        parts.extend([
            "## 明确缺口",
            "",
            "未形成合格节点结果的工作项：" + "、".join(missing),
            "",
            "这些缺口不得被视为已经验证；最终使用时应降低相应结论置信度。",
        ])
    return "\n".join(parts).strip()


def execute_v5_graph(
    graph: ExecutionGraph | Mapping[str, Any],
    run: Any,
    original_task: str,
    *,
    call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]] | None = None,
    output_dir: str | Path | None = None,
    limits: GraphLimits | None = None,
) -> dict[str, Any]:
    """Execute every reachable stage and degrade by work quorum, not node unanimity."""
    install()
    graph = graph if isinstance(graph, ExecutionGraph) else ExecutionGraph.from_mapping(graph)
    limits = limits or GraphLimits()
    issues = validate_execution_graph(graph, limits)
    if issues:
        raise v5_executor.V5ExecutionError(
            "Invalid execution graph: "
            + "; ".join(f"{item.code}:{item.message}" for item in issues)
        )
    if (
        limits.max_budget_usd is not None
        and graph.estimated_total_cost
        > float(limits.max_budget_usd) + 1e-12
    ):
        raise v5_executor.V5ExecutionError(
            "V5 graph rejected before inference: worst-case provider-endpoint "
            f"cost {graph.estimated_total_cost:.6f} USD exceeds "
            f"budget {float(limits.max_budget_usd):.6f} USD."
        )

    call = call_fn or v5_executor._default_call
    node_by_id = {node.node_id: node for node in graph.nodes}
    incoming: dict[str, list[str]] = {
        node.node_id: [] for node in graph.nodes
    }
    for edge in graph.edges:
        incoming[edge.target].append(edge.source)
    budget = v5_executor.ExecutionBudget(
        max_planned_calls=limits.max_model_calls,
        max_retries=limits.max_retries,
        max_replacements=limits.max_replacements,
        max_budget_usd=limits.max_budget_usd,
    )
    recovery = (
        graph.metadata.get("recovery_pool", {})
        if isinstance(graph.metadata, Mapping)
        else {}
    )
    outputs: dict[str, Any] = {}
    stage_records: list[dict[str, Any]] = []
    started = time.monotonic()

    for stage_index, stage in enumerate(graph.execution_stages):
        futures = {}
        workers = min(
            max(
                1,
                int(
                    getattr(
                        run,
                        "parallel_workers",
                        len(stage) or 1,
                    )
                ),
            ),
            len(stage),
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for node_id in stage:
                upstream = [
                    {
                        "node_id": source,
                        "answer": outputs[source].answer,
                        "quality_score": outputs[source].quality_score,
                    }
                    for source in incoming[node_id]
                    if source in outputs and _successful(outputs[source])
                ]
                futures[
                    pool.submit(
                        v5_executor._execute_node,
                        node_by_id[node_id],
                        original_task,
                        upstream,
                        run,
                        call,
                        list(recovery.get(node_id, []))
                        if isinstance(recovery, Mapping)
                        else [],
                        budget,
                    )
                ] = node_id
            stage_results = [
                future.result() for future in as_completed(futures)
            ]
        stage_results.sort(key=lambda row: row.node_id)
        for result in stage_results:
            outputs[result.node_id] = result
        failed = [
            result.node_id
            for result in stage_results
            if not _successful(result)
        ]
        stage_records.append({
            "stage_index": stage_index,
            "node_ids": list(stage),
            "failed_node_ids": failed,
            "status": "degraded" if failed else "success",
        })

    coverage = _coverage_summary(graph, outputs)
    successful_finals = [
        outputs[node_id]
        for node_id in graph.final_nodes
        if node_id in outputs and _successful(outputs[node_id])
    ]
    final_answer = "\n\n".join(
        result.answer or "" for result in successful_finals
    ).strip()
    fallback_used = False
    if not final_answer:
        final_answer = _degraded_answer(graph, outputs, coverage)
        fallback_used = bool(final_answer)

    minimum_coverage = float(
        graph.metadata.get(
            "minimum_usable_work_coverage",
            MINIMUM_USABLE_WORK_COVERAGE,
        )
        if isinstance(graph.metadata, Mapping)
        else MINIMUM_USABLE_WORK_COVERAGE
    )
    missing_critical = list(coverage["missing_critical_work"])
    usable = bool(
        final_answer
        and not missing_critical
        and float(coverage["non_synthesis_coverage_ratio"])
        + 1e-12
        >= minimum_coverage
    )
    all_nodes_success = (
        len(outputs) == len(graph.nodes)
        and all(_successful(result) for result in outputs.values())
    )
    completion_mode = (
        "full"
        if usable and all_nodes_success and successful_finals
        else "degraded"
        if usable
        else "partial"
        if final_answer
        else "failed"
    )
    budget_snapshot = budget.snapshot()
    result = {
        "version": 5,
        "status": "success" if usable else (
            "partial_success" if final_answer else "failed"
        ),
        "completion_mode": completion_mode,
        "degraded": completion_mode == "degraded",
        "deterministic_fallback_used": fallback_used,
        "execution_stages": stage_records,
        "node_results": [
            asdict(outputs[node_id]) for node_id in sorted(outputs)
        ],
        "final_node_ids": list(graph.final_nodes),
        "final_answer": final_answer or None,
        "work_coverage": coverage,
        "actual_cost_usd": round(
            sum(row.actual_cost_usd for row in outputs.values()),
            8,
        ),
        "recovery_used": any(
            attempt.replacement or attempt.retry
            for row in outputs.values()
            for attempt in row.attempts
        ),
        "execution_budget": budget_snapshot,
        "latency_seconds": round(time.monotonic() - started, 6),
        "stop_reason": (
            "all-nodes-and-quality-gates-passed"
            if completion_mode == "full"
            else "work-quorum-satisfied-with-audited-degradation"
            if completion_mode == "degraded"
            else "critical-work-or-coverage-quorum-not-satisfied"
        ),
        "degradation_reasons": {
            "failed_node_ids": sorted(
                node_id
                for node_id, row in outputs.items()
                if not _successful(row)
            ),
            "missing_work": coverage["missing_work"],
            "missing_critical_work": missing_critical,
        },
    }

    if output_dir is not None:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        (root / "v5-node-results.json").write_text(
            json.dumps(
                result["node_results"],
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        (root / "v5-execution-summary.json").write_text(
            json.dumps(
                {
                    key: value
                    for key, value in result.items()
                    if key != "node_results"
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        requests = [
            attempt.request
            for row in outputs.values()
            for attempt in row.attempts
        ]
        audit = {
            "status": "PASS"
            if all(
                not v5_executor.FORBIDDEN_FIELDS.intersection(request)
                and int(request.get("max_tokens", 0) or 0)
                == OUTPUT_ALLOWANCE_TOKENS
                for request in requests
            )
            else "FAIL",
            "request_count": len(requests),
            "requests": requests,
            "maximum_output_allowance_tokens": OUTPUT_ALLOWANCE_TOKENS,
            "artificial_low_token_ceiling_sent": False,
            "strict_json_schema_request_count": sum(
                request.get("response_format", {}).get("type")
                == "json_schema"
                for request in requests
                if isinstance(request.get("response_format"), Mapping)
            ),
            "external_tools_allowed": False,
            "global_limits": budget_snapshot,
        }
        (root / "v5-request-audit.json").write_text(
            json.dumps(
                audit,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        (root / "v5-final-report.md").write_text(
            final_answer or "# V5 execution failed\n",
            encoding="utf-8",
        )

    if not usable:
        raise v5_executor.V5ExecutionError(
            "V5 execution did not satisfy critical-work and minimum-coverage "
            "quorums; partial artifacts were preserved."
        )
    return result
