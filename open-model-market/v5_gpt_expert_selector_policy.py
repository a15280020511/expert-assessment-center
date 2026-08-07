"""Task-dynamic GPT selector facade with unrestricted OpenRouter Provider routing.

The native module supplies the JSON schema and structural parser only. This
production facade removes historical Provider, company, TopN, price and fixed
reserve constraints before any governance request is sent.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

import v5_gpt_expert_selector as _native

for _name in dir(_native):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_native, _name)

proposal_sha256 = _native.proposal_sha256
governance_prompt_catalog = _native.governance_prompt_catalog

# Finite schema bounds remain operational safety only; they are not model,
# company, Provider, price or qualification gates.
MAXIMUM_RECOVERY_CANDIDATES_PER_NODE = 4
_NATIVE_RECOVERY_LIMIT = int(_native.GPT_MAX_RECOVERY_PER_NODE)

_DYNAMIC_SYSTEM_PROMPT = (
    "你是专家团中心的动态编组器。针对当前任务自行决定工作拆分、专家数量、角色、模型、"
    "组合、执行关系和恢复候选。不得实施TopN、固定4+4、固定公司数量、公司去重、价格门槛、"
    "旗舰门槛、Provider白名单/only/order、ZDR、require_parameters或精确Provider端点资格门禁。"
    "允许同一公司出现多个专家，也允许任何目录内模型参与。provider字段若因兼容旧JSON schema而出现，"
    "仅视为非绑定目录元数据；真正OpenRouter请求不得携带Provider路由限制。专家禁止外部工具。"
    "只输出严格JSON。"
)


def _unrestrict_request(request: Mapping[str, Any]) -> dict[str, Any]:
    expanded = deepcopy(dict(request))
    expanded.pop("provider", None)

    messages = expanded.get("messages")
    if isinstance(messages, list) and messages:
        updated = list(messages)
        first = updated[0]
        if isinstance(first, Mapping):
            updated[0] = {**dict(first), "content": _DYNAMIC_SYSTEM_PROMPT}
        if len(updated) > 1 and isinstance(updated[1], Mapping):
            user = dict(updated[1])
            try:
                payload = json.loads(str(user.get("content") or ""))
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, Mapping):
                payload = dict(payload)
                constraints = payload.get("execution_constraints")
                constraints = dict(constraints) if isinstance(constraints, Mapping) else {}
                for key in (
                    "approved_total_calls",
                    "governance_calls_reserved",
                    "approved_recovery_calls",
                    "maximum_expert_initial_calls",
                    "distinct_expert_companies",
                    "governance_companies_forbidden_for_experts",
                    "provider_fallback_allowed",
                    "recovery_candidate_count_required",
                ):
                    constraints.pop(key, None)
                constraints.update(
                    {
                        "task_dynamic_expert_count": True,
                        "company_uniqueness_required": False,
                        "provider_routing_mode": "unrestricted-openrouter",
                        "provider_restrictions_allowed": False,
                        "topn_gate_required": False,
                        "price_gate_required": False,
                        "tools_allowed": False,
                    }
                )
                payload["execution_constraints"] = constraints
                user["content"] = _native._canonical_json(payload)
                updated[1] = user
        expanded["messages"] = updated

    try:
        recovery = expanded["response_format"]["json_schema"]["schema"][
            "properties"
        ]["nodes"]["items"]["properties"]["recovery"]
    except (KeyError, TypeError) as exc:
        raise _native.GPTSelectorError(
            "GPT selector response schema recovery contract is missing"
        ) from exc
    if isinstance(recovery, dict):
        recovery["maxItems"] = MAXIMUM_RECOVERY_CANDIDATES_PER_NODE

    governance = expanded.get("governance_policy")
    governance = dict(governance) if isinstance(governance, Mapping) else {}
    governance.update(
        {
            "provider_routing_mode": "unrestricted-openrouter",
            "provider_restrictions_applied": False,
            "company_uniqueness_constraint_used": False,
            "fixed_team_size_constraint_used": False,
            "topn_qualification_gate_used": False,
            "price_qualification_gate_used": False,
            "recovery_limit_is_finite_operational_safety": True,
        }
    )
    expanded["governance_policy"] = governance
    return expanded


def build_proposal_request(**kwargs: Any) -> dict[str, Any]:
    return _unrestrict_request(_native.build_proposal_request(**kwargs))


def build_synthesis_request(**kwargs: Any) -> dict[str, Any]:
    return _unrestrict_request(_native.build_synthesis_request(**kwargs))


def _validate_extra_recovery_rows(rows: list[Any]) -> None:
    if len(rows) > MAXIMUM_RECOVERY_CANDIDATES_PER_NODE:
        raise _native.GPTSelectorError("node recovery exceeds finite graph-safety bound")
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"model", "provider"}:
            raise _native.GPTSelectorError("recovery row has invalid fields")
        _native._identifier(row["model"], "recovery model", _native.GPT_IDENTIFIER_PATTERN)
        _native._identifier(row["provider"], "recovery provider metadata", _native.GPT_IDENTIFIER_PATTERN)


def parse_proposal(text: str) -> dict[str, Any]:
    """Validate structural JSON while retaining compatibility Provider metadata."""
    if not isinstance(text, str) or not text.strip():
        raise _native.GPTSelectorError("GPT proposal is empty")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _native.GPTSelectorError("GPT proposal is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise _native.GPTSelectorError("GPT proposal root must be an object")

    full = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    validation_copy = deepcopy(full)
    nodes = validation_copy.get("nodes")
    full_nodes = full.get("nodes")
    if not isinstance(nodes, list) or not isinstance(full_nodes, list):
        return _native.parse_proposal(text)

    for index, (node, full_node) in enumerate(zip(nodes, full_nodes, strict=True)):
        if not isinstance(node, Mapping) or not isinstance(full_node, Mapping):
            continue
        rows = full_node.get("recovery")
        if not isinstance(rows, list):
            continue
        _validate_extra_recovery_rows(rows)
        node = dict(node)
        node["recovery"] = rows[:_NATIVE_RECOVERY_LIMIT]
        nodes[index] = node

    parsed = _native.parse_proposal(_native._canonical_json(validation_copy))
    parsed_nodes = parsed.get("nodes", [])
    for index, full_node in enumerate(full_nodes):
        if not isinstance(full_node, Mapping):
            continue
        rows = full_node.get("recovery")
        if isinstance(rows, list) and index < len(parsed_nodes):
            parsed_nodes[index]["recovery"] = json.loads(
                json.dumps(rows, ensure_ascii=False, allow_nan=False)
            )
    return json.loads(json.dumps(parsed, ensure_ascii=False, allow_nan=False))


__all__ = [
    "MAXIMUM_RECOVERY_CANDIDATES_PER_NODE",
    "build_proposal_request",
    "build_synthesis_request",
    "parse_proposal",
    "proposal_sha256",
    "governance_prompt_catalog",
]
