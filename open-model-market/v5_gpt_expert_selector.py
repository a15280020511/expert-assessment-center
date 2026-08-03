"""Fixed GPT-latest task decomposition and expert selection protocol."""
from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any, Mapping

GPT_SELECTOR_MODEL = "~openai/gpt-latest"
GPT_SELECTOR_PROVIDER = "openai"
GPT_MAX_WORK_ITEMS = 32
GPT_MAX_NODES = 13
GPT_MAX_EDGES = 64
GPT_MAX_RECOVERY_PER_NODE = 2
GPT_MAX_FUNCTIONS_PER_NODE = 12
GPT_MAX_OUTPUT_FIELDS_PER_WORK = 16

GPT_IDENTIFIER_PATTERN = r"^[A-Za-z0-9_.~:@/+-]{1,160}$"
GPT_NODE_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
GPT_WORK_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,96}$"
GPT_RELATION_TYPES = (
    "dependency",
    "review",
    "adversarial",
    "supplement",
    "correction",
    "comparison",
    "synthesis",
    "adjudication",
    "formatting",
)

PROPOSAL_PROMPT = (
    "你是专家团中心唯一的动态任务拆解与专家编组器。你必须直接阅读原始任务和硬约束，"
    "自行生成最小充分的work_items、依赖关系和专家执行图；不存在本地预先生成的原子工作、"
    "复杂度评分、领域标签、能力权重或固定职业。不得使用固定席位、固定权重、评分公式、贪心、"
    "Pareto、CP-SAT或任何预设组合。只能选择目录中存在的model+provider。专家公司必须彼此不同，"
    "且不得选择OpenAI或Anthropic，因为它们已用于治理链。目录可能采用columns定义加数组行的无损压缩表示，"
    "必须严格按columns解读全部模型与Provider，不得把压缩格式当作排序或筛选。每项工作必须恰好分配一次，依赖必须"
    "由显式边表达，最终节点必须完成用户明确的交付合同。Provider必须单锁且禁止fallback，专家"
    "禁止工具。只输出严格JSON，不解释，不写报告。"
)
SYNTHESIS_PROMPT = (
    "你是专家团中心的一次性综合器。你必须根据原始任务、初始GPT任务拆解与专家组合、Claude一次"
    "红队给出的结构化修改意见和同一精确模型端点目录，形成最终work_items与专家执行图。Claude意见"
    "是咨询意见，不是批准、否决或门禁；你应逐条综合，可在硬约束下采纳、调整或不采纳。不得再次"
    "调用或请求Claude，不得循环，不得添加目录外模型，不得绕过硬约束，不得依赖本地评分或预设角色。目录可能采用"
    "columns定义加数组行的无损压缩表示，必须严格按columns解读全部模型与Provider。"
    "只输出一份最终严格JSON提案，不解释，不写报告。"
)
PROPOSAL_PROMPT_SHA256 = "1729b23ce30ab0ef9a263f6c593316356640613a80eb32f68c7346964b907fe8"
SYNTHESIS_PROMPT_SHA256 = "1d113d2f66a5e822d92dac009683649ef5de66930d5ad0143d23a8f18cfd755d"


class GPTSelectorError(RuntimeError):
    """Raised when the fixed GPT protocol or its output is invalid."""


def _fixed(prompt: str, expected: str) -> str:
    if sha256(prompt.encode("utf-8")).hexdigest() != expected:
        raise GPTSelectorError("fixed GPT selector prompt integrity failure")
    return prompt


