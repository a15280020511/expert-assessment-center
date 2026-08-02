"""Bounded Claude red-team policy for expert selection and information review.

Claude is a fail-closed reviewer only. It cannot select, replace, reorder, or
execute experts and cannot write reports. All inputs and outputs are compact,
strictly structured, length-bounded, and auditable.
"""
from __future__ import annotations

import json
import math
import re
from enum import Enum
from typing import Any, Mapping

CLAUDE_RED_TEAM_MODEL = "anthropic/claude-opus-5"
CLAUDE_RED_TEAM_PROVIDER = "anthropic"
CLAUDE_RED_TEAM_REASONING_EFFORT = "low"
CLAUDE_RED_TEAM_MAX_INPUT_CHARS = 6_000
CLAUDE_RED_TEAM_MAX_OUTPUT_CHARS = 800
CLAUDE_RED_TEAM_MAX_OUTPUT_TOKENS = 128
CLAUDE_RED_TEAM_MAX_ITEMS = 16
CLAUDE_RED_TEAM_MAX_CODES = 8
CLAUDE_RED_TEAM_MAX_TARGETS = 8
CLAUDE_RED_TEAM_MAX_STRING_CHARS = 240
CLAUDE_RED_TEAM_MAX_DEPTH = 5
CLAUDE_RED_TEAM_GOVERNANCE_CALLS = 2

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/+-]{1,96}$")


class RedTeamScope(str, Enum):
    INTERNAL_SELECTION = "internal_selection"
    EXTERNAL_INFORMATION = "external_information"


INTERNAL_CODES = frozenset(
    {
        "UNKNOWN_CANDIDATE",
        "WORK_UNCOVERED",
        "WORK_DUPLICATED",
        "DUPLICATE_COMPANY",
        "CALL_LIMIT_EXCEEDED",
        "RECOVERY_LIMIT_EXCEEDED",
        "COST_LIMIT_EXCEEDED",
        "PROVIDER_UNLOCKED",
        "TOOL_PERMISSION_PRESENT",
        "GRAPH_INVALID",
        "CONTRACT_MISMATCH",
        "ROLE_MISMATCH",
        "INSUFFICIENT_REVIEW_INPUT",
    }
)
EXTERNAL_CODES = frozenset(
    {
        "UNSUPPORTED_FACT",
        "FACT_INFERENCE_MIXED",
        "QUANTITY_CONFLICT",
        "LOCATION_CONFLICT",
        "SOURCE_MISSING",
        "UNKNOWN_NOT_PRESERVED",
        "CONTRACT_VIOLATION",
        "INFORMATION_INCOMPLETE",
        "INSUFFICIENT_REVIEW_INPUT",
    }
)

_SYSTEM_PROMPTS = {
    RedTeamScope.INTERNAL_SELECTION: (
        "你是受限红队判定器。只检查GPT提出的专家团动态组合是否违反输入中的硬约束。"
        "不得选择、替换、排序、补充专家，不得修改执行图，不得输出解释、建议、报告或自然语言。"
        "只返回JSON Schema允许的APPROVE或REJECT、枚举codes和targets。证据不足必须REJECT。"
    ),
    RedTeamScope.EXTERNAL_INFORMATION: (
        "你是受限信息审核判定器。只检查输入信息的事实来源、数量、位置、未知项和交付合同。"
        "不得补充事实、改写内容、给出建议、生成报告或参与专家执行。"
        "只返回JSON Schema允许的APPROVE或REJECT、枚举codes和targets。证据不足必须REJECT。"
    ),
}

_INTERNAL_TOP_LEVEL_KEYS = frozenset(
    {
        "task_digest",
        "proposal_digest",
        "approved_total_calls",
        "governance_calls_reserved",
        "approved_recovery_calls",
        "cost_anomaly_usd",
        "required_work",
        "nodes",
        "edges",
    }
)
_INTERNAL_NODE_KEYS = frozenset(
    {
        "node_id",
        "candidate_id",
        "work_ids",
        "model",
        "company",
        "provider",
        "estimated_cost_usd",
        "contract_kind",
        "recovery_candidate_ids",
    }
)
_INTERNAL_EDGE_KEYS = frozenset({"source", "target"})
_EXTERNAL_TOP_LEVEL_KEYS = frozenset(
    {
        "task_digest",
        "information_digest",
        "contract_kind",
        "claims",
    }
)
_EXTERNAL_CLAIM_KEYS = frozenset(
    {
        "claim_id",
        "label",
        "source",
        "text",
        "quantity_tokens",
        "location_tokens",
    }
)


def _codes(scope: RedTeamScope) -> frozenset[str]:
    return INTERNAL_CODES if scope is RedTeamScope.INTERNAL_SELECTION else EXTERNAL_CODES


