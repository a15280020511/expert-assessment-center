"""Fixed GPT-latest proposal and one-time synthesis protocol."""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Mapping

GPT_SELECTOR_MODEL = "~openai/gpt-latest"
GPT_SELECTOR_PROVIDER = "openai"
GPT_MAX_INPUT_CHARS = 360_000
GPT_MAX_OUTPUT_CHARS = 48_000
GPT_MAX_OUTPUT_TOKENS = 8_000
GPT_MAX_NODES = 13
GPT_MAX_EDGES = 64
GPT_MAX_RECOVERY_PER_NODE = 2

PROPOSAL_PROMPT = (
    "你是专家团中心唯一的动态专家编组器。根据当前任务、原子工作、硬约束和精确模型端点目录，"
    "直接提出最小充分的专家执行图。不得使用固定席位、固定职业、固定权重、评分公式、贪心、"
    "Pareto、CP-SAT或任何预设组合。只能选择目录中存在的model+provider。专家公司必须彼此不同，"
    "且不得选择OpenAI或Anthropic，因为它们已用于治理链。每个节点必须覆盖明确工作，Provider必须"
    "单锁且禁止fallback，专家禁止工具。只输出严格JSON，不解释，不写报告。"
)
SYNTHESIS_PROMPT = (
    "你是专家团中心的一次性综合器。你只根据初始GPT提案和Claude一次红队返回的枚举codes、targets"
    "修正专家组合。不得再次调用或请求Claude，不得循环，不得添加目录外模型，不得绕过硬约束。"
    "只输出一份最终严格JSON提案，不解释，不写报告。"
)
PROPOSAL_PROMPT_SHA256 = (
    "53d3a37962466df8df8bc47d94da75450e1b81cb5c422ea83a4808bcdac939a5"
)
SYNTHESIS_PROMPT_SHA256 = (
    "079b7773f4176bf9362fe863c1fd913d1d3dc4920a652407fd72082afce809fd"
)


class GPTSelectorError(RuntimeError):
    pass


def _fixed(prompt: str, expected: str) -> str:
    if sha256(prompt.encode("utf-8")).hexdigest() != expected:
        raise GPTSelectorError("fixed GPT selector prompt integrity failure")
    return prompt


def _schema(name: str) -> dict[str, Any]:
    identifier = {
        "type": "string",
        "pattern": "^[A-Za-z0-9_.~:@/+-]{1,160}$",
    }
    node_id = {
        "type": "string",
        "pattern": "^[A-Za-z0-9_.:-]{1,64}$",
    }
    work_id = {
        "type": "string",
        "pattern": "^[A-Za-z0-9_.:-]{1,96}$",
    }
    recovery = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "model": identifier,
            "provider": identifier,
        },
        "required": ["model", "provider"],
    }
    node = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "node_id": node_id,
            "work_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "uniqueItems": True,
                "items": work_id,
            },
            "role": {"type": "string", "minLength": 1, "maxLength": 160},
            "model": identifier,
            "provider": identifier,
            "reasoning_effort": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "max_output_tokens": {
                "type": "integer",
                "minimum": 256,
                "maximum": 32768,
            },
            "recovery": {
                "type": "array",
                "maxItems": GPT_MAX_RECOVERY_PER_NODE,
                "items": recovery,
            },
        },
        "required": [
            "node_id",
            "work_ids",
            "role",
            "model",
            "provider",
            "reasoning_effort",
            "max_output_tokens",
            "recovery",
        ],
    }
    edge = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source": node_id,
            "target": node_id,
            "relation_type": {
                "type": "string",
                "enum": [
                    "dependency",
                    "review",
                    "adversarial",
                    "supplement",
                    "correction",
                    "comparison",
                    "synthesis",
                    "adjudication",
                    "formatting",
                ],
            },
        },
        "required": ["source", "target", "relation_type"],
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "interpretation_id": work_id,
                    "nodes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": GPT_MAX_NODES,
                        "items": node,
                    },
                    "edges": {
                        "type": "array",
                        "maxItems": GPT_MAX_EDGES,
                        "items": edge,
                    },
                    "final_nodes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": GPT_MAX_NODES,
                        "uniqueItems": True,
                        "items": node_id,
                    },
                },
                "required": [
                    "interpretation_id",
                    "nodes",
                    "edges",
                    "final_nodes",
                ],
            },
        },
    }


