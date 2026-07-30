"""Compile a task into atomic work, prompt, capability, and parameter demands."""
from __future__ import annotations

import json
import os
from hashlib import sha256
from typing import Any

import task_matrix_optimizer as legacy
from model_market import ExpertTeamError, RunConfig, TaskProfile

PROMPT_MODULES = {
    "scope": ("界定目标、边界、约束、交付物和不处理事项。", 70, 60),
    "evidence": ("区分事实、假设、推断和证据缺口，不得虚构来源或数据。", 90, 95),
    "quant": ("明确变量、单位、公式、约束、敏感性和误差来源。", 95, 100),
    "scenario": ("比较基准、变化和失效情景，并给出触发条件。", 85, 85),
    "redteam": ("寻找反例、失败路径、隐藏假设、否决条件和最强替代解释。", 95, 110),
    "implementation": ("给出依赖、接口、步骤、验收、故障边界和回滚条件。", 90, 90),
    "decision": ("比较收益、成本、风险、可逆性和机会成本并给出排序。", 90, 90),
    "creative": ("生成差异充分的候选，再按约束筛选。", 70, 65),
    "uncertainty": ("给出置信度、关键不确定性和最值得补充的数据。", 70, 75),
    "delivery": ("按任务要求结构化交付，使结论、依据和风险可定位。", 65, 60),
    "independence": ("独立完成本工作单元，不迎合其他专家。", 60, 80),
    "synthesis": ("核对覆盖、共识、分歧、证据强度和冲突原因，不以多数票代替判断。", 95, 115),
}
OP_MODULES = {
    "analysis": {"scope", "uncertainty"},
    "decision": {"decision", "uncertainty"},
    "evidence": {"evidence", "uncertainty"},
    "quantitative": {"quant", "evidence"},
    "forecast": {"scenario", "uncertainty"},
    "adversarial": {"redteam", "evidence", "independence"},
    "implementation": {"implementation", "scope"},
    "creative": {"creative", "scope"},
}
OP_CAPS = {
    "analysis": {"reasoning"}, "decision": {"reasoning"}, "evidence": {"reasoning"},
    "quantitative": {"reasoning"}, "forecast": {"reasoning"},
    "adversarial": {"reasoning"}, "implementation": {"reasoning"}, "creative": set(),
}
OP_LABELS = {
    "analysis": "分析", "decision": "决策", "evidence": "证据核验",
    "quantitative": "定量计算", "forecast": "预测推演", "adversarial": "独立反证",
    "implementation": "工程实现", "creative": "创意生成",
}


