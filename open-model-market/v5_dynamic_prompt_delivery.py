"""Deliver the computed professional role without weakening output contracts."""
from __future__ import annotations

import v5_output_contract_delivery as contracts
from execution_graph import SelectedNode



def dynamic_system_prompt(node: SelectedNode) -> str:
    """Prepend the dynamic role to the canonical contract-aware system prompt."""
    base = contracts.contract_aware_system_prompt(node)
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
