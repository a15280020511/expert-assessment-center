"""Deliver the computed professional role without weakening output contracts."""
from __future__ import annotations

import v5_output_contract_delivery as contracts
from execution_graph import SelectedNode


def dynamic_system_prompt(node: SelectedNode) -> str:
    """Prepend the exact task-derived role to the canonical contract-aware prompt."""
    base = contracts.contract_aware_system_prompt(node)
    # New task-derived materialization stores the role under ``role``. Older
    # planner generations used ``professional_role``. Accept both so the role
    # actually reaches the model instead of becoming audit-only metadata.
    role = str(
        node.prompt_profile.get("professional_role")
        or node.prompt_profile.get("role")
        or ""
    ).strip()
    domains = [str(value) for value in node.prompt_profile.get("dominant_domains", [])]
    operations = [
        str(value)
        for value in (
            node.prompt_profile.get("cognitive_operations")
            or node.prompt_profile.get("modules")
            or []
        )
    ]
    if not role:
        return base
    evidence = (
        f"动态专业角色：{role}。"
        f"角色依据领域：{', '.join(domains) or '当前任务工作单元'}；"
        f"认知操作：{', '.join(operations) or 'analysis'}。"
        "该角色和认知操作仅定义本次任务的分析职责，不授予任何外部工具、"
        "联网、数据源或额外权限；只可使用题面与显式上游节点结果。"
    )
    return evidence + base
