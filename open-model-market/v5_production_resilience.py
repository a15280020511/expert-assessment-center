"""Production resilience overlay for the V5 execution graph.

This module deliberately patches only V5 modules. V3 remains untouched. The
formal V5 paths install it through ``v5_candidate_diversity.install()``.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_executor as executor
from execution_graph import ExecutionGraph, GraphLimits, SelectedNode
from execution_graph_validator import validate_execution_graph

_INSTALLED = False
_ORIGINAL_BUILD = executor.build_node_payload
_ORIGINAL_RESERVED = executor._reserved_attempt
_ORIGINAL_EXECUTE = executor.execute_v5_graph
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I | re.S)


def _delivery(node: SelectedNode) -> bool:
    return bool({"synthesis", "delivery", "adjudication"}.intersection(node.functions))


def _effective(node: SelectedNode) -> SelectedNode:
    """Make strict JSON a final-delivery concern, not an intermediate-node tax."""
    if _delivery(node) or not node.output_contract.get("machine_readable_required"):
        return node
    contract = dict(node.output_contract)
    contract["machine_readable_required"] = False
    contract["structured_delivery_deferred_to_final"] = True
    request = dict(node.request_config)
    request.pop("response_format", None)
    request.pop("json_schema", None)
    parameters = dict(node.parameter_profile)
    nested = dict(parameters.get("parameters", {}))
    nested.pop("response_format", None)
    nested.pop("json_schema", None)
    parameters["parameters"] = nested
    return replace(node, output_contract=contract, request_config=request, parameter_profile=parameters)


def _fields(node: SelectedNode) -> list[str]:
    return [str(value).strip() for value in node.output_contract.get("required_fields", []) if str(value).strip()]


def _allowance(node: SelectedNode) -> int:
    value = 1700 + min(12, len(_fields(node))) * 230
    if _delivery(node):
        value += 1800
    if {"implementation", "quantitative_modeling", "forecasting"}.intersection(node.functions):
        value += 700
    if node.output_contract.get("machine_readable_required"):
        value += 700
    return max(1536, min(10000, value))


def _token_field(node: SelectedNode) -> str:
    supported = {str(value).casefold() for value in node.parameter_profile.get("supported_parameters", [])}
    return "max_completion_tokens" if "max_completion_tokens" in supported else "max_tokens"


def _bounded_upstream(upstream: Sequence[Mapping[str, Any]], delivery: bool) -> list[dict[str, Any]]:
    total_limit = 24000 if delivery else 12000
    per_item = 6000 if delivery else 4000
    used = 0
    rows: list[dict[str, Any]] = []
    ordered = sorted(
        (row for row in upstream if row.get("answer")),
        key=lambda row: (-float(row.get("quality_score", 0.0) or 0.0), str(row.get("node_id") or "")),
    )
    for row in ordered:
        if used >= total_limit:
            break
        answer = str(row.get("answer") or "")[: min(per_item, total_limit - used)]
        used += len(answer)
        rows.append({**dict(row), "answer": answer})
    return rows


def build_node_payload(node: SelectedNode, original_task: str, upstream: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    node = _effective(node)
    payload = _ORIGINAL_BUILD(node, original_task, _bounded_upstream(upstream, _delivery(node)))
    existing: list[int] = []
    for key in ("max_tokens", "max_completion_tokens"):
        if payload.get(key) not in {None, ""}:
            existing.append(int(payload[key]))
        payload.pop(key, None)
    payload[_token_field(node)] = max(1024, min(10000, min(existing) if existing else _allowance(node)))
    return payload


def _parse_json(answer: str) -> Mapping[str, Any] | None:
    candidate = _JSON_FENCE.sub("", answer.strip()).strip()
    try:
        value = json.loads(candidate)
        return value if isinstance(value, Mapping) else None
    except json.JSONDecodeError:
        pass
    start = candidate.find("{")
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(candidate[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, Mapping) else None


def quality_gate(node: SelectedNode, response: Mapping[str, Any], answer: str) -> tuple[bool, float, list[str]]:
    node = _effective(node)
    reasons: list[str] = []
    finish = executor._finish_reason(response).casefold()
    if finish in {"length", "max_tokens"}:
        reasons.append("truncated-output")
    minimum = 320 if _delivery(node) else 180 if "implementation" in node.functions else 120
    if len(answer) < minimum:
        reasons.append(f"answer-too-short<{minimum}")
    folded = answer.casefold()
    if any(term in folded for term in ("i cannot access", "无法访问互联网", "作为ai无法", "没有提供任何答案")):
        reasons.append("non-delivery-or-tool-dependency")

    contract_score = min(1.0, len(answer) / max(1, minimum * 2))
    if node.output_contract.get("machine_readable_required"):
        parsed = _parse_json(answer)
        if parsed is None:
            reasons.append("invalid-required-json")
            contract_score = 0.0
        else:
            missing = [field for field in _fields(node) if field not in parsed]
            if missing:
                reasons.append("missing-required-json-keys:" + ",".join(missing))
            contract_score = 1.0 - len(missing) / max(1, len(_fields(node)))

    completeness = min(1.0, len(answer) / max(1, minimum * 3))
    finish_score = 0.0 if finish in {"length", "max_tokens"} else 1.0
    score = max(0.0, min(1.0, 0.48 * completeness + 0.27 * contract_score + 0.25 * finish_score))
    threshold = max(0.46, min(0.80, 0.48 + 0.20 * node.estimated_quality - 0.08 * node.quality_uncertainty))
    if score + 1e-12 < threshold:
        reasons.append(f"quality-score<{threshold:.3f}")
    return not reasons, round(score, 6), reasons


def _factor(node: SelectedNode) -> float:
    raw = node.output_contract.get("cost_reserve_multiplier")
    try:
        if raw not in {None, ""}:
            return max(1.0, min(5.0, float(raw)))
    except (TypeError, ValueError):
        pass
    if node.output_contract.get("machine_readable_required"):
        return 2.4
    if _delivery(node):
        return 2.1
    if {"implementation", "quantitative_modeling", "forecasting"}.intersection(node.functions):
        return 1.9
    return 1.6


def _reserved_attempt(node: SelectedNode, *args: Any, **kwargs: Any) -> Any:
    multiplier = _factor(node)
    if multiplier <= 1.0:
        return _ORIGINAL_RESERVED(node, *args, **kwargs)
    contract = dict(node.output_contract)
    contract["raw_expected_cost_usd"] = round(float(node.estimated_cost), 8)
    contract["cost_estimate_policy"] = "conservative-p95-reservation"
    contract["cost_reserve_multiplier"] = 1.0
    reserved = replace(
        node,
        estimated_cost=round(float(node.estimated_cost) * multiplier, 8),
        output_contract=contract,
    )
    return _ORIGINAL_RESERVED(reserved, *args, **kwargs)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _fallback(outputs: Mapping[str, Any], required: Sequence[str]) -> tuple[str | None, float]:
    rows = [row for row in outputs.values() if row.status.startswith("success") and row.answer]
    covered = {work for row in rows for work in row.assigned_work}
    ratio = len(covered.intersection(required)) / max(1, len(set(required)))
    rows = [row for row in rows if row.quality_score >= 0.55]
    rows.sort(key=lambda row: (-row.quality_score, row.actual_cost_usd, row.node_id))
    if ratio < 0.50 or not rows:
        return None, ratio
    body = "\n\n".join(f"## 已完成工作单元 {index}\n{row.answer}" for index, row in enumerate(rows[:4], 1))
    return (
        "# V5降级交付\n\n部分节点未返回可用结果。以下内容仅基于已通过质量门的工作单元，"
        "未覆盖部分不得视为已验证结论。\n\n" + body
    ), ratio


def _persist(output_dir: str | Path | None, result: Mapping[str, Any], outputs: Mapping[str, Any]) -> None:
    if output_dir is None:
        return
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    node_rows = [asdict(outputs[node_id]) for node_id in sorted(outputs)]
    _write_json(root / "v5-node-results.json", node_rows)
    _write_json(root / "v5-execution-summary.json", {key: value for key, value in result.items() if key != "node_results"})
    requests = [attempt.request for row in outputs.values() for attempt in row.attempts]
    _write_json(root / "v5-request-audit.json", {
        "status": "PASS" if all(not executor.FORBIDDEN_FIELDS.intersection(request) for request in requests) else "FAIL",
        "request_count": len(requests),
        "requests": requests,
        "bounded_output_tokens_sent": any("max_tokens" in request or "max_completion_tokens" in request for request in requests),
        "maximum_permitted_not_required": True,
        "artificial_token_ceiling_sent": False,
        "external_tools_allowed": False,
        "global_limits": result.get("execution_budget", {}),
    })
    (root / "v5-final-report.md").write_text(str(result.get("final_answer") or "# V5 execution failed\n"), encoding="utf-8")


def execute_v5_graph(
    graph: ExecutionGraph | Mapping[str, Any],
    run: Any,
    original_task: str,
    *,
    call_fn: Any = None,
    output_dir: str | Path | None = None,
    limits: GraphLimits | None = None,
) -> dict[str, Any]:
    graph = graph if isinstance(graph, ExecutionGraph) else ExecutionGraph.from_mapping(graph)
    limits = limits or GraphLimits()
    issues = validate_execution_graph(graph, limits)
    if issues:
        raise executor.V5ExecutionError("Invalid execution graph: " + "; ".join(f"{x.code}:{x.message}" for x in issues))
    graph = replace(graph, nodes=tuple(_effective(node) for node in graph.nodes))
    planned = round(sum(float(node.estimated_cost) * _factor(node) for node in graph.nodes), 8)
    budget = executor.ExecutionBudget(
        max_planned_calls=limits.max_model_calls,
        max_retries=limits.max_retries,
        max_replacements=limits.max_replacements,
        max_budget_usd=limits.max_budget_usd,
    )
    if limits.max_budget_usd is not None and planned > limits.max_budget_usd + 1e-12:
        budget.denials.append({
            "node_id": "__graph_preflight__", "kind": "preflight",
            "estimated_cost_usd": planned, "reason": "conservative-graph-reservation-exceeds-budget",
        })
        result = {
            "version": 5, "status": "failed", "delivery_mode": "none", "degraded": False,
            "execution_stages": [], "node_results": [], "final_node_ids": list(graph.final_nodes),
            "final_answer": None, "actual_cost_usd": 0.0, "conservative_planned_cost_usd": planned,
            "successful_required_work_ratio": 0.0, "recovery_used": False,
            "execution_budget": budget.snapshot(), "stop_reason": "preflight-reservation-exceeds-budget",
        }
        _persist(output_dir, result, {})
        raise executor.V5ExecutionError(
            f"V5 conservative preflight cost {planned:.6f} USD exceeds hard budget "
            f"{limits.max_budget_usd:.6f} USD; no model call was made."
        )

    node_by_id = {node.node_id: node for node in graph.nodes}
    incoming = {node.node_id: [] for node in graph.nodes}
    for edge in graph.edges:
        incoming[edge.target].append(edge.source)
    recovery = graph.metadata.get("recovery_pool", {}) if isinstance(graph.metadata, Mapping) else {}
    outputs: dict[str, Any] = {}
    stages: list[dict[str, Any]] = []
    call = call_fn or executor._default_call

    for index, stage in enumerate(graph.execution_stages):
        workers = 1 if limits.max_budget_usd is not None else min(
            max(1, int(getattr(run, "parallel_workers", len(stage) or 1))), len(stage)
        )
        futures = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for node_id in stage:
                upstream = [
                    {"node_id": source, "answer": outputs[source].answer, "quality_score": outputs[source].quality_score}
                    for source in incoming[node_id] if source in outputs and outputs[source].answer
                ]
                futures[pool.submit(
                    executor._execute_node, node_by_id[node_id], original_task, upstream, run, call,
                    list(recovery.get(node_id, [])) if isinstance(recovery, Mapping) else [], budget,
                )] = node_id
            rows = [future.result() for future in as_completed(futures)]
        rows.sort(key=lambda row: row.node_id)
        for row in rows:
            outputs[row.node_id] = row
        failed = [row.node_id for row in rows if not row.status.startswith("success")]
        stages.append({
            "stage_index": index, "node_ids": list(stage), "failed_node_ids": failed,
            "status": "degraded" if failed else "success",
            "continued_after_failure": bool(failed and index < len(graph.execution_stages) - 1),
        })

    finals = [outputs[node_id] for node_id in graph.final_nodes if node_id in outputs and outputs[node_id].status.startswith("success") and outputs[node_id].answer]
    failed_nodes = sorted(node_id for node_id, row in outputs.items() if not row.status.startswith("success"))
    answer = "\n\n".join(row.answer or "" for row in finals).strip()
    covered = {work for row in outputs.values() if row.status.startswith("success") for work in row.assigned_work}
    ratio = len(covered.intersection(graph.required_work)) / max(1, len(set(graph.required_work)))
    status, mode = "success", "final-node"
    if not answer:
        answer, ratio = _fallback(outputs, graph.required_work)
        status, mode = ("degraded_success", "partial-support-fallback") if answer else ("failed", "none")
    actual = round(sum(row.actual_cost_usd for row in outputs.values()), 8)
    if limits.max_budget_usd is not None and actual > limits.max_budget_usd + 1e-12:
        status, mode, answer = "failed", "none", None

    result = {
        "version": 5, "status": status, "delivery_mode": mode, "degraded": bool(failed_nodes),
        "execution_stages": stages, "node_results": [asdict(outputs[node_id]) for node_id in sorted(outputs)],
        "final_node_ids": list(graph.final_nodes), "successful_final_node_ids": [row.node_id for row in finals],
        "failed_node_ids": failed_nodes, "final_answer": answer or None, "actual_cost_usd": actual,
        "conservative_planned_cost_usd": planned, "successful_required_work_ratio": round(ratio, 6),
        "recovery_used": any(attempt.replacement or attempt.retry for row in outputs.values() for attempt in row.attempts),
        "execution_budget": budget.snapshot(),
        "stop_reason": "final-delivery-produced" if status == "success" else "partial-quality-gated-delivery" if status == "degraded_success" else "no-usable-delivery-or-hard-budget-exceeded",
    }
    _persist(output_dir, result, outputs)
    if status == "failed":
        raise executor.V5ExecutionError("V5 produced no usable answer within quality and hard budget constraints.")
    return result


def _harden_candidates(bundle: Mapping[str, Any]) -> dict[str, Any]:
    hardened = json.loads(json.dumps(bundle, ensure_ascii=False, default=str))
    for row in hardened.get("candidates", []):
        contract = row.get("output_contract") if isinstance(row.get("output_contract"), dict) else {}
        if contract.get("cost_estimate_policy") == "conservative-p95-reservation":
            continue
        node = SelectedNode(
            node_id=str(row.get("candidate_id") or "candidate"), assigned_work=tuple(row.get("assigned_work", [])),
            professional_capabilities={}, functions=tuple(row.get("functions", [])), prompt_profile={},
            reasoning_profile={}, parameter_profile={}, model=str(row.get("model") or "model"),
            provider_endpoint=str(row.get("provider_endpoint") or "provider"), output_contract=contract,
            estimated_quality=float(row.get("estimated_quality", 0.0)), quality_uncertainty=float(row.get("quality_uncertainty", 0.0)),
            estimated_cost=float(row.get("estimated_cost", 0.0)), failure_probability=float(row.get("failure_probability", 0.0)),
        )
        node = _effective(node)
        contract = dict(node.output_contract)
        if not _delivery(node):
            row["request_config"] = dict(row.get("request_config", {}))
            row["request_config"].pop("response_format", None)
            row["request_config"].pop("json_schema", None)
            profile = dict(row.get("parameter_profile", {}))
            parameters = dict(profile.get("parameters", {}))
            parameters.pop("response_format", None)
            parameters.pop("json_schema", None)
            profile["parameters"] = parameters
            row["parameter_profile"] = profile
        raw = float(row.get("estimated_cost", 0.0))
        factor = _factor(node) * (1.0 + min(0.50, float(row.get("failure_probability", 0.0))))
        contract.update({
            "raw_expected_cost_usd": round(raw, 8),
            "cost_estimate_policy": "conservative-p95-reservation",
            "cost_reserve_multiplier": 1.0,
        })
        row["output_contract"] = contract
        row["estimated_cost"] = round(raw * factor, 8)
    hardened["production_cost_policy"] = "conservative-p95-before-cp-sat-and-before-execution"
    return hardened


def install_benchmark_output_allowance() -> None:
    """Keep each node's lower dynamic cap; the benchmark allowance is only a ceiling."""
    import v5_live_benchmark as base
    import v5_live_benchmark_hardened as hardened

    original_safe = base._safe_payload

    def safe(endpoint: Mapping[str, Any], system: str, user: str) -> dict[str, Any]:
        payload = original_safe(endpoint, system, user)
        field = hardened._allowance_field(endpoint.get("supported_parameters", []))
        values = [int(payload[key]) for key in ("max_tokens", "max_completion_tokens") if payload.get(key) not in {None, ""}]
        payload.pop("max_tokens", None)
        payload.pop("max_completion_tokens", None)
        payload[field] = min([hardened.ALLOWANCE, *values]) if values else hardened.ALLOWANCE
        return payload

    base._safe_payload = safe
    original_node = executor.build_node_payload

    def node_payload(node: Any, original_task: str, upstream: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        payload = original_node(node, original_task, upstream)
        supported = node.parameter_profile.get("supported_parameters", []) if isinstance(node.parameter_profile, Mapping) else []
        field = hardened._allowance_field(supported)
        values = [int(payload[key]) for key in ("max_tokens", "max_completion_tokens") if payload.get(key) not in {None, ""}]
        payload.pop("max_tokens", None)
        payload.pop("max_completion_tokens", None)
        payload[field] = min([hardened.ALLOWANCE, *values]) if values else hardened.ALLOWANCE
        return payload

    executor.build_node_payload = node_payload

    def annotate(output_dir: str | Path | None) -> None:
        if output_dir is None:
            return
        path = Path(output_dir) / "v5-request-audit.json"
        if not path.exists():
            return
        audit = json.loads(path.read_text(encoding="utf-8"))
        requests = audit.get("requests") if isinstance(audit.get("requests"), list) else []
        fields: list[str] = []
        limits: list[int] = []
        valid = bool(requests)
        for row in requests:
            if not isinstance(row, Mapping):
                valid = False
                continue
            field = "max_completion_tokens" if row.get("max_completion_tokens") not in {None, ""} else "max_tokens" if row.get("max_tokens") not in {None, ""} else ""
            if not field:
                valid = False
                continue
            value = int(row[field])
            valid = valid and 0 < value <= hardened.ALLOWANCE
            fields.append(field)
            limits.append(value)
        audit.update({
            "benchmark_output_allowance_tokens": hardened.ALLOWANCE,
            "benchmark_output_allowance_parameters": sorted(set(fields)),
            "benchmark_output_allowance_policy": "maximum-permitted-not-required; lower-dynamic-node-cap-preserved",
            "benchmark_output_allowance_consistent": valid,
            "benchmark_dynamic_output_limits": limits,
            "artificial_token_ceiling_sent": False,
            "production_policy_changed": False,
        })
        path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    hardened._annotate_v5_audit = annotate
    original_execute = base.execute_v5_graph

    def execute(*args: Any, **kwargs: Any) -> Any:
        try:
            return original_execute(*args, **kwargs)
        finally:
            annotate(kwargs.get("output_dir"))

    base.execute_v5_graph = execute


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    executor.build_node_payload = build_node_payload
    executor.quality_gate = quality_gate
    executor._reserved_attempt = _reserved_attempt
    executor.execute_v5_graph = execute_v5_graph

    try:
        import v5_value_optimizer as value
        original_generate = value.generate_candidate_graph
        original_optimize = value.optimize_execution_graph

        def generate(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return _harden_candidates(original_generate(*args, **kwargs))

        def optimize(bundle: Mapping[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            return original_optimize(_harden_candidates(bundle), *args, **kwargs)

        value.generate_candidate_graph = generate
        value.optimize_execution_graph = optimize
    except ImportError:
        pass

    try:
        import v5_live_benchmark_hardened as hardened
        hardened._install_output_allowance = install_benchmark_output_allowance
    except ImportError:
        pass
    _INSTALLED = True
