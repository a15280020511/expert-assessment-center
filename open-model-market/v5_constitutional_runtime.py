"""Compatibility facade for the native constitutional runtime.

Legacy constitutional behavior remains available, but Provider locking is a
subclass policy rather than an unconditional pre-send assertion. The default is
still locked for compatibility; the active production expert policy explicitly
opts out so OpenRouter can select and fail over Providers without a local
allowlist/order/ZDR constraint.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

import v5_constitutional_runtime_legacy as _legacy
import v5_cost_reliability_hardening as cost_hardening
import v5_dynamic_prompt_delivery as dynamic_prompt
import v5_task_delivery_contract as delivery_contract
from execution_graph import SelectedNode
from v5_no_tools_policy import assert_request_has_no_tools
from v5_task_constraints import (
    closed_world_numeric_prompt,
    compile_task_constraints,
)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


class ConstitutionalPromptPolicy(_legacy.ConstitutionalPromptPolicy):
    """Reusable constitutional prompt builder with configurable Provider locking."""

    provider_lock_required = True

    def build_payload(
        self,
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
                        "answer": json.dumps(
                            self._compact_upstream_contract(contract),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                    }
                )
            else:
                structured.append(dict(row))

        node_task = delivery_contract.project_task_for_node(
            original_task,
            node.output_contract,
        )
        payload = cost_hardening.hardened_build_node_payload(
            node,
            node_task,
            structured,
        )
        constraints = compile_task_constraints(original_task)
        numeric_policy = closed_world_numeric_prompt(original_task, constraints)
        delivery_discipline = ""
        if bool(node.output_contract.get("explicit_markdown_contract")):
            delivery_discipline = (
                "\n显式长篇合同交付纪律：先按顺序生成全部指定H2标题并确保每节非空，"
                "再补充细节。若输出空间紧张，压缩重复事实、表格和修饰语，"
                "不得遗漏标题、改变顺序、增加其他H2或用冗长复述耗尽输出。"
            )

        messages = payload.get("messages")
        if (
            isinstance(messages, list)
            and messages
            and isinstance(messages[0], Mapping)
        ):
            system = dynamic_prompt.dynamic_system_prompt(node)
            constitutional = json.dumps(
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

        if self.provider_lock_required:
            provider = payload.get("provider")
            if not isinstance(provider, Mapping):
                raise RuntimeError("provider lock missing from node request")
            only = provider.get("only")
            if not isinstance(only, list) or len(only) != 1:
                raise RuntimeError(
                    "provider.only must contain exactly one endpoint provider"
                )
            if provider.get("allow_fallbacks") is not False:
                raise RuntimeError("provider fallbacks must be disabled")

        assert_request_has_no_tools(
            payload,
            context=f"constitutional node {node.node_id} request",
        )
        return payload


__all__ = [name for name in dir(_legacy) if not name.startswith("__")]
if "ConstitutionalPromptPolicy" not in __all__:
    __all__.append("ConstitutionalPromptPolicy")