def _schema(name: str) -> dict[str, Any]:
    identifier = {
        "type": "string",
        "pattern": GPT_IDENTIFIER_PATTERN,
    }
    node_id = {
        "type": "string",
        "pattern": GPT_NODE_ID_PATTERN,
    }
    work_id = {
        "type": "string",
        "pattern": GPT_WORK_ID_PATTERN,
    }
    bounded_text = {
        "type": "string",
        "minLength": 1,
        "maxLength": 320,
    }
    work = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "work_id": work_id,
            "objective": bounded_text,
            "dependencies": {
                "type": "array",
                "maxItems": GPT_MAX_WORK_ITEMS,
                "uniqueItems": True,
                "items": work_id,
            },
            "required_outputs": {
                "type": "array",
                "minItems": 1,
                "maxItems": GPT_MAX_OUTPUT_FIELDS_PER_WORK,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 160,
                },
            },
        },
        "required": [
            "work_id",
            "objective",
            "dependencies",
            "required_outputs",
        ],
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
                "maxItems": GPT_MAX_WORK_ITEMS,
                "uniqueItems": True,
                "items": work_id,
            },
            "role": bounded_text,
            "functions": {
                "type": "array",
                "maxItems": GPT_MAX_FUNCTIONS_PER_NODE,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 96,
                },
            },
            "model": identifier,
            "provider": identifier,
            "reasoning_effort": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "max_output_tokens": {
                "type": "integer",
                "minimum": 256,
                "description": (
                    "Advisory output-capacity estimate only; no local maximum "
                    "is enforced and the value is never sent as a provider cap."
                ),
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
            "functions",
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
                "enum": list(GPT_RELATION_TYPES),
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
                    "work_items": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": GPT_MAX_WORK_ITEMS,
                        "items": work,
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
                        "items": node_id,
                    },
                },
                "required": [
                    "work_items",
                    "nodes",
                    "edges",
                    "final_nodes",
                ],
            },
        },
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _parameter_flags(values: Any) -> str:
    supported = {
        str(value).casefold()
        for value in values
    } if isinstance(values, list) else set()
    output = (
        "c"
        if "max_completion_tokens" in supported
        else "t" if "max_tokens" in supported else "-"
    )
    return "".join(
        (
            output,
            "r" if "reasoning" in supported else "-",
            "e" if "reasoning_effort" in supported else "-",
            "T" if "temperature" in supported else "-",
            (
                "S"
                if {"structured_outputs", "response_format"}.intersection(
                    supported
                )
                else "-"
            ),
        )
    )


def _catalog_endpoint_identity(
    row: Any,
) -> tuple[str, str, str, int]:
    if not isinstance(row, Mapping):
        raise GPTSelectorError("GPT selector catalog endpoint is invalid")
    model = str(row.get("model") or "")
    company = str(row.get("company") or "")
    provider = str(row.get("provider") or "")
    rank = int(row.get("official_intelligence_rank") or 0)
    if not model or not company or not provider or rank <= 0:
        raise GPTSelectorError("GPT selector catalog identity is invalid")
    return model, company, provider, rank


def _prompt_provider_row(
    row: Mapping[str, Any],
    provider: str,
) -> list[Any]:
    return [
        provider,
        int(row.get("context_length") or 0),
        int(row.get("max_completion_tokens") or 0),
        float(row.get("prompt_price_per_million") or 0.0),
        float(row.get("completion_price_per_million") or 0.0),
        _parameter_flags(row.get("supported_parameters")),
        bool(row.get("synthetic_fixture_only")),
    ]


def _group_prompt_catalog_endpoints(
    endpoints: list[Any],
) -> tuple[
    dict[tuple[str, str, int], list[list[Any]]],
    set[tuple[str, str]],
]:
    grouped: dict[tuple[str, str, int], list[list[Any]]] = {}
    endpoint_pairs: set[tuple[str, str]] = set()
    for row in endpoints:
        model, company, provider, rank = _catalog_endpoint_identity(row)
        pair = (model, provider)
        if pair in endpoint_pairs:
            raise GPTSelectorError(
                "GPT selector catalog contains duplicate endpoint"
            )
        endpoint_pairs.add(pair)
        grouped.setdefault((model, company, rank), []).append(
            _prompt_provider_row(row, provider)
        )
    return grouped, endpoint_pairs


def _prompt_catalog_models(
    grouped: Mapping[tuple[str, str, int], list[list[Any]]],
) -> list[list[Any]]:
    return [
        [
            model,
            company,
            rank,
            sorted(providers, key=lambda value: str(value[0])),
        ]
        for (model, company, rank), providers in sorted(
            grouped.items(),
            key=lambda item: (item[0][2], item[0][0]),
        )
    ]


def _prompt_catalog_source(
    catalog: Mapping[str, Any],
    endpoints: list[Any],
) -> dict[str, Any]:
    return {
        "required_context_tokens": int(
            catalog.get("required_context_tokens") or 0
        ),
        "minimum_completion_tokens": int(
            catalog.get("minimum_completion_tokens") or 0
        ),
        "endpoints": sorted(
            [dict(row) for row in endpoints],
            key=lambda row: (
                str(row.get("model") or ""),
                str(row.get("provider") or ""),
            ),
        ),
    }


