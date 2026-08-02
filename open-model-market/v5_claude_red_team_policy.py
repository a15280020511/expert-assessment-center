"""Fixed single-call Claude Opus latest red-team advisory contract.

Claude performs one bounded review that covers both the GPT-authored expert
composition and the task/evidence boundary. It only returns modification
advice. It cannot approve, reject, block, select, execute, browse, or repeat.
"""
from __future__ import annotations

import json
import math
import re
from hashlib import sha256
from typing import Any, Mapping

CLAUDE_RED_TEAM_MODEL = "~anthropic/claude-opus-latest"
CLAUDE_RED_TEAM_PROVIDER = "anthropic"
CLAUDE_RED_TEAM_REASONING_EFFORT = "low"
CLAUDE_RED_TEAM_MAX_INPUT_CHARS = 48_000
CLAUDE_RED_TEAM_MAX_TASK_CHARS = 20_000
CLAUDE_RED_TEAM_MAX_OUTPUT_CHARS = 4_000
CLAUDE_RED_TEAM_MAX_OUTPUT_TOKENS = 512
CLAUDE_RED_TEAM_MAX_ITEMS = 32
CLAUDE_RED_TEAM_MAX_EDGES = 64
CLAUDE_RED_TEAM_MAX_SUGGESTIONS = 8
GPT_PROPOSAL_CALLS = 1
CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK = 1
GPT_SYNTHESIS_CALLS = 1
CLAUDE_RED_TEAM_GOVERNANCE_CALLS = 3
RED_TEAM_SCOPE = "unified_selection_and_information"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.~:@/+-]{1,160}$")

RED_TEAM_CODES = frozenset(
    {
        "UNKNOWN_CANDIDATE",
        "WORK_UNCOVERED",
        "WORK_DUPLICATED",
        "WORK_DEPENDENCY_INVALID",
        "DUPLICATE_COMPANY",
        "CALL_LIMIT_EXCEEDED",
        "RECOVERY_LIMIT_EXCEEDED",
        "COST_LIMIT_EXCEEDED",
        "PROVIDER_UNLOCKED",
        "TOOL_PERMISSION_PRESENT",
        "GRAPH_INVALID",
        "CONTRACT_MISMATCH",
        "ROLE_MISMATCH",
        "UNSUPPORTED_FACT",
        "FACT_INFERENCE_MIXED",
        "QUANTITY_CONFLICT",
        "LOCATION_CONFLICT",
        "SOURCE_MISSING",
        "UNKNOWN_NOT_PRESERVED",
        "CONTRACT_VIOLATION",
        "INFORMATION_INCOMPLETE",
        "REVIEW_INPUT_INCOMPLETE",
    }
)

UNIFIED_RED_TEAM_PROMPT = (
    "你是专家团中心唯一且固定的Claude红队顾问。你每个任务只执行一次，同时审查两部分："
    "第一，GPT已经提出的任务拆解、专家角色、模型、Provider、恢复顺序和执行图；第二，原始任务与"
    "输入证据中的事实来源、数量、位置、未知项、推断边界和用户交付合同。你只给出具体、可执行、"
    "最小必要的修改意见。你不是批准者、否决者或门禁，不得输出APPROVE、REJECT、通过、不通过或"
    "任何执行许可；不得直接选择专家、修改执行图、执行任务、写最终报告、调用工具或浏览；不得要求"
    "第二次复审。只返回严格JSON；没有修改意见时返回空suggestions。"
)
UNIFIED_RED_TEAM_PROMPT_SHA256 = sha256(
    UNIFIED_RED_TEAM_PROMPT.encode("utf-8")
).hexdigest()


def fixed_prompt() -> str:
    if sha256(UNIFIED_RED_TEAM_PROMPT.encode("utf-8")).hexdigest() != (
        UNIFIED_RED_TEAM_PROMPT_SHA256
    ):
        raise RuntimeError("Claude fixed red-team prompt integrity check failed")
    return UNIFIED_RED_TEAM_PROMPT


def fixed_prompt_sha256() -> str:
    fixed_prompt()
    return UNIFIED_RED_TEAM_PROMPT_SHA256


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{field} must be a bounded identifier")
    return value


