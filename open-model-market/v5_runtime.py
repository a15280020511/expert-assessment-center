"""V5 runtime compatibility layer for audited provider fallback pools.

The native runtime implementation is preserved verbatim in
``v5_runtime_legacy``. This layer replaces only request-policy validation so a
request may carry more than one provider when—and only when—``only`` and
``order`` are the same explicit audited whitelist for one fixed model.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_runtime_legacy as _legacy
from execution_graph import SelectedNode

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


class PromptPolicy:
    """Build expert payloads with an explicit same-model provider whitelist."""

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

        provider = payload.get("provider")
        if not isinstance(provider, Mapping):
            raise RuntimeError("provider whitelist missing from node request")
        only = provider.get("only")
        order = provider.get("order")
        if (
            not isinstance(only, list)
            or not only
            or not isinstance(order, list)
            or only != order
        ):
            raise RuntimeError(
                "provider.only and provider.order must be the same non-empty audited whitelist"
            )
        normalized = [str(value).strip() for value in only]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise RuntimeError("provider whitelist contains empty or duplicate entries")
        if provider.get("allow_fallbacks") is not True:
            raise RuntimeError("audited same-model provider fallback must be enabled")
        if provider.get("require_parameters") is not True:
            raise RuntimeError("provider.require_parameters must be true")

        _legacy.assert_request_has_no_tools(
            payload,
            context=f"expert node {node.node_id} request",
        )
        return payload


# ProductionRuntime and every derived runtime resolve PromptPolicy dynamically
# from the implementation module, so patching this one symbol preserves all
# other native runtime behavior while changing only the provider contract.
_legacy.PromptPolicy = PromptPolicy