def _prompt_catalog_columns() -> dict[str, Any]:
    return {
        "model": ["model", "company", "official_rank", "providers"],
        "provider": [
            "provider",
            "context_tokens",
            "max_output_tokens",
            "prompt_price_per_million",
            "completion_price_per_million",
            "flags",
            "synthetic_fixture_only",
        ],
        "flags": (
            "position1 c=max_completion_tokens,t=max_tokens; "
            "r=reasoning; e=reasoning_effort; T=temperature; "
            "S=structured_outputs_or_response_format; -=unsupported"
        ),
    }


def governance_prompt_catalog(
    catalog: Mapping[str, Any],
) -> dict[str, Any]:
    """Project every exact endpoint into a compact, lossless prompt view.

    The full catalog remains the deterministic validation authority. This view
    removes only repeated descriptive fields; it performs no ranking, scoring,
    pruning, or candidate selection.
    """
    endpoints = catalog.get("endpoints", [])
    if not isinstance(endpoints, list) or not endpoints:
        raise GPTSelectorError("GPT selector catalog has no endpoints")
    grouped, endpoint_pairs = _group_prompt_catalog_endpoints(endpoints)
    models = _prompt_catalog_models(grouped)
    normalized_source = _prompt_catalog_source(catalog, endpoints)
    view = {
        "schema_version": "v5-gpt-catalog-prompt-view-1",
        "source_catalog_sha256": sha256(
            _canonical_json(normalized_source).encode("utf-8")
        ).hexdigest(),
        "source_endpoint_count": len(endpoint_pairs),
        "model_count": len(models),
        "official_order_only": True,
        "local_score_computed": False,
        "optimizer_used": False,
        "pareto_pruning_used": False,
        "heuristic_ranking_used": False,
        "required_context_tokens": int(
            catalog.get("required_context_tokens") or 0
        ),
        "minimum_completion_tokens": int(
            catalog.get("minimum_completion_tokens") or 0
        ),
        "columns": _prompt_catalog_columns(),
        "models": models,
    }
    return view


def _content(value: Mapping[str, Any]) -> str:
    return _canonical_json(value)


