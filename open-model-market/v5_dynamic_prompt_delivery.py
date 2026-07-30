"""Deliver the dynamically computed professional role in the actual node prompt."""
from __future__ import annotations

import v5_executor as executor
from execution_graph import SelectedNode

_INSTALLED = False
_ORIGINAL_SYSTEM_PROMPT = executor._system_prompt


def dynamic_system_prompt(node: SelectedNode) -> str:
    base = _ORIGINAL_SYSTEM_PROMPT(node)
    role = str(node.prompt_profile.get("professional_role") or "").strip()
    domains = [str(value) for value in node.prompt_profile.get("dominant_domains", [])]
    operations = [str(value) for value in node.prompt_profile.get("cognitive_operations", [])]
    if not role:
        return base
    evidence = (
        f"动态专业角色：{role}。"
        f"角色依据领域：{', '.join(domains) or '未声明'}；"
        f"认知操作：{', '.join(operations) or 'analysis'}。"
        "该角色只定义分析视角，不授予任何外部工具、数据源或额外权限。"
    )
    return evidence + base


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    executor._system_prompt = dynamic_system_prompt
    _INSTALLED = True
