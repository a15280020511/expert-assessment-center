"""V5 runtime compatibility layer for audited provider fallback pools.

The native runtime implementation is preserved verbatim in
``v5_runtime_legacy``. This layer accepts both the legacy exact single-provider
lock and the new explicit audited same-model provider whitelist. New production
plans generate only the whitelist form; the legacy form remains rollback-only.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_runtime_legacy as _legacy
from execution_graph import SelectedNode
from v5_provider_lock import canonical_provider_lock

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


class PromptPolicy:
    """Build expert payloads with a fail-closed provider routing contract."""

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
        if (
            isinstance(messages, list)
            and messages
            and isinstance(messages[0], Mapping)
        ):
            messages[0] = {
                **dict(messages[0]),
                "content": _legacy.dynamic_prompt.dynamic_system_prompt(node),
            }
            payload["messages"] = messages

        if not canonical_provider_lock(payload):
            raise RuntimeError(
                "provider routing must be either one exact locked endpoint or "
                "one explicit audited same-model provider whitelist"
            )
        _legacy.assert_request_has_no_tools(
            payload,
            context=f"expert node {node.node_id} request",
        )
        return payload


# ProductionRuntime and every derived runtime resolve PromptPolicy dynamically
# from the implementation module, so patching this one symbol preserves all
# other native runtime behavior while changing only the provider contract.
_legacy.PromptPolicy = PromptPolicy