def _check_digest(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")


def _check_id(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{field} is missing or not a bounded identifier")


def _check_number(value: Any, field: str, *, minimum: float = 0.0) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < minimum:
        raise ValueError(f"{field} is outside the allowed range")


def _check_integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} is outside the allowed range")
    return value


def _check_string_list(value: Any, field: str, *, maximum: int = CLAUDE_RED_TEAM_MAX_ITEMS) -> None:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be a bounded list")
    for index, item in enumerate(value):
        _check_id(item, f"{field}[{index}]")


def _check_tree(value: Any, *, depth: int = 0) -> None:
    if depth > CLAUDE_RED_TEAM_MAX_DEPTH:
        raise ValueError("Claude red-team input exceeds maximum nesting depth")
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Claude red-team input contains a non-finite number")
        return
    if isinstance(value, str):
        if len(value) > CLAUDE_RED_TEAM_MAX_STRING_CHARS:
            raise ValueError("Claude red-team input contains an oversized string")
        return
    if isinstance(value, list):
        if len(value) > CLAUDE_RED_TEAM_MAX_ITEMS:
            raise ValueError("Claude red-team input contains an oversized list")
        for item in value:
            _check_tree(item, depth=depth + 1)
        return
    if isinstance(value, Mapping):
        if len(value) > CLAUDE_RED_TEAM_MAX_ITEMS:
            raise ValueError("Claude red-team input contains an oversized object")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 64:
                raise ValueError("Claude red-team input contains an invalid key")
            _check_tree(item, depth=depth + 1)
        return
    raise ValueError("Claude red-team input contains an unsupported value type")


def _validate_internal(payload: Mapping[str, Any]) -> None:
    if set(payload) != _INTERNAL_TOP_LEVEL_KEYS:
        raise ValueError("internal Claude review input has missing or extra fields")
    _check_digest(payload["task_digest"], "task_digest")
    _check_digest(payload["proposal_digest"], "proposal_digest")
    total_calls = _check_integer(
        payload["approved_total_calls"],
        "approved_total_calls",
        minimum=4,
        maximum=16,
    )
    governance_calls = _check_integer(
        payload["governance_calls_reserved"],
        "governance_calls_reserved",
        minimum=CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
        maximum=CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
    )
    recovery_calls = _check_integer(
        payload["approved_recovery_calls"],
        "approved_recovery_calls",
        minimum=0,
        maximum=total_calls - governance_calls - 1,
    )
    if payload["cost_anomaly_usd"] is not None:
        _check_number(payload["cost_anomaly_usd"], "cost_anomaly_usd", minimum=0.00000001)
    _check_string_list(payload["required_work"], "required_work")

    nodes = payload["nodes"]
    maximum_nodes = total_calls - governance_calls - recovery_calls
    if (
        not isinstance(nodes, list)
        or not nodes
        or len(nodes) > min(CLAUDE_RED_TEAM_MAX_ITEMS, maximum_nodes)
    ):
        raise ValueError("nodes exceed the expert-call capacity after governance and recovery reserve")
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping) or set(node) != _INTERNAL_NODE_KEYS:
            raise ValueError(f"nodes[{index}] has missing or extra fields")
        for field in ("node_id", "candidate_id", "model", "company", "provider", "contract_kind"):
            _check_id(node[field], f"nodes[{index}].{field}")
        _check_string_list(node["work_ids"], f"nodes[{index}].work_ids")
        _check_string_list(
            node["recovery_candidate_ids"],
            f"nodes[{index}].recovery_candidate_ids",
            maximum=4,
        )
        _check_number(node["estimated_cost_usd"], f"nodes[{index}].estimated_cost_usd")

    edges = payload["edges"]
    if not isinstance(edges, list) or len(edges) > CLAUDE_RED_TEAM_MAX_ITEMS:
        raise ValueError("edges must be a bounded list")
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping) or set(edge) != _INTERNAL_EDGE_KEYS:
            raise ValueError(f"edges[{index}] has missing or extra fields")
        _check_id(edge["source"], f"edges[{index}].source")
        _check_id(edge["target"], f"edges[{index}].target")