def _request(
    prompt: str,
    prompt_hash: str,
    name: str,
    user: Mapping[str, Any],
) -> dict[str, Any]:
    user_content = _content(user)
    catalog = user.get("catalog")
    catalog = dict(catalog) if isinstance(catalog, Mapping) else {}
    return {
        "model": GPT_SELECTOR_MODEL,
        "messages": [
            {"role": "system", "content": _fixed(prompt, prompt_hash)},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "reasoning": {"effort": "high", "exclude": True},
        "response_format": _schema(name),
        "provider": {
            "only": [GPT_SELECTOR_PROVIDER],
            "order": [GPT_SELECTOR_PROVIDER],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        "governance_policy": {
            "catalog_prompt_schema": catalog.get("schema_version"),
            "catalog_source_sha256": catalog.get("source_catalog_sha256"),
            "catalog_source_endpoint_count": catalog.get(
                "source_endpoint_count"
            ),
            "catalog_model_count": catalog.get("model_count"),
            "catalog_prompt_characters": len(_canonical_json(catalog)),
            "user_message_characters": len(user_content),
            "candidate_pruning_used": False,
            "local_scoring_used": False,
            "local_token_ceiling_enforced": False,
        },
    }


def _execution_constraints(
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
        "maximum_work_items": GPT_MAX_WORK_ITEMS,
        "maximum_edges": GPT_MAX_EDGES,
        "cost_anomaly_usd": cost_anomaly_usd,
        "distinct_expert_companies": True,
        "governance_companies_forbidden_for_experts": [
            "openai",
            "anthropic",
        ],
        "tools_allowed": False,
        "provider_fallback_allowed": False,
        "local_token_ceiling_enforced": False,
        "cost_threshold_can_reject_plan": False,
    }


def build_proposal_request(
    *,
    task: str,
    task_envelope: Mapping[str, Any],
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
            "task_envelope": task_envelope,
            "catalog": governance_prompt_catalog(catalog),
            "execution_constraints": _execution_constraints(
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
    claude_advice: Mapping[str, Any],
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    approved_total_calls: int,
    governance_calls_reserved: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    constraints = _execution_constraints(
        approved_total_calls=approved_total_calls,
        governance_calls_reserved=governance_calls_reserved,
        approved_recovery_calls=approved_recovery_calls,
        cost_anomaly_usd=cost_anomaly_usd,
    )
    constraints["claude_second_review_allowed"] = False
    constraints["claude_is_advisory_only"] = True
    return _request(
        SYNTHESIS_PROMPT,
        SYNTHESIS_PROMPT_SHA256,
        "gpt_expert_team_synthesis",
        {
            "task": str(task),
            "initial_proposal": initial_proposal,
            "claude_red_team_advice": {
                "suggestions": list(
                    claude_advice.get("suggestions") or []
                ),
            },
            "task_envelope": task_envelope,
            "catalog": governance_prompt_catalog(catalog),
            "execution_constraints": constraints,
        },
    )


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GPTSelectorError(f"{field} is invalid")
    if any(ord(character) < 32 for character in value):
        raise GPTSelectorError(f"{field} contains control characters")
    return value


def _identifier(value: Any, field: str, pattern: str) -> str:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise GPTSelectorError(f"{field} must be a bounded identifier")
    return value


def _validate_work_item(work: Any, index: int) -> str:
    expected = {"work_id", "objective", "dependencies", "required_outputs"}
    if not isinstance(work, Mapping) or set(work) != expected:
        raise GPTSelectorError(f"work_items[{index}] has invalid fields")
    work_id = _identifier(
        work["work_id"],
        f"work_items[{index}].work_id",
        GPT_WORK_ID_PATTERN,
    )
    _bounded_text(work["objective"], f"work_items[{index}].objective", 320)
    dependencies = work["dependencies"]
    outputs = work["required_outputs"]
    if not isinstance(dependencies, list) or len(dependencies) > GPT_MAX_WORK_ITEMS:
        raise GPTSelectorError("work dependencies are invalid")
    normalized_dependencies = [
        _identifier(value, "work dependency", GPT_WORK_ID_PATTERN)
        for value in dependencies
    ]
    if len(normalized_dependencies) != len(set(normalized_dependencies)):
        raise GPTSelectorError("work dependencies contain duplicates")
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= GPT_MAX_OUTPUT_FIELDS_PER_WORK:
        raise GPTSelectorError("work required_outputs are invalid")
    normalized_outputs = [
        _bounded_text(output, "required_output", 160)
        for output in outputs
    ]
    if len(normalized_outputs) != len(set(normalized_outputs)):
        raise GPTSelectorError("work required_outputs contain duplicates")
    return work_id


def _validate_work_items(work_items: Any) -> set[str]:
    if not isinstance(work_items, list) or not 1 <= len(work_items) <= GPT_MAX_WORK_ITEMS:
        raise GPTSelectorError("GPT proposal work_items are invalid")
    work_ids = [_validate_work_item(work, index) for index, work in enumerate(work_items)]
    if len(work_ids) != len(set(work_ids)):
        raise GPTSelectorError("GPT proposal has duplicate work ids")
    known_work = set(work_ids)
    for work in work_items:
        dependencies = set(work["dependencies"])
        if work["work_id"] in dependencies or not dependencies.issubset(known_work):
            raise GPTSelectorError("work dependency references are invalid")
    return known_work


def _validate_recovery_rows(recovery: Any) -> None:
    if not isinstance(recovery, list) or len(recovery) > GPT_MAX_RECOVERY_PER_NODE:
        raise GPTSelectorError("node recovery is invalid")
    for row in recovery:
        if not isinstance(row, Mapping) or set(row) != {"model", "provider"}:
            raise GPTSelectorError("recovery row has invalid fields")
        _identifier(row["model"], "recovery model", GPT_IDENTIFIER_PATTERN)
        _identifier(row["provider"], "recovery provider", GPT_IDENTIFIER_PATTERN)


def _validate_node(node: Any, index: int, known_work: set[str]) -> str:
    expected = {
        "node_id", "work_ids", "role", "functions", "model", "provider",
        "reasoning_effort", "max_output_tokens", "recovery",
    }
    if not isinstance(node, Mapping) or set(node) != expected:
        raise GPTSelectorError(f"nodes[{index}] has invalid fields")
    node_id = _identifier(
        node["node_id"],
        f"nodes[{index}].node_id",
        GPT_NODE_ID_PATTERN,
    )
    assigned = node["work_ids"]
    functions = node["functions"]
    if not isinstance(assigned, list) or not assigned or len(assigned) > GPT_MAX_WORK_ITEMS:
        raise GPTSelectorError("node work_ids are invalid")
    normalized_assigned = [
        _identifier(value, "node work_id", GPT_WORK_ID_PATTERN)
        for value in assigned
    ]
    if (
        len(normalized_assigned) != len(set(normalized_assigned))
        or not set(normalized_assigned).issubset(known_work)
    ):
        raise GPTSelectorError("node work_ids contain duplicates or unknown work")
    _bounded_text(node["role"], f"nodes[{index}].role", 320)
    if not isinstance(functions, list) or len(functions) > GPT_MAX_FUNCTIONS_PER_NODE:
        raise GPTSelectorError("node functions are invalid")
    normalized_functions = [
        _bounded_text(function, "node function", 96)
        for function in functions
    ]
    if len(normalized_functions) != len(set(normalized_functions)):
        raise GPTSelectorError("node functions contain duplicates")
    _identifier(node["model"], f"nodes[{index}].model", GPT_IDENTIFIER_PATTERN)
    _identifier(node["provider"], f"nodes[{index}].provider", GPT_IDENTIFIER_PATTERN)
    if node["reasoning_effort"] not in {"low", "medium", "high"}:
        raise GPTSelectorError("node reasoning_effort is invalid")
    maximum = node["max_output_tokens"]
    if isinstance(maximum, bool) or not isinstance(maximum, int):
        raise GPTSelectorError("node max_output_tokens must be an integer")
    if maximum < 256:
        raise GPTSelectorError(
            "node max_output_tokens advisory must be at least 256"
        )
    _validate_recovery_rows(node["recovery"])
    return node_id


def _validate_nodes(nodes: Any, known_work: set[str]) -> set[str]:
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= GPT_MAX_NODES:
        raise GPTSelectorError("GPT proposal nodes are invalid")
    node_ids = [_validate_node(node, index, known_work) for index, node in enumerate(nodes)]
    if len(node_ids) != len(set(node_ids)):
        raise GPTSelectorError("GPT proposal has duplicate node ids")
    return set(node_ids)


def _validate_edges(edges: Any, known_nodes: set[str]) -> None:
    if not isinstance(edges, list) or len(edges) > GPT_MAX_EDGES:
        raise GPTSelectorError("GPT proposal edges are invalid")
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping) or set(edge) != {"source", "target", "relation_type"}:
            raise GPTSelectorError("edge has invalid fields")
        source = _identifier(
            edge["source"],
            f"edges[{index}].source",
            GPT_NODE_ID_PATTERN,
        )
        target = _identifier(
            edge["target"],
            f"edges[{index}].target",
            GPT_NODE_ID_PATTERN,
        )
        if source not in known_nodes or target not in known_nodes:
            raise GPTSelectorError("edge references unknown node")
        if edge["relation_type"] not in GPT_RELATION_TYPES:
            raise GPTSelectorError("edge relation_type is invalid")


def _validate_final_nodes(final_nodes: Any, known_nodes: set[str]) -> None:
    if not isinstance(final_nodes, list) or not final_nodes:
        raise GPTSelectorError("GPT proposal final_nodes are invalid")
    normalized = [
        _identifier(value, "final_node", GPT_NODE_ID_PATTERN)
        for value in final_nodes
    ]
    if len(normalized) != len(set(normalized)) or not set(normalized).issubset(known_nodes):
        raise GPTSelectorError("GPT proposal final_nodes contain duplicates or unknown nodes")


def _validate_proposal(value: Mapping[str, Any]) -> None:
    required = {"work_items", "nodes", "edges", "final_nodes"}
    if set(value) != required:
        raise GPTSelectorError("GPT proposal has missing or extra fields")
    known_work = _validate_work_items(value["work_items"])
    known_nodes = _validate_nodes(value["nodes"], known_work)
    _validate_edges(value["edges"], known_nodes)
    _validate_final_nodes(value["final_nodes"], known_nodes)


def parse_proposal(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise GPTSelectorError("GPT proposal is empty")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GPTSelectorError("GPT proposal is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise GPTSelectorError("GPT proposal root must be an object")
    _validate_proposal(value)
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
