"""Fixed single-call Claude latest red-team advisory contract."""
from __future__ import annotations

import json
import math
import re
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping

CLAUDE_RED_TEAM_MODEL = "~anthropic/claude-opus-latest"
CLAUDE_RED_TEAM_PROVIDER = "anthropic"
CLAUDE_RED_TEAM_REASONING_EFFORT = "low"
CLAUDE_RED_TEAM_MAX_INPUT_CHARS = 12_000
CLAUDE_RED_TEAM_MAX_OUTPUT_CHARS = 4_000
CLAUDE_RED_TEAM_MAX_OUTPUT_TOKENS = 512
CLAUDE_RED_TEAM_MAX_ITEMS = 16
CLAUDE_RED_TEAM_MAX_SUGGESTIONS = 8
GPT_PROPOSAL_CALLS = 1
CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK = 1
GPT_SYNTHESIS_CALLS = 1
CLAUDE_RED_TEAM_GOVERNANCE_CALLS = 3

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.~:@/+-]{1,160}$")


class RedTeamScope(str, Enum):
    INTERNAL_SELECTION = "internal_selection"
    EXTERNAL_INFORMATION = "external_information"


INTERNAL_CODES = frozenset({
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
    "REVIEW_INPUT_INCOMPLETE",
})
EXTERNAL_CODES = frozenset({
    "UNSUPPORTED_FACT",
    "FACT_INFERENCE_MIXED",
    "QUANTITY_CONFLICT",
    "LOCATION_CONFLICT",
    "SOURCE_MISSING",
    "UNKNOWN_NOT_PRESERVED",
    "CONTRACT_VIOLATION",
    "INFORMATION_INCOMPLETE",
    "REVIEW_INPUT_INCOMPLETE",
})

INTERNAL_SELECTION_PROMPT = (
    "你是专家团中心的固定红队顾问。你只审查GPT已经提出的动态专家团组合，并给出具体、可执行、最小必要的修改意见。"
    "你不是批准者、否决者或门禁，不得输出APPROVE、REJECT、通过或不通过。你不得直接选择或执行专家，不得修改执行图，"
    "不得调用工具或浏览。你在每个任务中只执行一次。只返回严格JSON；没有修改意见时返回空suggestions。"
)
EXTERNAL_INFORMATION_PROMPT = (
    "你是专家团中心的固定信息红队顾问。你只审查输入信息的事实来源、数量、位置、未知项和交付合同，并给出具体、可执行、"
    "最小必要的修改意见。你不是批准者、否决者或门禁，不得输出APPROVE、REJECT、通过或不通过。你不得补充未经输入支持的事实，"
    "不得执行任务、调用工具或浏览。你在每个任务中只执行一次。只返回严格JSON；没有修改意见时返回空suggestions。"
)
INTERNAL_SELECTION_PROMPT_SHA256 = (
    "51cab7adb01591f7656970e5b5e04ac4a3c3aeef719f6a83de509e35976a134d"
)
EXTERNAL_INFORMATION_PROMPT_SHA256 = (
    "1a26944dca51aa620082dad9f8ea14abb6859bcb17f0958e8d5ff79ade6b6223"
)
_PROMPTS = {
    RedTeamScope.INTERNAL_SELECTION: (
        INTERNAL_SELECTION_PROMPT,
        INTERNAL_SELECTION_PROMPT_SHA256,
    ),
    RedTeamScope.EXTERNAL_INFORMATION: (
        EXTERNAL_INFORMATION_PROMPT,
        EXTERNAL_INFORMATION_PROMPT_SHA256,
    ),
}


def _codes(scope: RedTeamScope) -> frozenset[str]:
    return INTERNAL_CODES if scope is RedTeamScope.INTERNAL_SELECTION else EXTERNAL_CODES


def fixed_prompt(scope: RedTeamScope | str) -> str:
    prompt, expected = _PROMPTS[RedTeamScope(scope)]
    if sha256(prompt.encode("utf-8")).hexdigest() != expected:
        raise RuntimeError("Claude fixed red-team prompt integrity check failed")
    return prompt


