"""Fixed GPT proposal/synthesis requests for the expert-team center.

GPT chooses the dynamic expert composition directly from the current task
resources and exact catalog rows. No local scoring or solver participates.
"""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Mapping, Sequence

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
PROPOSAL_PROMPT_SHA256 = "53d3a37962466df8df8bc47d94da75450e1b81cb5c422ea83a4808bcdac939a5"
SYNTHESIS_PROMPT_SHA256 = "079b7773f4176bf9362fe863c1fd913d1d3dc4920a652407fd72082afce809fd"


class GPTSelectorError(RuntimeError):
    """Fail-closed GPT selection/synthesis protocol error."""


def _proposal_schema(name: str) -> dict[str, Any]:
    node = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "node_id": {
                "type": "string",
                "pattern": "^[A-Za-z0-9_.:-]{1,64}$",
            },
            "work_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9_.:-]{1,96}$",
                },
            },
            "role": {
                "type": "string",
                "minLength": 1,
                "maxLength": 160,
            },
            "model": {
                "type": "string",
                "pattern": "^[A-Za-z0-9_.~:@/+-]{1,160}$",
            },
            "provider": {
                "type": "string",
                "pattern": "^[A-Za-z0-9_.:@/+-]{1,160}$",
            },
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
                "uniqueItems": True,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "model": {
                            "type": "string",
                            "pattern": "^[A-Za-z0-9_.~:@/+-]{1,160}$",
                        },
                        "provider": {
                            "type": "string",
                            "pattern": "^[A-Za-z0-9_.:@/+-]{1,160}$",
                        },
                    },
                    "required": ["model", "provider"],
                },
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
            "source": {
                "type": "string",
                "pattern": "^[A-Za-z0-9_.:-]{1,64}$",
            },
            "target": {
                "type": "string",
                "pattern": "^[A-Za-z0-9_.:-]{1,64}$",
            },
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
                    "interpretation_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9_.:-]{1,96}$",
                    },
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
                        "items": {
                            "type": "string",
                            "pattern": "^[A-Za-z0-9_.:-]{1,64}$",
                        },
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


def _fixed_prompt(prompt: str, expected_sha256: str) -> str:
    actual = sha256(prompt.encode("utf-8")).hexdigest()
    if actual != expected_sha256:
        raise GPTSelectorError("fixed GPT selector prompt integrity failure")
    return prompt


def _canonical_user_content(value: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    if len(rendered) > GPT_MAX_INPUT_CHARS:
        raise GPTSelectorError("GPT selector input exceeds hard character limit")
    return rendered


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
    prompt = _fixed_prompt(PROPOSAL_PROMPT, PROPOSAL_PROMPT_SHA256)
    user = _canonical_user_content(
        {
            "task": str(task),
            "resources": resources,
            "catalog": catalog,
            "hard_limits": {
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
                "governance_companies_forbidden_for_experts": [
                    "openai",
                    "anthropic",
                ],
                "tools_allowed": False,
                "provider_fallback_allowed": False,
            },
        }
    )
    return {
        "model": GPT_SELECTOR_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": GPT_MAX_OUTPUT_TOKENS,
        "reasoning": {"effort": "high", "exclude": True},
        "verbosity": "low",
        "response_format": _proposal_schema("gpt_expert_team_proposal"),
        "provider": {
            "only": [GPT_SELECTOR_PROVIDER],
            "order": [GPT_SELECTOR_PROVIDER],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        "governance_policy": {
            "role": "dynamic-expert-team-proposer",
            "local_scoring_used": False,
            "optimizer_used": False,
            "tools_allowed": False,
            "prompt_sha256": PROPOSAL_PROMPT_SHA256,
        },
    }


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
    prompt = _fixed_prompt(SYNTHESIS_PROMPT, SYNTHESIS_PROMPT_SHA256)
    user = _canonical_user_content(
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
            "hard_limits": {
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
                "governance_companies_forbidden_for_experts": [
                    "openai",
                    "anthropic",
                ],
                "tools_allowed": False,
                "provider_fallback_allowed": False,
                "claude_second_review_allowed": False,
            },
        }
    )
    return {
        "model": GPT_SELECTOR_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": GPT_MAX_OUTPUT_TOKENS,
        "reasoning": {"effort": "high", "exclude": True},
        "verbosity": "low",
        "response_format": _proposal_schema("gpt_expert_team_synthesis"),
        "provider": {
            "only": [GPT_SELECTOR_PROVIDER],
            "order": [GPT_SELECTOR_PROVIDER],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        "governance_policy": {
            "role": "single-pass-post-red-team-synthesis",
            "maximum_calls": 1,
            "claude_second_review_allowed": False,
            "model_loop_allowed": False,
            "tools_allowed": False,
            "prompt_sha256": SYNTHESIS_PROMPT_SHA256,
        },
    }


def parse_proposal(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise GPTSelectorError("GPT proposal is empty")
    if len(text) > GPT_MAX_OUTPUT_CHARS:
        raise GPTSelectorError("GPT proposal exceeds output character limit")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GPTSelectorError("GPT proposal is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise GPTSelectorError("GPT proposal must be an object")
    required = {"interpretation_id", "nodes", "edges", "final_nodes"}
    if set(value) != required:
        raise GPTSelectorError("GPT proposal has missing or extra top-level fields")
    nodes = value.get("nodes")
    edges = value.get("edges")
    final_nodes = value.get("final_nodes")
    if not isinstance(nodes, list) or not nodes or len(nodes) > GPT_MAX_NODES:
        raise GPTSelectorError("GPT proposal nodes are invalid")
    if not isinstance(edges, list) or len(edges) > GPT_MAX_EDGES:
        raise GPTSelectorError("GPT proposal edges are invalid")
    if not isinstance(final_nodes, list) or not final_nodes:
        raise GPTSelectorError("GPT proposal final_nodes are invalid")
    return json.loads(
        json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)
    )


def proposal_sha256(value: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(rendered.encode("utf-8")).hexdigest()
