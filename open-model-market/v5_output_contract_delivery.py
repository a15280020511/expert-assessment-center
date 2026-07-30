"""Turn V5 output contracts into direct, concise delivery instructions.

The previous executor serialized contract metadata into the prompt. Some models
then repeated that metadata instead of filling the required fields, and bounded
outputs were truncated before the JSON object closed. This module replaces the
system prompt with an executable contract: required keys, actual content, no
schema echo, no Markdown fence, and explicit JSON-closure priority.
"""
from __future__ import annotations

import json
from typing import Any

import v5_executor
from execution_graph import SelectedNode

_INSTALLED = False
_ORIGINAL_SYSTEM_PROMPT = v5_executor._system_prompt
CONTRACT_METADATA_KEYS = (
    "machine_readable_required",
    "must_separate_fact_assumption_inference",
    "required_fields",
)


def _required_fields(node: SelectedNode) -> list[str]:
    return [
        str(value).strip()
        for value in node.output_contract.get("required_fields", [])
        if str(value).strip()
    ]


def _delivery_rule(node: SelectedNode) -> str:
    fields = _required_fields(node)
    quoted_fields = json.dumps(fields, ensure_ascii=False)
    separate = bool(
        node.output_contract.get("must_separate_fact_assumption_inference")
    )
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
            f"{separation_rule}"
            "内容必须精炼，避免重复；在篇幅受限时优先保证所有必填键存在且JSON语法完整闭合。"
        )
    field_text = "、".join(fields) if fields else "任务要求的交付内容"
    separation_rule = (
        "正文中必须明确区分事实、假设、推断和不确定性。"
        if separate
        else ""
    )
    return (
        f"最终响应必须直接交付以下内容：{field_text}。"
        "禁止复述输出契约、字段清单或模式定义。"
        f"{separation_rule}"
        "内容应精炼、完整、可直接使用。"
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


def install() -> None:
    """Install the contract-aware prompt once for all formal V5 execution paths."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    v5_executor._system_prompt = contract_aware_system_prompt