def fixed_prompt_sha256(scope: RedTeamScope | str) -> str:
    scope = RedTeamScope(scope)
    fixed_prompt(scope)
    return _PROMPTS[scope][1]


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{field} must be a bounded identifier")
    return value


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} is outside the allowed range")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} is outside the allowed range")
    return number


def _id_list(value: Any, field: str, maximum: int = CLAUDE_RED_TEAM_MAX_ITEMS) -> None:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be a bounded list")
    for index, item in enumerate(value):
        _identifier(item, f"{field}[{index}]")


def _semantic_list(value: Any, field: str) -> None:
    if not isinstance(value, list) or len(value) > CLAUDE_RED_TEAM_MAX_ITEMS:
        raise ValueError(f"{field} must be a bounded list")
    for index, item in enumerate(value):
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 64
            or any(ord(character) < 32 for character in item)
        ):
            raise ValueError(f"{field}[{index}] is invalid")


def _validate_internal(payload: Mapping[str, Any]) -> None:
    expected = {
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
    if set(payload) != expected:
        raise ValueError("internal Claude review has missing or extra fields")
    _digest(payload["task_digest"], "task_digest")
    _digest(payload["proposal_digest"], "proposal_digest")
    total = _integer(payload["approved_total_calls"], "approved_total_calls", 4, 16)
    governance = _integer(
        payload["governance_calls_reserved"],
        "governance_calls_reserved",
        CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
        CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
    )
    recovery = _integer(
        payload["approved_recovery_calls"],
        "approved_recovery_calls",
        0,
        total - governance - 1,
    )
    if payload["cost_anomaly_usd"] is not None:
        if _number(payload["cost_anomaly_usd"], "cost_anomaly_usd") <= 0:
            raise ValueError("cost_anomaly_usd must be positive")
    _id_list(payload["required_work"], "required_work")
    nodes = payload["nodes"]
    maximum_nodes = total - governance - recovery
    if (
        not isinstance(nodes, list)
        or not nodes
        or len(nodes) > min(CLAUDE_RED_TEAM_MAX_ITEMS, maximum_nodes)
    ):
        raise ValueError("nodes exceed expert-call capacity")
    node_fields = {
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
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping) or set(node) != node_fields:
            raise ValueError(f"nodes[{index}] has missing or extra fields")
        for field in (
            "node_id",
            "candidate_id",
            "model",
            "company",
            "provider",
            "contract_kind",
        ):
            _identifier(node[field], f"nodes[{index}].{field}")
        _id_list(node["work_ids"], f"nodes[{index}].work_ids")
        _id_list(
            node["recovery_candidate_ids"],
            f"nodes[{index}].recovery_candidate_ids",
            maximum=4,
        )
        _number(node["estimated_cost_usd"], f"nodes[{index}].estimated_cost_usd")
    edges = payload["edges"]
    if not isinstance(edges, list) or len(edges) > CLAUDE_RED_TEAM_MAX_ITEMS:
        raise ValueError("edges must be bounded")
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping) or set(edge) != {"source", "target"}:
            raise ValueError(f"edges[{index}] has missing or extra fields")
        _identifier(edge["source"], f"edges[{index}].source")
        _identifier(edge["target"], f"edges[{index}].target")


