"""Constitutional runtime compatibility for unrestricted provider routing."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_constitutional_runtime_legacy as _legacy
from execution_graph import SelectedNode


def _build_payload(
    self: Any,
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    structured = []
    for row in upstream:
        contract = row.get("contract") if isinstance(row, Mapping) else None
        if isinstance(contract, Mapping):
            structured.append(
                {
                    "node_id": row.get("node_id"),
                    "answer": _legacy.json.dumps(
                        self._compact_upstream_contract(contract),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                }
            )
        else:
            structured.append(dict(row))
    node_task = _legacy.delivery_contract.project_task_for_node(
        original_task,
        node.output_contract,
    )
    payload = _legacy.cost_hardening.hardened_build_node_payload(
        node,
        node_task,
        structured,
    )
    constraints = _legacy.compile_task_constraints(original_task)
    numeric_policy = _legacy.closed_world_numeric_prompt(original_task, constraints)
    delivery_discipline = ""
    if bool(node.output_contract.get("explicit_markdown_contract")):
        delivery_discipline = (
            "\n显式长篇合同交付纪律：先按顺序生成全部指定H2标题并确保每节非空，"
            "再补充细节。若输出空间紧张，压缩重复事实、表格和修饰语，"
            "不得遗漏标题、改变顺序、增加其他H2或用冗长复述耗尽输出。"
        )
    messages = payload.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], Mapping):
        system = _legacy.dynamic_prompt.dynamic_system_prompt(node)
        constitutional = _legacy.json.dumps(
            constraints.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        messages[0] = {
            **dict(messages[0]),
            "content": (
                system
                + "\n\n不可覆盖的任务约束："
                + constitutional
                + "\n题面是唯一用户事实源。模型推断必须标为推断或假设；"
                "不得把上游模型判断改标为事实；不得引入题面没有的精确数量。"
                "事实标签必须只承载题面事实；任何必须、禁止、建议、否决、"
                "优先或行动要求必须另起结论或推断标签，不得与事实同句。"
                + (("\n" + numeric_policy) if numeric_policy else "")
                + delivery_discipline
            ),
        }
        payload["messages"] = messages

    payload.pop("provider", None)
    _legacy.assert_request_has_no_tools(
        payload,
        context=f"constitutional node {node.node_id} request",
    )
    return payload


_legacy.ConstitutionalPromptPolicy.build_payload = _build_payload

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

ConstitutionalPromptPolicy = _legacy.ConstitutionalPromptPolicy
ConstitutionalExecutionEngine = _legacy.ConstitutionalExecutionEngine
validate_scope_boundaries = _legacy.validate_scope_boundaries