def _text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{field} is invalid")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field} is invalid")
    if any(ord(character) < 32 and character not in "\n\t\r" for character in value):
        raise ValueError(f"{field} contains control characters")
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


def _id_list(value: Any, field: str, maximum: int) -> None:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be a bounded list")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicates")
    for index, item in enumerate(value):
        _identifier(item, f"{field}[{index}]")


def _text_list(value: Any, field: str, maximum: int, item_maximum: int) -> None:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be a bounded list")
    for index, item in enumerate(value):
        _text(item, f"{field}[{index}]", item_maximum)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _validate_payload_header(payload: Mapping[str, Any]) -> tuple[int, int, int]:
    expected = {
        "task_digest",
        "proposal_digest",
        "approved_total_calls",
        "governance_calls_reserved",
        "approved_recovery_calls",
        "cost_anomaly_usd",
        "task_excerpt",
        "task_characters",
        "task_truncated",
        "task_constraints",
        "explicit_delivery_contract",
        "work_items",
        "nodes",
        "edges",
    }
    if set(payload) != expected:
        raise ValueError("Claude unified review has missing or extra fields")
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
    if recovery >= total - governance:
        raise ValueError("approved recovery calls leave no initial expert call")
    if payload["cost_anomaly_usd"] is not None:
        if _number(payload["cost_anomaly_usd"], "cost_anomaly_usd") <= 0:
            raise ValueError("cost_anomaly_usd must be positive")
    task_excerpt = _text(
        payload["task_excerpt"],
        "task_excerpt",
        CLAUDE_RED_TEAM_MAX_TASK_CHARS,
    )
    task_characters = _integer(
        payload["task_characters"],
        "task_characters",
        len(task_excerpt),
        2_000_000,
    )
    if not isinstance(payload["task_truncated"], bool):
        raise ValueError("task_truncated must be boolean")
    if payload["task_truncated"] != (task_characters > len(task_excerpt)):
        raise ValueError("task_truncated does not match task length")
    constraints = _mapping(payload["task_constraints"], "task_constraints")
    contract = _mapping(
        payload["explicit_delivery_contract"],
        "explicit_delivery_contract",
    )
    if len(json.dumps(constraints, ensure_ascii=False, default=str)) > 8_000:
        raise ValueError("task_constraints are oversized")
    if len(json.dumps(contract, ensure_ascii=False, default=str)) > 8_000:
        raise ValueError("explicit_delivery_contract is oversized")
    return total, governance, recovery


def _validate_work_item(work: Any, index: int) -> str:
    work_fields = {"work_id", "objective", "dependencies", "required_outputs"}
    if not isinstance(work, Mapping) or set(work) != work_fields:
        raise ValueError(f"work_items[{index}] has missing or extra fields")
    work_id = _identifier(work["work_id"], f"work_items[{index}].work_id")
    _text(work["objective"], f"work_items[{index}].objective", 320)
    _id_list(
        work["dependencies"],
        f"work_items[{index}].dependencies",
        CLAUDE_RED_TEAM_MAX_ITEMS,
    )
    _text_list(
        work["required_outputs"],
        f"work_items[{index}].required_outputs",
        16,
        160,
    )
    return work_id


def _validate_work_items(payload: Mapping[str, Any]) -> set[str]:
    work_items = payload["work_items"]
    if not isinstance(work_items, list):
        raise ValueError("work_items must be a non-empty bounded list")
    if not 1 <= len(work_items) <= CLAUDE_RED_TEAM_MAX_ITEMS:
        raise ValueError("work_items must be a non-empty bounded list")
    work_ids = [_validate_work_item(work, index) for index, work in enumerate(work_items)]
    if len(work_ids) != len(set(work_ids)):
        raise ValueError("work_items contain duplicate work ids")
    known_work = set(work_ids)
    for work in work_items:
        if not set(work["dependencies"]).issubset(known_work):
            raise ValueError("work dependency references unknown work")
    return known_work