def _validate_external(payload: Mapping[str, Any]) -> None:
    if set(payload) != _EXTERNAL_TOP_LEVEL_KEYS:
        raise ValueError("external Claude review input has missing or extra fields")
    _check_digest(payload["task_digest"], "task_digest")
    _check_digest(payload["information_digest"], "information_digest")
    _check_id(payload["contract_kind"], "contract_kind")
    claims = payload["claims"]
    if not isinstance(claims, list) or not claims or len(claims) > CLAUDE_RED_TEAM_MAX_ITEMS:
        raise ValueError("claims must be a non-empty bounded list")
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping) or set(claim) != _EXTERNAL_CLAIM_KEYS:
            raise ValueError(f"claims[{index}] has missing or extra fields")
        for field in ("claim_id", "label", "source"):
            _check_id(claim[field], f"claims[{index}].{field}")
        text = claim["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 240:
            raise ValueError(f"claims[{index}].text is empty or oversized")
        _check_string_list(claim["quantity_tokens"], f"claims[{index}].quantity_tokens")
        _check_string_list(claim["location_tokens"], f"claims[{index}].location_tokens")


def canonical_review_input(scope: RedTeamScope | str, payload: Mapping[str, Any]) -> str:
    scope = RedTeamScope(scope)
    if not isinstance(payload, Mapping):
        raise ValueError("Claude red-team input must be an object")
    _check_tree(payload)
    if scope is RedTeamScope.INTERNAL_SELECTION:
        _validate_internal(payload)
    else:
        _validate_external(payload)
    rendered = json.dumps(
        {"scope": scope.value, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(rendered) > CLAUDE_RED_TEAM_MAX_INPUT_CHARS:
        raise ValueError("Claude red-team input exceeds the hard character limit")
    return rendered


def verdict_json_schema(scope: RedTeamScope | str) -> dict[str, Any]:
    scope = RedTeamScope(scope)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": f"claude_{scope.value}_verdict",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "decision": {"type": "string", "enum": ["APPROVE", "REJECT"]},
                    "codes": {
                        "type": "array",
                        "maxItems": CLAUDE_RED_TEAM_MAX_CODES,
                        "uniqueItems": True,
                        "items": {"type": "string", "enum": sorted(_codes(scope))},
                    },
                    "targets": {
                        "type": "array",
                        "maxItems": CLAUDE_RED_TEAM_MAX_TARGETS,
                        "uniqueItems": True,
                        "items": {"type": "string", "maxLength": 96},
                    },
                },
                "required": ["decision", "codes", "targets"],
            },
        },
    }


def build_claude_red_team_request(
    scope: RedTeamScope | str,
    payload: Mapping[str, Any],
    *,
    provider_slug: str = CLAUDE_RED_TEAM_PROVIDER,
) -> dict[str, Any]:
    scope = RedTeamScope(scope)
    _check_id(provider_slug, "provider_slug")
    user_content = canonical_review_input(scope, payload)
    return {
        "model": CLAUDE_RED_TEAM_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPTS[scope]},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "max_tokens": CLAUDE_RED_TEAM_MAX_OUTPUT_TOKENS,
        "reasoning": {
            "effort": CLAUDE_RED_TEAM_REASONING_EFFORT,
            "exclude": True,
        },
        "verbosity": "low",
        "response_format": verdict_json_schema(scope),
        "provider": {
            "only": [provider_slug],
            "order": [provider_slug],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
    }


def parse_claude_red_team_verdict(
    scope: RedTeamScope | str,
    text: str,
) -> dict[str, Any]:
    scope = RedTeamScope(scope)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Claude red-team verdict is empty")
    if len(text) > CLAUDE_RED_TEAM_MAX_OUTPUT_CHARS:
        raise ValueError("Claude red-team verdict exceeds the hard character limit")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Claude red-team verdict is not valid JSON") from exc
    if not isinstance(value, Mapping) or set(value) != {"decision", "codes", "targets"}:
        raise ValueError("Claude red-team verdict has missing or extra fields")
    decision = value["decision"]
    codes = value["codes"]
    targets = value["targets"]
    if decision not in {"APPROVE", "REJECT"}:
        raise ValueError("Claude red-team verdict decision is invalid")
    if (
        not isinstance(codes, list)
        or len(codes) > CLAUDE_RED_TEAM_MAX_CODES
        or len(codes) != len(set(codes))
        or any(code not in _codes(scope) for code in codes)
    ):
        raise ValueError("Claude red-team verdict codes are invalid")
    if (
        not isinstance(targets, list)
        or len(targets) > CLAUDE_RED_TEAM_MAX_TARGETS
        or len(targets) != len(set(targets))
    ):
        raise ValueError("Claude red-team verdict targets are invalid")
    for index, target in enumerate(targets):
        _check_id(target, f"targets[{index}]")
    if decision == "APPROVE" and (codes or targets):
        raise ValueError("APPROVE must not contain codes or targets")
    if decision == "REJECT" and not codes:
        raise ValueError("REJECT must contain at least one enumerated code")
    return {
        "decision": decision,
        "codes": list(codes),
        "targets": list(targets),
        "scope": scope.value,
        "model": CLAUDE_RED_TEAM_MODEL,
        "reviewer_role": "bounded-red-team-verdict-only",
    }


def forbidden_claude_capabilities() -> tuple[str, ...]:
    """Machine-readable proof of everything Claude is not authorized to do."""
    return (
        "select_experts",
        "replace_experts",
        "reorder_experts",
        "modify_execution_graph",
        "execute_task",
        "call_tools",
        "browse_network",
        "write_report",
        "rewrite_information",
        "emit_free_text_reasoning",
    )