def _validate_external(payload: Mapping[str, Any]) -> None:
    expected = {
        "task_digest",
        "information_digest",
        "contract_kind",
        "claims",
    }
    if set(payload) != expected:
        raise ValueError("external Claude review has missing or extra fields")
    _digest(payload["task_digest"], "task_digest")
    _digest(payload["information_digest"], "information_digest")
    _identifier(payload["contract_kind"], "contract_kind")
    claims = payload["claims"]
    if (
        not isinstance(claims, list)
        or not claims
        or len(claims) > CLAUDE_RED_TEAM_MAX_ITEMS
    ):
        raise ValueError("claims must be a non-empty bounded list")
    fields = {
        "claim_id",
        "label",
        "source",
        "text",
        "quantity_tokens",
        "location_tokens",
    }
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping) or set(claim) != fields:
            raise ValueError(f"claims[{index}] has missing or extra fields")
        for field in ("claim_id", "label", "source"):
            _identifier(claim[field], f"claims[{index}].{field}")
        text = claim["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 240:
            raise ValueError(f"claims[{index}].text is invalid")
        _semantic_list(claim["quantity_tokens"], f"claims[{index}].quantity_tokens")
        _semantic_list(claim["location_tokens"], f"claims[{index}].location_tokens")


def canonical_review_input(
    scope: RedTeamScope | str,
    payload: Mapping[str, Any],
) -> str:
    scope = RedTeamScope(scope)
    if not isinstance(payload, Mapping):
        raise ValueError("Claude red-team input must be an object")
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
        raise ValueError("Claude red-team input exceeds hard limit")
    return rendered


def advice_json_schema(scope: RedTeamScope | str) -> dict[str, Any]:
    scope = RedTeamScope(scope)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": f"claude_{scope.value}_advice",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "suggestions": {
                        "type": "array",
                        "maxItems": CLAUDE_RED_TEAM_MAX_SUGGESTIONS,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "code": {
                                    "type": "string",
                                    "enum": sorted(_codes(scope)),
                                },
                                "target": {
                                    "type": "string",
                                    "pattern": "^[A-Za-z0-9_.~:@/+-]{1,160}$",
                                },
                                "change": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 240,
                                },
                            },
                            "required": ["code", "target", "change"],
                        },
                    },
                },
                "required": ["suggestions"],
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
    _identifier(provider_slug, "provider_slug")
    return {
        "model": CLAUDE_RED_TEAM_MODEL,
        "messages": [
            {"role": "system", "content": fixed_prompt(scope)},
            {"role": "user", "content": canonical_review_input(scope, payload)},
        ],
        "temperature": 0,
        "max_tokens": CLAUDE_RED_TEAM_MAX_OUTPUT_TOKENS,
        "reasoning": {
            "effort": CLAUDE_RED_TEAM_REASONING_EFFORT,
            "exclude": True,
        },
        "response_format": advice_json_schema(scope),
        "provider": {
            "only": [provider_slug],
            "order": [provider_slug],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
    }


def parse_claude_red_team_advice(
    scope: RedTeamScope | str,
    text: str,
) -> dict[str, Any]:
    scope = RedTeamScope(scope)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Claude red-team advice is empty")
    if len(text) > CLAUDE_RED_TEAM_MAX_OUTPUT_CHARS:
        raise ValueError("Claude red-team advice exceeds hard limit")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Claude red-team advice is not JSON") from exc
    if not isinstance(value, Mapping) or set(value) != {"suggestions"}:
        raise ValueError("Claude advice has missing or extra fields")
    suggestions = value["suggestions"]
    if not isinstance(suggestions, list) or len(suggestions) > CLAUDE_RED_TEAM_MAX_SUGGESTIONS:
        raise ValueError("Claude suggestions are invalid")
    normalized: list[dict[str, str]] = []
    for index, suggestion in enumerate(suggestions):
        if not isinstance(suggestion, Mapping) or set(suggestion) != {"code", "target", "change"}:
            raise ValueError(f"suggestions[{index}] has missing or extra fields")
        code = str(suggestion["code"])
        if code not in _codes(scope):
            raise ValueError(f"suggestions[{index}].code is invalid")
        target = _identifier(suggestion["target"], f"suggestions[{index}].target")
        change = suggestion["change"]
        if (
            not isinstance(change, str)
            or not change.strip()
            or len(change) > 240
            or any(ord(character) < 32 for character in change)
        ):
            raise ValueError(f"suggestions[{index}].change is invalid")
        normalized.append({"code": code, "target": target, "change": change.strip()})
    return {
        "suggestions": normalized,
        "scope": scope.value,
        "model": CLAUDE_RED_TEAM_MODEL,
        "reviewer_role": "advisory-red-team-only",
        "prompt_sha256": fixed_prompt_sha256(scope),
        "maximum_calls_per_task": 1,
        "hard_gate": False,
        "approval_authority": False,
    }


def forbidden_claude_capabilities() -> tuple[str, ...]:
    return (
        "approve_proposal",
        "reject_proposal",
        "block_execution",
        "select_experts",
        "execute_task",
        "call_tools",
        "browse_network",
        "write_final_report",
        "repeat_red_team_review",
    )