def _content(value: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    if len(rendered) > GPT_MAX_INPUT_CHARS:
        raise GPTSelectorError("GPT selector input exceeds hard limit")
    return rendered


def _request(prompt: str, prompt_hash: str, name: str, user: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model": GPT_SELECTOR_MODEL,
        "messages": [
            {"role": "system", "content": _fixed(prompt, prompt_hash)},
            {"role": "user", "content": _content(user)},
        ],
        "temperature": 0,
        "max_tokens": GPT_MAX_OUTPUT_TOKENS,
        "reasoning": {"effort": "high", "exclude": True},
        "response_format": _schema(name),
        "provider": {
            "only": [GPT_SELECTOR_PROVIDER],
            "order": [GPT_SELECTOR_PROVIDER],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
    }


def _hard_limits(
    *,
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    return {
        "approved_total_calls": int(approved_total_calls),
        "governance_calls_reserved": int(governance_calls_reserved),
        "approved_recovery_calls": int(approved_recovery_calls),
        "maximum_expert_initial_calls": (
            int(approved_total_calls)
            - int(governance_calls_reserved)
            - int(approved_recovery_calls)
        ),
        "cost_anomaly_usd": cost_anomaly_usd,
        "distinct_expert_companies": True,
        "governance_companies_forbidden_for_experts": ["openai", "anthropic"],
        "tools_allowed": False,
        "provider_fallback_allowed": False,
    }


def build_proposal_request(
    *,
    task: str,
    resources: Mapping[str, Any],
    catalog: Mapping[str, Any],
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    return _request(
        PROPOSAL_PROMPT,
        PROPOSAL_PROMPT_SHA256,
        "gpt_expert_team_proposal",
        {
            "task": str(task),
            "resources": resources,
            "catalog": catalog,
            "hard_limits": _hard_limits(
                approved_total_calls=approved_total_calls,
                governance_calls_reserved=governance_calls_reserved,
                approved_recovery_calls=approved_recovery_calls,
                cost_anomaly_usd=cost_anomaly_usd,
            ),
        },
    )


def build_synthesis_request(
    *,
    task: str,
    initial_proposal: Mapping[str, Any],
    claude_verdict: Mapping[str, Any],
    resources: Mapping[str, Any],
    catalog: Mapping[str, Any],
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    hard = _hard_limits(
        approved_total_calls=approved_total_calls,
        governance_calls_reserved=governance_calls_reserved,
        approved_recovery_calls=approved_recovery_calls,
        cost_anomaly_usd=cost_anomaly_usd,
    )
    hard["claude_second_review_allowed"] = False
    return _request(
        SYNTHESIS_PROMPT,
        SYNTHESIS_PROMPT_SHA256,
        "gpt_expert_team_synthesis",
        {
            "task": str(task),
            "initial_proposal": initial_proposal,
            "claude_red_team_verdict": {
                "decision": claude_verdict.get("decision"),
                "codes": list(claude_verdict.get("codes") or []),
                "targets": list(claude_verdict.get("targets") or []),
            },
            "resources": resources,
            "catalog": catalog,
            "hard_limits": hard,
        },
    )


def parse_proposal(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise GPTSelectorError("GPT proposal is empty")
    if len(text) > GPT_MAX_OUTPUT_CHARS:
        raise GPTSelectorError("GPT proposal exceeds hard limit")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GPTSelectorError("GPT proposal is not valid JSON") from exc
    required = {"interpretation_id", "nodes", "edges", "final_nodes"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise GPTSelectorError("GPT proposal has missing or extra fields")
    if not isinstance(value["nodes"], list) or not value["nodes"]:
        raise GPTSelectorError("GPT proposal has no nodes")
    if len(value["nodes"]) > GPT_MAX_NODES:
        raise GPTSelectorError("GPT proposal exceeds node limit")
    if not isinstance(value["edges"], list) or len(value["edges"]) > GPT_MAX_EDGES:
        raise GPTSelectorError("GPT proposal edges are invalid")
    if not isinstance(value["final_nodes"], list) or not value["final_nodes"]:
        raise GPTSelectorError("GPT proposal final nodes are invalid")
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def proposal_sha256(value: Mapping[str, Any]) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
