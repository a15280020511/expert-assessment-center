"""NetworkX-stage V5 executor with dynamic quality gates and bounded recovery."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from execution_graph import ExecutionGraph, GraphLimits, SelectedNode
from execution_graph_validator import validate_execution_graph
from openrouter_api import CHAT_URL, OpenRouterRequestError, request_json

FORBIDDEN_FIELDS = {
    "tools", "tool_choice", "plugins", "web_search", "web_search_options",
    "file_search", "browser", "code_interpreter", "models",
}
PROMPT_MODULES: Mapping[str, str] = {
    "scope_control": "严格限定任务边界，不扩展到题目未提供的事实。",
    "uncertainty_calibration": "明确区分事实、假设、推断、不确定性与证据缺口。",
    "structured_delivery": "按输出契约组织结果，避免重复和空泛表述。",
    "evidence_discipline": "逐项检查论据是否由输入支持，不得假装联网或引用未提供资料。",
    "quantitative_rigor": "列出变量、计算关系、单位、边界与敏感性，不伪造数据。",
    "scenario_analysis": "给出情景、触发条件、时间范围和可观察指标。",
    "decision_comparison": "按同一组标准比较方案并说明权衡、排序与否决条件。",
    "adversarial_challenge": "主动寻找反例、失败路径、脆弱假设和不可接受风险。",
    "implementation_contract": "输出依赖、步骤、验收标准、故障条件和回滚方式。",
    "divergent_generation": "生成有差异的候选，不用同义改写充数。",
    "synthesis_discipline": "合并共识，保留分歧，按证据强度裁决，不以多数代替正确。",
}


class V5ExecutionError(RuntimeError):
    pass


@dataclass
class NodeAttempt:
    attempt_index: int
    candidate_id: str
    model: str
    provider_endpoint: str
    request: Mapping[str, Any]
    status: str
    answer: str | None
    quality_score: float
    gate_reasons: list[str]
    latency_seconds: float
    usage: Mapping[str, Any]
    response_id: str | None
    response_model: str | None
    response_provider: str | None
    error: str | None = None
    replacement: bool = False


@dataclass
class NodeExecutionResult:
    node_id: str
    assigned_work: tuple[str, ...]
    status: str
    selected_model: str
    resolved_model: str | None
    provider_endpoint: str
    answer: str | None
    quality_score: float
    attempts: list[NodeAttempt]
    actual_cost_usd: float


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _node_from_candidate(candidate: Mapping[str, Any], fallback: SelectedNode) -> SelectedNode:
    return SelectedNode(
        node_id=str(candidate.get("candidate_id") or fallback.node_id),
        assigned_work=tuple(str(x) for x in candidate.get("assigned_work", fallback.assigned_work)),
        professional_capabilities=dict(candidate.get("professional_capabilities", fallback.professional_capabilities)),
        functions=tuple(str(x) for x in candidate.get("functions", fallback.functions)),
        prompt_profile=dict(candidate.get("prompt_profile", fallback.prompt_profile)),
        reasoning_profile=dict(candidate.get("reasoning_profile", fallback.reasoning_profile)),
        parameter_profile=dict(candidate.get("parameter_profile", fallback.parameter_profile)),
        model=str(candidate.get("model") or fallback.model),
        provider_endpoint=str(candidate.get("provider_endpoint") or fallback.provider_endpoint),
        output_contract=dict(candidate.get("output_contract", fallback.output_contract)),
        estimated_quality=float(candidate.get("estimated_quality", fallback.estimated_quality)),
        quality_uncertainty=float(candidate.get("quality_uncertainty", fallback.quality_uncertainty)),
        estimated_cost=float(candidate.get("estimated_cost", fallback.estimated_cost)),
        failure_probability=float(candidate.get("failure_probability", fallback.failure_probability)),
        request_config=dict(candidate.get("request_config", fallback.request_config)),
        independence_group=fallback.independence_group,
    )


def _system_prompt(node: SelectedNode) -> str:
    modules = list(node.prompt_profile.get("modules", []))
    rules = "".join(PROMPT_MODULES.get(str(name), f"执行提示模块：{name}。") for name in modules)
    contract = json.dumps(dict(node.output_contract), ensure_ascii=False, sort_keys=True)
    functions = "、".join(node.functions)
    return (
        "你是V5动态专家执行图中的一个严格隔离节点。"
        f"本节点功能：{functions}。负责原子工作：{', '.join(node.assigned_work)}。"
        "禁止调用、请求或假装使用网页、搜索、插件、文件、代码执行、数据库、API、浏览器、工具或其他模型。"
        "只能依据原始任务和系统显式传入的上游节点结果。不得读取未声明节点，不得与同独立组节点交换结果。"
        f"{rules}输出契约：{contract}。"
        "输出完整可交付正文；不要展示隐藏思维过程。"
    )


def build_node_payload(node: SelectedNode, original_task: str, upstream: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    upstream_text = "\n\n".join(
        f"### 上游节点 {row.get('node_id')}\n{row.get('answer')}" for row in upstream if row.get("answer")
    ) or "[无上游结果；请独立处理。]"
    user = (
        f"原始任务：\n{original_task}\n\n"
        f"本节点工作ID：{', '.join(node.assigned_work)}\n\n"
        f"允许读取的上游结果：\n{upstream_text}"
    )
    payload: dict[str, Any] = {
        "model": node.model,
        "messages": [{"role": "system", "content": _system_prompt(node)}, {"role": "user", "content": user}],
        "stream": False,
    }
    request_config = _json_copy(dict(node.request_config))
    payload.update(request_config)
    forbidden = sorted(FORBIDDEN_FIELDS.intersection(payload))
    if forbidden:
        raise V5ExecutionError(f"Forbidden request fields for node {node.node_id}: {forbidden}")
    model = str(payload.get("model") or "").casefold()
    if model.startswith("openrouter/") or ":online" in model or ":batch" in model:
        raise V5ExecutionError(f"Forbidden routed model for node {node.node_id}: {payload.get('model')}")
    if "max_tokens" in payload or "max_completion_tokens" in payload:
        raise V5ExecutionError("Artificial output token ceilings are forbidden in V5 requests.")
    return payload


def _extract_answer(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], Mapping) else {}
    message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for row in content:
            if isinstance(row, Mapping) and isinstance(row.get("text"), str):
                parts.append(row["text"])
        return "\n".join(parts).strip()
    return ""


def _finish_reason(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        return str(choices[0].get("finish_reason") or "")
    return ""


def _actual_cost(response: Mapping[str, Any]) -> float:
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    for key in ("cost", "total_cost"):
        try:
            if usage.get(key) is not None:
                return max(0.0, float(usage[key]))
        except (TypeError, ValueError):
            pass
    return 0.0


def quality_gate(node: SelectedNode, response: Mapping[str, Any], answer: str) -> tuple[bool, float, list[str]]:
    reasons: list[str] = []
    finish = _finish_reason(response).casefold()
    if finish in {"length", "max_tokens"}:
        reasons.append("truncated-output")
    minimum_chars = 320 if "synthesis" in node.functions else 180 if "implementation" in node.functions else 120
    if len(answer) < minimum_chars:
        reasons.append(f"answer-too-short<{minimum_chars}")
    folded = answer.casefold()
    if any(term in folded for term in ("i cannot access", "无法访问互联网", "作为ai无法", "没有提供任何答案")):
        reasons.append("non-delivery-or-tool-dependency")
    contract = node.output_contract
    required_fields = [str(x) for x in contract.get("required_fields", [])]
    field_hits = sum(field.replace("_", " ").casefold() in folded or field.casefold() in folded for field in required_fields)
    if contract.get("machine_readable_required"):
        try:
            parsed = json.loads(answer)
            if not isinstance(parsed, Mapping):
                reasons.append("machine-readable-output-not-object")
        except json.JSONDecodeError:
            reasons.append("invalid-required-json")
    completeness = min(1.0, len(answer) / max(minimum_chars * 3, 1))
    contract_score = field_hits / max(1, len(required_fields))
    finish_score = 0.0 if finish in {"length", "max_tokens"} else 1.0
    score = max(0.0, min(1.0, 0.48 * completeness + 0.27 * contract_score + 0.25 * finish_score))
    threshold = max(0.48, min(0.82, 0.50 + 0.22 * node.estimated_quality - 0.10 * node.quality_uncertainty))
    if score + 1e-12 < threshold:
        reasons.append(f"quality-score<{threshold:.3f}")
    return not reasons, round(score, 6), reasons


def _default_call(run: Any, payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], float]:
    api_key = getattr(run, "api_key", None)
    if not api_key:
        raise V5ExecutionError("OPENROUTER_API_KEY is not set.")
    started = time.monotonic()
    try:
        response = request_json(
            CHAT_URL,
            api_key,
            int(getattr(run, "model_timeout_seconds", 240)),
            int(getattr(run, "model_max_retries", 0)),
            dict(payload),
        )
    except OpenRouterRequestError as exc:
        raise V5ExecutionError(str(exc)) from exc
    return response, time.monotonic() - started


def _attempt(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
    run: Any,
    call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]],
    attempt_index: int,
    *,
    replacement: bool,
) -> NodeAttempt:
    payload = build_node_payload(node, original_task, upstream)
    response: Mapping[str, Any] = {}
    latency = 0.0
    try:
        response, latency = call_fn(run, payload)
        answer = _extract_answer(response)
        passed, quality, reasons = quality_gate(node, response, answer)
        usage = dict(response.get("usage") or {}) if isinstance(response.get("usage"), Mapping) else {}
        return NodeAttempt(
            attempt_index=attempt_index,
            candidate_id=node.node_id,
            model=node.model,
            provider_endpoint=node.provider_endpoint,
            request=payload,
            status="passed" if passed else "quality_gate_failed",
            answer=answer or None,
            quality_score=quality,
            gate_reasons=reasons,
            latency_seconds=round(float(latency), 6),
            usage=usage,
            response_id=str(response.get("id") or "") or None,
            response_model=str(response.get("model") or node.model) or None,
            response_provider=str(response.get("provider") or "") or None,
            replacement=replacement,
        )
    except Exception as exc:  # noqa: BLE001 - converted into audited attempt
        return NodeAttempt(
            attempt_index=attempt_index,
            candidate_id=node.node_id,
            model=node.model,
            provider_endpoint=node.provider_endpoint,
            request=payload,
            status="call_failed",
            answer=None,
            quality_score=0.0,
            gate_reasons=["call-failed"],
            latency_seconds=round(float(latency), 6),
            usage={},
            response_id=None,
            response_model=None,
            response_provider=None,
            error=str(exc),
            replacement=replacement,
        )


def _execute_node(
    selected: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
    run: Any,
    call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]],
    recovery_rows: Sequence[Mapping[str, Any]],
    limits: GraphLimits,
) -> NodeExecutionResult:
    attempts: list[NodeAttempt] = []
    attempt_index = 0
    active = selected
    candidates = [selected] + [_node_from_candidate(row, selected) for row in recovery_rows[: limits.max_replacements]]
    for candidate_index, candidate in enumerate(candidates):
        same_endpoint_attempts = 1 + (limits.max_retries if candidate_index == 0 else 0)
        for _ in range(same_endpoint_attempts):
            attempt_index += 1
            attempt = _attempt(
                candidate, original_task, upstream, run, call_fn, attempt_index,
                replacement=candidate_index > 0,
            )
            attempts.append(attempt)
            if attempt.status == "passed":
                actual_cost = sum(_actual_cost({"usage": row.usage}) for row in attempts)
                return NodeExecutionResult(
                    node_id=selected.node_id,
                    assigned_work=selected.assigned_work,
                    status="success_recovered" if candidate_index > 0 else "success",
                    selected_model=selected.model,
                    resolved_model=attempt.response_model or candidate.model,
                    provider_endpoint=candidate.provider_endpoint,
                    answer=attempt.answer,
                    quality_score=attempt.quality_score,
                    attempts=attempts,
                    actual_cost_usd=round(actual_cost, 8),
                )
        active = candidate
    return NodeExecutionResult(
        node_id=selected.node_id,
        assigned_work=selected.assigned_work,
        status="failed",
        selected_model=selected.model,
        resolved_model=active.model,
        provider_endpoint=active.provider_endpoint,
        answer=None,
        quality_score=0.0,
        attempts=attempts,
        actual_cost_usd=round(sum(_actual_cost({"usage": row.usage}) for row in attempts), 8),
    )


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def execute_v5_graph(
    graph: ExecutionGraph | Mapping[str, Any],
    run: Any,
    original_task: str,
    *,
    call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]] | None = None,
    output_dir: str | Path | None = None,
    limits: GraphLimits | None = None,
) -> dict[str, Any]:
    """Execute each DAG generation in parallel; stop closed on unrecovered work loss."""
    graph = graph if isinstance(graph, ExecutionGraph) else ExecutionGraph.from_mapping(graph)
    limits = limits or GraphLimits()
    issues = validate_execution_graph(graph, limits)
    if issues:
        raise V5ExecutionError("Invalid execution graph: " + "; ".join(f"{x.code}:{x.message}" for x in issues))
    call = call_fn or _default_call
    node_by_id = {node.node_id: node for node in graph.nodes}
    incoming: dict[str, list[str]] = {node.node_id: [] for node in graph.nodes}
    for edge in graph.edges:
        incoming[edge.target].append(edge.source)
    outputs: dict[str, NodeExecutionResult] = {}
    recovery = graph.metadata.get("recovery_pool", {}) if isinstance(graph.metadata, Mapping) else {}
    stage_records: list[dict[str, Any]] = []
    for stage_index, stage in enumerate(graph.execution_stages):
        futures = {}
        workers = min(max(1, int(getattr(run, "parallel_workers", len(stage) or 1))), len(stage))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for node_id in stage:
                upstream = [
                    {"node_id": source, "answer": outputs[source].answer, "quality_score": outputs[source].quality_score}
                    for source in incoming[node_id]
                    if source in outputs and outputs[source].answer
                ]
                futures[pool.submit(
                    _execute_node,
                    node_by_id[node_id], original_task, upstream, run, call,
                    list(recovery.get(node_id, [])) if isinstance(recovery, Mapping) else [],
                    limits,
                )] = node_id
            stage_results = [future.result() for future in as_completed(futures)]
        stage_results.sort(key=lambda row: row.node_id)
        for result in stage_results:
            outputs[result.node_id] = result
        failed = [result.node_id for result in stage_results if not result.status.startswith("success")]
        stage_records.append({
            "stage_index": stage_index,
            "node_ids": list(stage),
            "failed_node_ids": failed,
            "status": "failed" if failed else "success",
        })
        if failed:
            break
    successful_finals = [outputs[node_id] for node_id in graph.final_nodes if node_id in outputs and outputs[node_id].status.startswith("success")]
    complete = len(outputs) == len(graph.nodes) and all(result.status.startswith("success") for result in outputs.values()) and len(successful_finals) == len(graph.final_nodes)
    final_answer = "\n\n".join(result.answer or "" for result in successful_finals).strip()
    result = {
        "version": 5,
        "status": "success" if complete else "failed",
        "execution_stages": stage_records,
        "node_results": [asdict(outputs[node_id]) for node_id in sorted(outputs)],
        "final_node_ids": list(graph.final_nodes),
        "final_answer": final_answer or None,
        "actual_cost_usd": round(sum(result.actual_cost_usd for result in outputs.values()), 8),
        "recovery_used": any(attempt.replacement for result in outputs.values() for attempt in result.attempts),
        "stop_reason": "all-quality-gates-passed" if complete else "unrecovered-node-failure",
    }
    if output_dir is not None:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        _write_json(root / "v5-node-results.json", result["node_results"])
        _write_json(root / "v5-execution-summary.json", {key: value for key, value in result.items() if key != "node_results"})
        _write_json(root / "v5-request-audit.json", {
            "status": "PASS" if all(not FORBIDDEN_FIELDS.intersection(attempt.request) for row in outputs.values() for attempt in row.attempts) else "FAIL",
            "request_count": sum(len(row.attempts) for row in outputs.values()),
            "requests": [attempt.request for row in outputs.values() for attempt in row.attempts],
            "artificial_token_ceiling_sent": any("max_tokens" in attempt.request or "max_completion_tokens" in attempt.request for row in outputs.values() for attempt in row.attempts),
            "external_tools_allowed": False,
        })
        (root / "v5-final-report.md").write_text(final_answer or "# V5 execution failed\n", encoding="utf-8")
    if not complete:
        raise V5ExecutionError("V5 execution stopped because one or more required nodes failed quality and recovery gates.")
    return result
