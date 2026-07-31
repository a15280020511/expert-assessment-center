"""Turn V5 output contracts into direct, concise delivery instructions.

Contract metadata is converted into executable delivery rules. Both exact JSON
schemas and explicit user-requested Markdown H2 section lists are validated so
schema echoes, missing sections and truncated outputs cannot masquerade as
completed work.
"""
from __future__ import annotations

import json
import os
from typing import Any, Mapping

import v5_executor
import v5_task_delivery_contract as task_delivery_contract
from execution_graph import SelectedNode

_INSTALLED = False
_ORIGINAL_SYSTEM_PROMPT = v5_executor._system_prompt
_ORIGINAL_QUALITY_GATE = v5_executor.quality_gate
COMPACT_MODE_ENV = "V5_COMPACT_OUTPUT_CONTRACT"
CONTRACT_METADATA_KEYS = (
    "machine_readable_required",
    "must_separate_fact_assumption_inference",
    "required_fields",
    "exact_top_level_fields",
    "nested_exact_fields",
    "nested_values_must_be_objects",
    "explicit_user_contract",
    "exact_markdown_headings",
    "explicit_markdown_contract",
)


def _required_fields(node: SelectedNode) -> list[str]:
    return [
        str(value).strip()
        for value in node.output_contract.get("required_fields", [])
        if str(value).strip()
    ]


def _compact_mode_enabled() -> bool:
    return os.getenv(COMPACT_MODE_ENV, "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _compact_delivery_rule(fields: list[str]) -> str:
    """Return canary-only brevity limits sized to the number of required fields."""
    field_count = max(1, len(fields))
    maximum_items = 1 if field_count >= 6 else 2
    maximum_chars = 36 if field_count >= 6 else 48
    return (
        "这是受限Token的微型Canary精简模式，仅用于验证执行链。"
        "先确保JSON骨架和全部顶层键完整，再填写最关键内容。"
        f"每个普通字段最多{maximum_items}条；每条不超过{maximum_chars}个中文字符。"
        "acceptance_tests字段最多3条，每条不超过48个中文字符。"
        "禁止复述题目、背景、字段含义或相同结论；只保留最高严重度风险、最关键证据和可执行动作。"
        "整个JSON尽量控制在450个中文字符以内，并必须在输出上限前闭合所有括号和引号。"
    )


def _delivery_rule(node: SelectedNode) -> str:
    fields = _required_fields(node)
    quoted_fields = json.dumps(fields, ensure_ascii=False)
    separate = bool(
        node.output_contract.get("must_separate_fact_assumption_inference")
    )
    compact_rule = _compact_delivery_rule(fields) if _compact_mode_enabled() else ""
    explicit_rule = task_delivery_contract.delivery_rule(node.output_contract)
    if node.output_contract.get("machine_readable_required"):
        separation_rule = (
            "事实、假设、推断和不确定性必须在相应字段内明确区分。"
            if separate
            else ""
        )
        return (
            "最终响应必须只包含一个合法JSON对象，不要使用Markdown代码块或任何前后缀。"
            f"JSON顶层必须包含这些键：{quoted_fields}。"
            "每个键必须直接填写本节点对原始任务的实际分析、证据、结论或建议；"
            "禁止复述输出契约、字段清单或模式定义，禁止输出"
            "machine_readable_required、must_separate_fact_assumption_inference、required_fields"
            "等契约元数据。"
            f"{explicit_rule}{separation_rule}"
            "内容必须精炼，避免重复；在篇幅受限时优先保证所有必填键存在且JSON语法完整闭合。"
            f"{compact_rule}"
        )
    if node.output_contract.get("explicit_markdown_contract"):
        separation_rule = (
            "每个章节内必须明确区分事实、假设、推断和不确定性。"
            if separate
            else ""
        )
        return (
            f"{explicit_rule}{separation_rule}"
            "禁止复述输出契约、章节清单或模式定义。"
            "内容必须完整、可直接使用；先保证全部二级章节存在并填充，再扩展三级标题和细节。"
            f"{compact_rule}"
        )
    field_text = "、".join(fields) if fields else "任务要求的交付内容"
    heading_rule = (
        "必须按以下顺序使用完全一致的Markdown二级标题，并在每个标题下填写非空正文："
        + "、".join(f"## {field}" for field in fields)
        + "。不得把多个必填字段合并到同一标题，也不得只在段落中提到字段名。"
        if fields
        else ""
    )
    separation_rule = (
        "正文中必须明确区分事实、假设、推断和不确定性。"
        if separate
        else ""
    )
    return (
        f"最终响应必须直接交付以下内容：{field_text}。"
        f"{heading_rule}"
        "禁止复述输出契约、字段清单或模式定义。"
        f"{separation_rule}"
        "内容应精炼、完整、可直接使用。"
        f"{compact_rule}"
    )


def contract_aware_system_prompt(node: SelectedNode) -> str:
    modules = list(node.prompt_profile.get("modules", []))
    rules = "".join(
        v5_executor.PROMPT_MODULES.get(
            str(name), f"执行提示模块：{name}。"
        )
        for name in modules
    )
    functions = "、".join(node.functions)
    return (
        "你是V5动态专家执行图中的一个严格隔离节点。"
        f"本节点功能：{functions}。负责原子工作：{', '.join(node.assigned_work)}。"
        "禁止调用、请求或假装使用网页、搜索、插件、文件、代码执行、数据库、API、浏览器、工具或其他模型。"
        "只能依据原始任务和系统显式传入的上游节点结果。不得读取未声明节点，不得与同独立组节点交换结果。"
        f"{rules}{_delivery_rule(node)}"
        "不要展示隐藏思维过程。"
    )


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def contract_aware_quality_gate(
    node: SelectedNode,
    response: Mapping[str, Any],
    answer: str,
) -> tuple[bool, float, list[str]]:
    """Enforce exact task contracts after the base semantic quality gate."""
    passed, score, reasons = _ORIGINAL_QUALITY_GATE(node, response, answer)
    markdown_violations = task_delivery_contract.validate_markdown_contract(
        answer, node.output_contract
    )
    for violation in markdown_violations:
        _append_reason(reasons, violation)
    if markdown_violations:
        passed = False
        score = min(float(score), 0.35)

    if not node.output_contract.get("machine_readable_required"):
        return passed, score, reasons

    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError:
        return passed, score, reasons
    if not isinstance(parsed, Mapping):
        return passed, score, reasons

    required = _required_fields(node)
    missing = [field for field in required if field not in parsed]
    metadata = [key for key in CONTRACT_METADATA_KEYS if key in parsed]
    if missing:
        _append_reason(reasons, "missing-required-json-keys:" + ",".join(missing))
    if metadata and len(missing) == len(required):
        _append_reason(reasons, "contract-metadata-echo")
    explicit_violations = task_delivery_contract.validate_parsed_contract(
        parsed, node.output_contract
    )
    for violation in explicit_violations:
        _append_reason(reasons, violation)

    if missing or "contract-metadata-echo" in reasons or explicit_violations:
        passed = False
        score = min(float(score), 0.35)
    return passed, score, reasons


def install() -> None:
    """Install contract-aware prompt and quality gate for formal V5 paths."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    v5_executor._system_prompt = contract_aware_system_prompt
    v5_executor.quality_gate = contract_aware_quality_gate
