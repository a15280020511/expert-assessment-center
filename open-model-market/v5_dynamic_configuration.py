"""Dynamic V5 node-role and request-parameter configuration.

Hard invariants remain non-optimizable: no tools, no routed models, explicit
provider endpoints, bounded calls/cost/context, schema validation, and auditability.
Everything below those ceilings is derived from the task work units and endpoint
capabilities rather than a fixed expert-seat template.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

import v5_planner as planner

_BASE_CANDIDATE_FOR = planner._candidate_for

DOMAIN_LABELS = {
    "business": "商业与财务",
    "legal": "法律与合规",
    "public_policy": "公共政策",
    "coding": "软件工程",
    "math": "定量建模",
    "research": "证据研究",
    "security": "安全与风险",
    "medical": "医疗健康",
    "international_relations": "国际关系",
    "supply_chain": "供应链运营",
    "creative": "创意设计",
    "general": "综合分析",
}
OPERATION_LABELS = {
    "analysis": "分析",
    "causal_reasoning": "因果推断",
    "quantitative_modeling": "定量建模",
    "forecasting": "预测推演",
    "counterfactual_analysis": "反事实",
    "evidence_validation": "证据核验",
    "decision_comparison": "决策优化",
    "adversarial_reasoning": "独立红队",
    "implementation": "工程实施",
    "creative_generation": "创意生成",
    "synthesis": "综合裁决",
}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _work_rows(args: Sequence[Any]) -> list[Mapping[str, Any]]:
    raw = args[2] if len(args) > 2 else ()
    return [row for row in raw if isinstance(row, Mapping)] if isinstance(raw, Sequence) else []


def _endpoint(args: Sequence[Any]) -> Mapping[str, Any]:
    raw = args[4] if len(args) > 4 else {}
    return raw if isinstance(raw, Mapping) else {}


def _role_profile(works: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    domains: dict[str, float] = {}
    operations: dict[str, float] = {}
    for work in works:
        for name, value in dict(work.get("domain_requirements", {})).items():
            domains[str(name)] = max(domains.get(str(name), 0.0), _float(value))
        for name, value in dict(work.get("operation_requirements", {})).items():
            operations[str(name)] = max(operations.get(str(name), 0.0), _float(value))
    domain_order = sorted(domains, key=lambda name: (-domains[name], name))
    operation_order = sorted(operations, key=lambda name: (-operations[name], name))
    domain_text = "＋".join(DOMAIN_LABELS.get(name, name) for name in domain_order[:2]) or "综合分析"
    operation_text = "＋".join(OPERATION_LABELS.get(name, name) for name in operation_order[:2]) or "分析"
    return {
        "professional_role": f"{domain_text}·{operation_text}复合节点",
        "dominant_domains": domain_order[:4],
        "cognitive_operations": operation_order,
        "profession_source": "task-domain-operation-matrix",
        "fixed_profession_used": False,
    }


def _dynamic_parameters(
    works: Sequence[Mapping[str, Any]],
    endpoint: Mapping[str, Any],
    candidate: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    supported = {str(value).casefold() for value in endpoint.get("supported_parameters", [])}
    operations = {
        str(name)
        for work in works
        for name in dict(work.get("operation_requirements", {}))
    }
    importance = max((_float(work.get("importance"), 0.5) for work in works), default=0.5)
    error_cost = max((_float(work.get("error_cost"), 0.5) for work in works), default=0.5)
    depth = _float(candidate.reasoning_profile.get("depth"), 0.5)
    control_inputs = (depth, error_cost, importance)
    control_score = max(
        0.0,
        min(1.0, sum(control_inputs) / len(control_inputs)),
    )
    structured = bool(candidate.output_contract.get("machine_readable_required"))
    creative = bool(operations & {"creative_generation", "counterfactual_analysis"})
    verification = bool(operations & {
        "quantitative_modeling", "evidence_validation", "implementation", "synthesis"
    })

    if structured:
        temperature = 0.0
        top_p = 0.85
    elif creative:
        temperature = min(0.85, 0.42 + 0.35 * (1.0 - control_score))
        top_p = 0.95
    elif verification:
        temperature = max(0.0, 0.18 * (1.0 - control_score))
        top_p = 0.88
    else:
        temperature = max(0.05, 0.28 * (1.0 - control_score))
        top_p = 0.92

    effort = "high" if control_score >= 0.78 else "medium" if control_score >= 0.52 else "low"
    request = dict(candidate.request_config)
    decisions: dict[str, Any] = {
        "control_score": round(control_score, 6),
        "reasoning_effort": effort,
        "temperature": round(temperature, 4),
        "top_p": round(top_p, 4),
        "structured_delivery": structured,
        "creative_exploration": creative,
        "verification_intensive": verification,
        "supported_parameter_filter_applied": True,
    }
    if "reasoning" in supported and candidate.reasoning_profile.get("reasoning_enabled"):
        request["reasoning"] = {"effort": effort, "exclude": True}
    if "temperature" in supported:
        request["temperature"] = round(temperature, 4)
    if "top_p" in supported:
        request["top_p"] = round(top_p, 4)
    if creative and "presence_penalty" in supported:
        request["presence_penalty"] = round(min(0.35, 0.10 + 0.20 * (1.0 - control_score)), 4)
    if verification and "frequency_penalty" in supported:
        request["frequency_penalty"] = 0.0
    return request, decisions


def dynamic_candidate_for(*args: Any, **kwargs: Any) -> Any:
    candidate = _BASE_CANDIDATE_FOR(*args, **kwargs)
    if candidate is None:
        return None
    works = _work_rows(args)
    endpoint = _endpoint(args)
    role = _role_profile(works)
    request, decisions = _dynamic_parameters(works, endpoint, candidate)
    prompt = dict(candidate.prompt_profile)
    prompt.update(role)
    parameter = dict(candidate.parameter_profile)
    parameter["dynamic_parameter_decisions"] = decisions
    parameter["fixed_request_parameter_profile_used"] = False
    return replace(
        candidate,
        prompt_profile=prompt,
        parameter_profile=parameter,
        request_config=request,
    )


def parameter_audit_catalog() -> list[dict[str, str]]:
    """Canonical classification used by tests, artifacts, and design documentation."""
    rows = [
        ("external_tools", "hard_invariant", "always forbidden"),
        ("model", "dynamic", "candidate market plus CP-SAT"),
        ("provider_endpoint", "dynamic", "real endpoint market plus budget-safe preflight"),
        ("professional_role", "dynamic", "domain-operation capability matrix"),
        ("expert_count", "dynamic_bounded", "selected graph node count"),
        ("team_topology", "dynamic_bounded", "atomic-work DAG"),
        ("independent_copies", "dynamic_bounded", "stakes and independence requirements"),
        ("prompt_modules", "dynamic", "prompt requirement strengths"),
        ("reasoning_depth", "dynamic", "operation, complexity, stakes, error cost"),
        ("reasoning_effort", "dynamic_supported", "continuous control score to low/medium/high"),
        ("temperature", "dynamic_supported", "task control/exploration score"),
        ("top_p", "dynamic_supported", "task control/exploration score"),
        ("presence_penalty", "dynamic_supported", "creative tasks only"),
        ("frequency_penalty", "dynamic_supported", "verification tasks only"),
        ("output_contract", "dynamic", "work operations and delivery boundary"),
        ("output_allowance", "dynamic_bounded", "completion envelope under endpoint and 32768 hard permission"),
        ("estimated_token_usage", "dynamic", "reasoning-inclusive P95 estimate"),
        ("context_allocation", "dynamic_bounded", "task plus declared upstream graph"),
        ("optimizer_iterations", "dynamic_bounded", "candidate count and solver time"),
        ("retry_count", "dynamic_policy_bounded", "failure class within global ceiling"),
        ("replacement_count", "dynamic_policy_bounded", "recovery pool within global ceiling"),
        ("provider_diversity", "dynamic_soft", "rebalance only when budget-safe"),
        ("hard_budget", "governance_invariant", "user/workflow ceiling"),
        ("call_ceiling", "governance_invariant", "finite anti-loop ceiling"),
        ("max_nodes_edges_stages", "governance_invariant", "finite graph safety ceiling"),
        ("schema_validation", "hard_invariant", "delivery contract"),
        ("audit_artifacts", "hard_invariant", "request, cost, graph, result provenance"),
        ("production_cutover", "governance_invariant", "separate Stage-D/Canary/observation gates"),
    ]
    return [
        {"parameter": parameter, "classification": classification, "basis": basis}
        for parameter, classification, basis in rows
    ]


def install() -> None:
    """Deprecated no-op: PlannerPolicy calls dynamic functions directly."""
    return None