def _validate_node(node: Any, index: int) -> None:
    node_fields = {
        "node_id",
        "candidate_id",
        "work_ids",
        "role",
        "functions",
        "model",
        "company",
        "provider",
        "estimated_cost_usd",
        "contract_kind",
        "recovery_candidate_ids",
    }
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
    _text(node["role"], f"nodes[{index}].role", 320)
    _id_list(node["work_ids"], f"nodes[{index}].work_ids", CLAUDE_RED_TEAM_MAX_ITEMS)
    functions = node["functions"]
    _text_list(functions, f"nodes[{index}].functions", 12, 96)
    if len(functions) != len(set(functions)):
        raise ValueError(f"nodes[{index}].functions contains duplicates")
    _id_list(
        node["recovery_candidate_ids"],
        f"nodes[{index}].recovery_candidate_ids",
        4,
    )
    _number(node["estimated_cost_usd"], f"nodes[{index}].estimated_cost_usd")


def _validate_nodes(payload: Mapping[str, Any], maximum_nodes: int) -> None:
    nodes = payload["nodes"]
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("nodes exceed expert-call capacity")
    if len(nodes) > min(CLAUDE_RED_TEAM_MAX_ITEMS, maximum_nodes):
        raise ValueError("nodes exceed expert-call capacity")
    for index, node in enumerate(nodes):
        _validate_node(node, index)


def _validate_edges(payload: Mapping[str, Any]) -> None:
    edges = payload["edges"]
    if not isinstance(edges, list) or len(edges) > CLAUDE_RED_TEAM_MAX_EDGES:
        raise ValueError("edges must be bounded")
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping) or set(edge) != {"source", "target"}:
            raise ValueError(f"edges[{index}] has missing or extra fields")
        _identifier(edge["source"], f"edges[{index}].source")
        _identifier(edge["target"], f"edges[{index}].target")


def _validate_payload(payload: Mapping[str, Any]) -> None:
    total, governance, recovery = _validate_payload_header(payload)
    _validate_work_items(payload)
    _validate_nodes(payload, total - governance - recovery)
    _validate_edges(payload)


def canonical_review_input(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError("Claude red-team input must be an object")
    _validate_payload(payload)
    rendered = json.dumps(
        {"scope": RED_TEAM_SCOPE, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(rendered) > CLAUDE_RED_TEAM_MAX_INPUT_CHARS:
        raise ValueError("Claude red-team input exceeds hard limit")
    return rendered


def advice_json_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "claude_unified_red_team_advice",
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
                                    "enum": sorted(RED_TEAM_CODES),
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
    payload: Mapping[str, Any],
    *,
    provider_slug: str = CLAUDE_RED_TEAM_PROVIDER,
) -> dict[str, Any]:
    _identifier(provider_slug, "provider_slug")
    return {
        "model": CLAUDE_RED_TEAM_MODEL,
        "messages": [
            {"role": "system", "content": fixed_prompt()},
            {"role": "user", "content": canonical_review_input(payload)},
        ],
        "temperature": 0,
        "max_tokens": CLAUDE_RED_TEAM_MAX_OUTPUT_TOKENS,
        "reasoning": {
            "effort": CLAUDE_RED_TEAM_REASONING_EFFORT,
            "exclude": True,
        },
        "response_format": advice_json_schema(),
        "provider": {
            "only": [provider_slug],
            "order": [provider_slug],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
    }


def parse_claude_red_team_advice(text: str) -> dict[str, Any]:
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
        if code not in RED_TEAM_CODES:
            raise ValueError(f"suggestions[{index}].code is invalid")
        target = _identifier(suggestion["target"], f"suggestions[{index}].target")
        change = _text(suggestion["change"], f"suggestions[{index}].change", 240)
        normalized.append(
            {"code": code, "target": target, "change": change.strip()}
        )
    return {
        "suggestions": normalized,
        "scope": RED_TEAM_SCOPE,
        "model": CLAUDE_RED_TEAM_MODEL,
        "reviewer_role": "advisory-red-team-only",
        "prompt_sha256": fixed_prompt_sha256(),
        "maximum_calls_per_task": 1,
        "hard_gate": False,
        "approval_authority": False,
        "covers_internal_selection": True,
        "covers_external_information": True,
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