def digest(prefix: str, value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-{sha256(raw.encode()).hexdigest()[:10]}"


def parse_constraints(run: RunConfig) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    match = legacy.INPUT_RE.search(run.task)
    if match:
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise ExpertTeamError(f"Invalid <expert-team-input> JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ExpertTeamError("<expert-team-input> must contain one JSON object.")
        raw.update(value)
    env = os.getenv("EXPERT_TEAM_INPUT_JSON", "").strip()
    if env:
        try:
            value = json.loads(env)
        except json.JSONDecodeError as exc:
            raise ExpertTeamError(f"EXPERT_TEAM_INPUT_JSON is invalid: {exc}") from exc
        if not isinstance(value, dict):
            raise ExpertTeamError("EXPERT_TEAM_INPUT_JSON must be a JSON object.")
        raw.update(value)
    accepted = {
        "budget_usd", "min_experts", "max_experts", "strict_provider_diversity",
        "candidate_pool_per_seat", "candidate_pool_per_work_package",
        "solver_timeout_seconds", "quality_tolerance_pct",
        "forbidden_models", "preferred_models",
    }
    unknown = sorted(set(raw) - accepted)
    if unknown:
        raise ExpertTeamError(f"Unsupported expert-team input fields: {unknown}")
    budget_raw = raw.get("budget_usd", run.max_estimated_cost_usd)
    budget = None if budget_raw in {None, ""} else legacy._finite(budget_raw, -1.0)
    if budget is not None and budget <= 0:
        raise ExpertTeamError("budget_usd must be greater than zero.")
    minimum = max(1, int(raw.get("min_experts", 1)))
    maximum_raw = raw.get("max_experts")
    maximum = None if maximum_raw in {None, ""} else int(maximum_raw)
    if maximum is not None and maximum < minimum:
        raise ExpertTeamError("max_experts must be >= min_experts.")
    pool = int(raw.get(
        "candidate_pool_per_work_package",
        raw.get("candidate_pool_per_seat", max(12, int(run.candidate_pool_per_seat))),
    ))
    return {
        "budget_usd": budget,
        "min_experts": minimum,
        "max_experts": maximum,
        "strict_provider_diversity": bool(raw.get("strict_provider_diversity", True)),
        "candidate_pool_per_work_package": max(4, min(50, pool)),
        "solver_timeout_seconds": max(1.0, min(60.0, legacy._finite(raw.get("solver_timeout_seconds", 12), 12))),
        "quality_tolerance_pct": max(0.0, min(20.0, legacy._finite(raw.get("quality_tolerance_pct", 2), 2))),
        "forbidden_models": sorted({str(x) for x in raw.get("forbidden_models", []) if str(x)}),
        "preferred_models": sorted({str(x) for x in raw.get("preferred_models", []) if str(x)}),
    }


def _level(operation: str, importance: float, profile: TaskProfile) -> int:
    level = 2 if operation in {"quantitative", "forecast", "adversarial"} else 1
    if operation == "creative":
        level = 0
    if profile.high_stakes and operation in {"decision", "evidence", "adversarial"}:
        level = 2
    if profile.complexity == "complex" and importance >= 0.85:
        level = max(level, 2)
    return level


def _unit(operation: str, domain: str, importance: float, profile: TaskProfile, chars: int) -> dict[str, Any]:
    modules = set(OP_MODULES[operation]) | {"delivery"}
    if profile.high_stakes:
        modules.add("uncertainty")
    # High-stakes work needs structured reasoning and delivery, but not necessarily
    # provider-native JSON/schema support. Native structured output is requested
    # only by the task-level output contract below.
    caps = set(OP_CAPS[operation])
    tokens = 650 + int(importance * 900) + min(650, chars // 8)
    if operation in {"quantitative", "forecast", "adversarial", "implementation"}:
        tokens += 300
    return {
        "id": digest("work", [operation, domain]),
        "operation": operation,
        "domain": domain,
        "importance": round(legacy._clamp(importance), 6),
        "required_capabilities": sorted(caps),
        "required_prompt_modules": sorted(modules),
        "minimum_reasoning_level": _level(operation, importance, profile),
        "structured_output_required": False,
        "minimum_context_tokens": profile.requested_context,
        "expected_output_tokens": max(700, min(4200, tokens)),
        "independence_group": "risk_challenge" if operation == "adversarial" else None,
    }


def compile_requirements(profile: TaskProfile, run: RunConfig) -> dict[str, Any]:
    text = legacy.INPUT_RE.sub(" ", run.task).strip()
    base = legacy.build_task_matrix(profile, run)
    domains = [
        name for name, score in sorted(base["domain_scores"].items(), key=lambda x: (-x[1], x[0]))
        if score >= 0.25
    ][:4] or [profile.primary_domain or "general"]
    primary = profile.primary_domain if profile.primary_domain in domains else domains[0]
    operations = dict(base["operation_scores"])
    operations["analysis"] = max(float(operations.get("analysis", 0)), 0.60)
    folded = text.casefold()
    if any(x in folded for x in ("选择", "比较", "最优", "方案", "choose", "compare", "recommend")):
        operations["decision"] = max(float(operations.get("decision", 0)), 0.75)
    if profile.high_stakes:
        operations["evidence"] = max(float(operations.get("evidence", 0)), 0.80)
        operations["adversarial"] = max(float(operations.get("adversarial", 0)), 0.82)
    thresholds = {
        "analysis": 0.25, "decision": 0.45, "evidence": 0.50, "quantitative": 0.50,
        "forecast": 0.50, "adversarial": 0.52, "implementation": 0.50, "creative": 0.50,
    }
    if profile.high_stakes:
        thresholds["evidence"] = thresholds["adversarial"] = 0.20
    active = [name for name, value in operations.items() if value >= thresholds[name]]
    if "analysis" not in active:
        active.insert(0, "analysis")
    structured_requested = any(term in folded for term in (
        "json", "json schema", "schema", "结构化输出", "机器可读", "严格字段", "response_format"
    ))
    units = [_unit("analysis", primary, operations["analysis"], profile, len(text))]
    for domain in domains:
        if domain != primary and float(base["domain_scores"].get(domain, 0)) >= 0.45:
            units.append(_unit("analysis", domain, float(base["domain_scores"][domain]), profile, len(text)))
    op_domain = {
        "decision": primary, "evidence": "research" if "research" in domains else primary,
        "quantitative": "math", "forecast": primary,
        "adversarial": next((x for x in ("security", "legal", "medical", "international_relations") if x in domains), primary),
        "implementation": "coding", "creative": "creative",
    }
    for operation in active:
        if operation != "analysis":
            units.append(_unit(operation, op_domain[operation], max(float(operations[operation]), 0.55), profile, len(text)))
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for unit in units:
        key = (unit["operation"], unit["domain"])
        if key not in unique or unit["importance"] > unique[key]["importance"]:
            unique[key] = unit
    units = sorted(unique.values(), key=lambda x: (-x["importance"], x["operation"], x["domain"]))
    copies = {
        unit["id"]: 2 if profile.high_stakes and unit["operation"] in {"analysis", "evidence"} else 1
        for unit in units
    }
    synthesis_modules = {"scope", "delivery", "uncertainty", "synthesis"}
    if any(x["operation"] == "decision" for x in units):
        synthesis_modules.add("decision")
    if any(x["operation"] == "evidence" for x in units):
        synthesis_modules.add("evidence")
    return {
        "version": 3,
        "architecture": "task-to-resource-requirements-before-market-lookup",
        "task_signals": {
            **base["signals"], "primary_domain": primary, "active_domains": domains,
            "active_operations": active, "native_structured_output_requested": structured_requested,
        },
        "domain_scores": base["domain_scores"],
        "operation_scores": {k: round(float(v), 6) for k, v in operations.items()},
        "atomic_work_units": units,
        "coverage_requirements": copies,
        "synthesis_requirements": {
            "minimum_context_tokens": profile.requested_context,
            "expected_output_tokens": max(1100, min(5000, 850 + len(units) * 420 + len(text) // 7)),
            "minimum_reasoning_level": 2 if profile.high_stakes or len(units) >= 4 else 1,
            "structured_output_required": structured_requested,
            "required_prompt_modules": sorted(synthesis_modules),
        },
        "requested_market_attributes": [
            "model_id", "provider", "intelligence_rank", "benchmark_by_domain",
            "prompt_price", "completion_price", "context_length", "max_output_tokens",
            "supported_parameters", "modalities", "reasoning_support",
            "knowledge_cutoff", "expiration_date", "version_status",
        ],
        "constraints": parse_constraints(run),
        "history_input_used": False,
        "fixed_team_mode_used": False,
        "fixed_seat_template_used": False,
        "fixed_prompt_template_used": False,
        "fixed_parameter_template_used": False,
    }
