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
_TARGET_RE = re.compile(
    r"^(?:task|contract|work:[A-Za-z0-9_.:-]{1,96}|"
    r"node:[A-Za-z0-9_.:-]{1,64}|"
    r"edge:[A-Za-z0-9_.:-]{1,64}->[A-Za-z0-9_.:-]{1,64})$"
)

RED_TEAM_CODES = frozenset(
    {
        "UNKNOWN_CANDIDATE",
        "WORK_UNCOVERED",
        "WORK_DUPLICATED",
        "WORK_DEPENDENCY_INVALID",
        "DUPLICATE_COMPANY",
        "CALL_LIMIT_EXCEEDED",
        "RECOVERY_LIMIT_EXCEEDED",
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
        "INFORMATION_INCOMPLETE",
        "REVIEW_INPUT_INCOMPLETE",
    }
)

UNIFIED_RED_TEAM_PROMPT = (
    "你是专家团中心唯一且固定的Claude红队顾问，每个任务只审查一次。审查对象仅限输入payload，"
    "不得补充外部事实、目录外模型或未提供的能力。按以下优先级核对：第一，调用、恢复、费用、"
    "Provider单锁、禁用工具、初始与恢复专家公司全局不同等结构与安全约束；费用和Token仅是审计与优化建议，"
    "最终节点和用户交付合同；第三，事实来源、数量、位置、未知项、事实与推断边界。只对真实且尚未"
    "满足的缺陷给出意见；一条suggestion只处理一个缺陷，必须指向明确的task、contract、work_id、"
    "node_id或source->target边，修改动作应最小、可执行且不与其他意见重复或冲突。不得给一般性建议，"
    "不得重述已满足条件，不得直接改图、选择专家、执行任务、写报告、调用工具、浏览或要求复审。"
    "输入不足以安全判断时，只返回REVIEW_INPUT_INCOMPLETE意见并禁止猜测缺失内容。不得因费用或Token建议值"
    "否决方案、要求降级、删减必要交付或停止执行。"
    "你不是批准者、否决者或门禁，不得输出APPROVE、REJECT、通过、不通过或执行许可。只返回严格JSON；"
    "没有必要修改时返回空suggestions。"
)
UNIFIED_RED_TEAM_PROMPT_SHA256 = "db8ec1119fa4847e90c918088fc55e8aec5f2e8f6c93647dc64b7a2365c08932"


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
        "final_nodes",
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
        max(1, len(str(payload["task_excerpt"]))),
    )
    task_characters = payload["task_characters"]
    if (
        isinstance(task_characters, bool)
        or not isinstance(task_characters, int)
        or task_characters != len(task_excerpt)
    ):
        raise ValueError("task_characters must equal the complete task length")
    if payload["task_truncated"] is not False:
        raise ValueError("task_truncated must be false; local task truncation is forbidden")
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


def _validate_recovery_candidate(value: Any, field: str) -> str:
    expected = {
        "candidate_id",
        "model",
        "company",
        "provider",
        "estimated_cost_usd",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{field} has missing or extra fields")
    for name in ("candidate_id", "model", "company", "provider"):
        _identifier(value[name], f"{field}.{name}")
    _number(value["estimated_cost_usd"], f"{field}.estimated_cost_usd")
    return str(value["candidate_id"])


def _validate_node(node: Any, index: int) -> str:
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
        "recovery_candidates",
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
    recoveries = node["recovery_candidates"]
    if not isinstance(recoveries, list) or len(recoveries) > 4:
        raise ValueError(f"nodes[{index}].recovery_candidates must be bounded")
    recovery_ids = [
        _validate_recovery_candidate(
            value,
            f"nodes[{index}].recovery_candidates[{recovery_index}]",
        )
        for recovery_index, value in enumerate(recoveries)
    ]
    if len(recovery_ids) != len(set(recovery_ids)):
        raise ValueError(f"nodes[{index}].recovery_candidates contains duplicates")
    _number(node["estimated_cost_usd"], f"nodes[{index}].estimated_cost_usd")
    return str(node["node_id"])


def _validate_nodes(payload: Mapping[str, Any], maximum_nodes: int) -> set[str]:
    nodes = payload["nodes"]
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("nodes exceed expert-call capacity")
    if len(nodes) > min(CLAUDE_RED_TEAM_MAX_ITEMS, maximum_nodes):
        raise ValueError("nodes exceed expert-call capacity")
    node_ids = [_validate_node(node, index) for index, node in enumerate(nodes)]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("nodes contain duplicate node ids")
    return set(node_ids)


def _validate_edges(payload: Mapping[str, Any], known_nodes: set[str]) -> None:
    edges = payload["edges"]
    if not isinstance(edges, list) or len(edges) > CLAUDE_RED_TEAM_MAX_EDGES:
        raise ValueError("edges must be bounded")
    identities: list[tuple[str, str, str]] = []
    for index, edge in enumerate(edges):
        expected = {"source", "target", "relation_type"}
        if not isinstance(edge, Mapping) or set(edge) != expected:
            raise ValueError(f"edges[{index}] has missing or extra fields")
        source = _identifier(edge["source"], f"edges[{index}].source")
        target = _identifier(edge["target"], f"edges[{index}].target")
        relation = _identifier(
            edge["relation_type"],
            f"edges[{index}].relation_type",
        )
        if source not in known_nodes or target not in known_nodes or source == target:
            raise ValueError(f"edges[{index}] references invalid nodes")
        identities.append((source, target, relation))
    if len(identities) != len(set(identities)):
        raise ValueError("edges contain duplicates")


def _validate_final_nodes(payload: Mapping[str, Any], known_nodes: set[str]) -> None:
    final_nodes = payload["final_nodes"]
    _id_list(final_nodes, "final_nodes", CLAUDE_RED_TEAM_MAX_ITEMS)
    if not final_nodes or not set(final_nodes).issubset(known_nodes):
        raise ValueError("final_nodes contain unknown nodes or are empty")


def _validate_payload(payload: Mapping[str, Any]) -> None:
    total, governance, recovery = _validate_payload_header(payload)
    _validate_work_items(payload)
    known_nodes = _validate_nodes(payload, total - governance - recovery)
    _validate_edges(payload, known_nodes)
    _validate_final_nodes(payload, known_nodes)


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
                                    "pattern": _TARGET_RE.pattern,
                                    "minLength": 1,
                                    "maxLength": 160,
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
        target = _text(suggestion["target"], f"suggestions[{index}].target", 160).strip()
        if _TARGET_RE.fullmatch(target) is None:
            raise ValueError(f"suggestions[{index}].target is not an exact review target")
        change = _text(suggestion["change"], f"suggestions[{index}].change", 240).strip()
        normalized.append(
            {"code": code, "target": target, "change": change}
        )
    identities = [
        (row["code"], row["target"], row["change"])
        for row in normalized
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("Claude suggestions contain exact duplicates")
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
