"""V5 runtime compatibility layer for unrestricted OpenRouter provider routing."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_runtime_legacy as _legacy
from execution_graph import SelectedNode

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


class PromptPolicy:
    """Build expert payloads without any provider routing constraints."""

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
                        "answer": _legacy.json.dumps(
                            contract,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                    }
                )
            else:
                structured.append(dict(row))
        payload = _legacy.cost_hardening.hardened_build_node_payload(
            node,
            original_task,
            structured,
        )
        messages = payload.get("messages")
        if isinstance(messages, list) and messages and isinstance(messages[0], Mapping):
            messages[0] = {
                **dict(messages[0]),
                "content": _legacy.dynamic_prompt.dynamic_system_prompt(node),
            }
            payload["messages"] = messages

        # Complete provider opening: OpenRouter decides the provider. Removing
        # the provider object also removes require_parameters, only/order, ZDR,
        # data-collection and price/quantization filters inherited from legacy.
        payload.pop("provider", None)
        _legacy.assert_request_has_no_tools(
            payload,
            context=f"expert node {node.node_id} request",
        )
        return payload


_legacy.PromptPolicy = PromptPolicy
