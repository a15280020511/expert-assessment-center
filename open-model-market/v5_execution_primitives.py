"""Native request, response and semantic quality primitives for V5."""
from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode
from v5_no_tools_policy import assert_request_has_no_tools

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


class V5ExecutionPrimitiveError(RuntimeError):
    pass


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def system_prompt(node: SelectedNode) -> str:
    modules = list(node.prompt_profile.get("modules", []))
    rules = "".join(
        PROMPT_MODULES.get(str(name), f"执行提示模块：{name}。")
        for name in modules
    )
    contract = json.dumps(dict(node.output_contract), ensure_ascii=False, sort_keys=True)
    functions = "、".join(node.functions)
    function_rule = f"本节点功能：{functions}。" if functions else ""
    return (
        "你是V5动态专家执行图中的一个严格隔离节点。"
        f"{function_rule}负责原子工作：{', '.join(node.assigned_work)}。"
        "禁止调用、请求或假装使用网页、搜索、插件、文件、代码执行、数据库、API、浏览器、工具或其他模型。"
        "只能依据原始任务和系统显式传入的上游节点结果。不得读取未声明节点，不得与同独立组节点交换结果。"
        f"{rules}输出契约：{contract}。输出完整可交付正文；不要展示隐藏思维过程。"
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
    ) or "[无上游结果；请独立处理。]"
    payload: dict[str, Any] = {
        "model": node.model,
        "messages": [
            {"role": "system", "content": system_prompt(node)},
            {
                "role": "user",
                "content": (
                    f"原始任务：\n{original_task}\n\n"
                    f"本节点工作ID：{', '.join(node.assigned_work)}\n\n"
                    f"允许读取的上游结果：\n{upstream_text}"
                ),
            },
        ],
        "stream": False,
    }
    payload.update(_json_copy(dict(node.request_config)))
    assert_request_has_no_tools(
        payload, context=f"expert node {node.node_id} request"
    )
    if "max_tokens" in payload or "max_completion_tokens" in payload:
        raise V5ExecutionPrimitiveError(
            "Artificial output token ceilings are forbidden in the base payload."
        )
    return payload


def extract_answer(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], Mapping) else {}
    message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(row["text"])
            for row in content
            if isinstance(row, Mapping) and isinstance(row.get("text"), str)
        ).strip()
    return ""


def finite_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def finish_reason(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        return str(choices[0].get("finish_reason") or "")
    return ""


def actual_cost(response: Mapping[str, Any]) -> float:
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    for key in ("cost", "total_cost"):
        try:
            if usage.get(key) is not None:
                return max(0.0, float(usage[key]))
        except (TypeError, ValueError):
            continue
    return 0.0


def quality_gate(
    node: SelectedNode,
    response: Mapping[str, Any],
    answer: str,
) -> tuple[bool, float, list[str]]:
    """Gate observable delivery failures without fixed business heuristics.

    Length is not treated as a proxy for quality and model-estimated quality does
    not become a hidden recovery threshold. Hard failures are directly
    auditable: empty delivery, truncation, tool-dependent non-delivery, missing
    contract field markers, or malformed required JSON. The numeric score is an
    equal-weight telemetry summary of the active observable signals only.
    """
    reasons: list[str] = []
    rendered = str(answer or "").strip()
    folded = rendered.casefold()
    finish = finish_reason(response).casefold()

    nonempty_score = 1.0 if rendered else 0.0
    if not rendered:
        reasons.append("empty-output")

    finish_score = 0.0 if finish in {"length", "max_tokens"} else 1.0
    if finish_score == 0.0:
        reasons.append("truncated-output")

    refusal_terms = (
        "i cannot access",
        "无法访问互联网",
        "作为ai无法",
        "没有提供任何答案",
    )
    delivery_score = 0.0 if any(term in folded for term in refusal_terms) else 1.0
    if delivery_score == 0.0:
        reasons.append("non-delivery-or-tool-dependency")

    required_fields = [
        str(value).strip()
        for value in node.output_contract.get("required_fields", [])
        if str(value).strip()
    ]
    field_hits = sum(
        field.replace("_", " ").casefold() in folded
        or field.casefold() in folded
        for field in required_fields
    )
    contract_score = (
        field_hits / len(required_fields)
        if required_fields
        else 1.0
    )
    if required_fields and field_hits < len(required_fields):
        missing = [
            field
            for field in required_fields
            if field.replace("_", " ").casefold() not in folded
            and field.casefold() not in folded
        ]
        reasons.append("missing-required-field-markers:" + ",".join(missing))

    signal_scores = [
        nonempty_score,
        finish_score,
        delivery_score,
        contract_score,
    ]
    if node.output_contract.get("machine_readable_required"):
        json_score = 0.0
        try:
            parsed = json.loads(rendered)
            if isinstance(parsed, Mapping):
                json_score = 1.0
            else:
                reasons.append("machine-readable-output-not-object")
        except json.JSONDecodeError:
            reasons.append("invalid-required-json")
        signal_scores.append(json_score)

    score = sum(signal_scores) / max(1, len(signal_scores))
    return not reasons, round(max(0.0, min(1.0, score)), 6), reasons
