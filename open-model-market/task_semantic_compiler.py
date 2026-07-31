"""Market-free V5 task compiler: task -> interpretations -> atomic work."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any, Mapping

from model_market import RunConfig, TaskProfile

import v5_task_delivery_contract as task_delivery_contract

_INPUT_RE = re.compile(
    r"<expert-team-input>\s*(\{.*?\})\s*</expert-team-input>", re.I | re.S
)
DOMAINS = {
    "business": ("商业", "金融", "投资", "市场", "财务", "business", "finance"),
    "legal": ("法律", "法规", "合规", "监管", "合同", "legal", "compliance"),
    "public_policy": (
        "公共政策",
        "政府",
        "财政",
        "治理",
        "政策",
        "government",
        "governance",
    ),
    "coding": (
        "代码",
        "软件",
        "接口",
        "仓库",
        "部署",
        "code",
        "software",
        "repository",
    ),
    "math": (
        "计算",
        "建模",
        "仿真",
        "统计",
        "优化",
        "模型",
        "simulation",
        "statistics",
    ),
    "research": ("研究", "证据", "文献", "数据", "核验", "research", "evidence"),
    "security": ("安全", "攻击", "漏洞", "威胁", "security", "cyber", "threat"),
    "medical": ("医疗", "临床", "健康", "药品", "medical", "clinical"),
    "international_relations": ("外交", "战争", "制裁", "地缘", "war", "sanction"),
    "supply_chain": ("供应链", "物流", "采购", "库存", "运营", "logistics"),
    "creative": ("创意", "写作", "文案", "设计", "creative", "writing"),
}
OPERATIONS = {
    "analysis": ("分析", "评估", "判断", "解释", "analyze", "evaluate"),
    "causal_reasoning": ("原因", "因果", "机制", "驱动", "causal", "mechanism"),
    "quantitative_modeling": (
        "计算",
        "建模",
        "仿真",
        "概率",
        "统计",
        "优化",
        "calculate",
        "simulate",
    ),
    "forecasting": ("预测", "推演", "情景", "趋势", "未来", "forecast", "scenario"),
    "counterfactual_analysis": (
        "反事实",
        "如果",
        "替代情景",
        "what if",
        "counterfactual",
    ),
    "evidence_validation": (
        "证据",
        "数据",
        "来源",
        "核验",
        "文献",
        "验证",
        "evidence",
        "verify",
    ),
    "decision_comparison": (
        "决策",
        "选择",
        "方案",
        "建议",
        "最优",
        "排序",
        "decision",
        "recommend",
    ),
    "adversarial_reasoning": (
        "红队",
        "反证",
        "漏洞",
        "失败",
        "风险",
        "否决",
        "adversarial",
        "failure",
    ),
    "implementation": (
        "代码",
        "仓库",
        "部署",
        "接口",
        "实现",
        "implementation",
        "deploy",
    ),
    "creative_generation": (
        "创意",
        "文案",
        "写作",
        "故事",
        "设计",
        "creative",
        "story",
    ),
}
LABEL = {
    "analysis": "分析",
    "causal_reasoning": "因果推断",
    "quantitative_modeling": "定量建模",
    "forecasting": "预测推演",
    "counterfactual_analysis": "反事实分析",
    "evidence_validation": "证据核验",
    "decision_comparison": "方案比较与决策",
    "adversarial_reasoning": "独立反证与失败分析",
    "implementation": "工程实现",
    "creative_generation": "创意生成",
    "synthesis": "综合裁决",
}
PROMPTS = {
    "analysis": {
        "scope_control": 0.82,
        "uncertainty_calibration": 0.62,
        "structured_delivery": 0.68,
    },
    "causal_reasoning": {"evidence_discipline": 0.75, "uncertainty_calibration": 0.78},
    "quantitative_modeling": {
        "quantitative_rigor": 0.98,
        "evidence_discipline": 0.82,
        "structured_delivery": 0.86,
    },
    "forecasting": {"scenario_analysis": 0.94, "uncertainty_calibration": 0.91},
    "counterfactual_analysis": {"scenario_analysis": 0.88, "scope_control": 0.76},
    "evidence_validation": {
        "evidence_discipline": 0.98,
        "uncertainty_calibration": 0.86,
    },
    "decision_comparison": {
        "decision_comparison": 0.96,
        "uncertainty_calibration": 0.82,
    },
    "adversarial_reasoning": {
        "adversarial_challenge": 0.98,
        "evidence_discipline": 0.86,
    },
    "implementation": {"implementation_contract": 0.96, "structured_delivery": 0.88},
    "creative_generation": {"divergent_generation": 0.90, "scope_control": 0.76},
    "synthesis": {
        "synthesis_discipline": 0.98,
        "evidence_discipline": 0.80,
        "structured_delivery": 0.94,
    },
}
VERIFY = {
    "analysis": 0.48,
    "causal_reasoning": 0.42,
    "quantitative_modeling": 0.88,
    "forecasting": 0.30,
    "counterfactual_analysis": 0.36,
    "evidence_validation": 0.76,
    "decision_comparison": 0.44,
    "adversarial_reasoning": 0.52,
    "implementation": 0.72,
    "creative_generation": 0.34,
    "synthesis": 0.46,
}
ERROR = {
    "analysis": 0.58,
    "causal_reasoning": 0.64,
    "quantitative_modeling": 0.72,
    "forecasting": 0.70,
    "counterfactual_analysis": 0.60,
    "evidence_validation": 0.76,
    "decision_comparison": 0.82,
    "adversarial_reasoning": 0.68,
    "implementation": 0.66,
    "creative_generation": 0.36,
    "synthesis": 0.86,
}


@dataclass(frozen=True)
class AtomicWork:
    work_id: str
    objective: str
    importance: float
    error_cost: float
    verifiability: float
    domain_requirements: Mapping[str, float]
    operation_requirements: Mapping[str, float]
    prompt_requirements: Mapping[str, float]
    reasoning_requirements: Mapping[str, Any]
    context_requirements: Mapping[str, int]
    output_contract: Mapping[str, Any]
    independence_requirements: Mapping[str, Any]
    dependencies: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskInterpretation:
    interpretation_id: str
    strategy: str
    rationale: str
    atomic_work: tuple[AtomicWork, ...]
    metrics: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "interpretation_id": self.interpretation_id,
            "strategy": self.strategy,
            "rationale": self.rationale,
            "atomic_work": [x.to_dict() for x in self.atomic_work],
            "metrics": dict(self.metrics),
        }


def _digest(prefix: str, value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-{sha256(raw.encode()).hexdigest()[:12]}"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _score(text: str, terms: tuple[str, ...], divisor: float) -> float:
    folded = text.casefold()
    return _clamp(sum(term.casefold() in folded for term in terms) / divisor)


def _scores(
    task: str, profile: TaskProfile
) -> tuple[dict[str, float], dict[str, float]]:
    domains = {name: _score(task, terms, 3) for name, terms in DOMAINS.items()}
    for index, domain in enumerate(profile.domains[:4]):
        domains[domain] = max(domains.get(domain, 0), 1 - 0.13 * index)
    domains[profile.primary_domain or "general"] = max(
        domains.get(profile.primary_domain or "general", 0), 0.72
    )
    operations = {name: _score(task, terms, 2) for name, terms in OPERATIONS.items()}
    operations["analysis"] = max(operations["analysis"], 0.58)
    if profile.high_stakes:
        operations["evidence_validation"] = max(operations["evidence_validation"], 0.82)
        operations["adversarial_reasoning"] = max(
            operations["adversarial_reasoning"], 0.78
        )
        operations["decision_comparison"] = max(operations["decision_comparison"], 0.66)
    if profile.complexity == "complex":
        operations["causal_reasoning"] = max(operations["causal_reasoning"], 0.48)
    return (
        {k: round(v, 6) for k, v in domains.items()},
        {k: round(v, 6) for k, v in operations.items()},
    )


def _special_domain(operation: str, primary: str, active: list[str]) -> str:
    fixed = {
        "quantitative_modeling": "math",
        "evidence_validation": "research",
        "implementation": "coding",
        "creative_generation": "creative",
    }.get(operation)
    if fixed:
        return fixed
    if operation == "adversarial_reasoning":
        return next(
            (
                x
                for x in ("security", "legal", "medical", "international_relations")
                if x in active
            ),
            primary,
        )
    return primary


def _prompt_vector(operations: Mapping[str, float]) -> dict[str, float]:
    labels = {label for op in operations for label in PROMPTS.get(op, {})}
    return {
        label: round(max(PROMPTS.get(op, {}).get(label, 0) for op in operations), 6)
        for label in sorted(labels)
    }


def _reasoning_vector(
    operations: Mapping[str, float], high_stakes: bool, complexity: str
) -> dict[str, Any]:
    names = set(operations)
    depth = max(max(operations.values(), default=0.58), 0.86 if high_stakes else 0.58)
    return {
        "reasoning_enabled": not (
            names == {"creative_generation"} and complexity == "simple"
        ),
        "depth": round(depth, 6),
        "exploration": 0.84
        if names
        & {"creative_generation", "counterfactual_analysis", "adversarial_reasoning"}
        else 0.45,
        "verification": 0.90
        if names & {"quantitative_modeling", "evidence_validation", "synthesis"}
        else (0.88 if high_stakes else 0.62),
        "counterfactual": 0.88 if "counterfactual_analysis" in names else 0.38,
        "causal_reasoning": 0.90 if "causal_reasoning" in names else 0.46,
    }


def _output_contract(
    task: str, operations: Mapping[str, float], structured: bool
) -> dict[str, Any]:
    fields = {"conclusions", "assumptions", "uncertainties", "evidence_gaps"}
    extras = {
        "quantitative_modeling": {
            "variables",
            "formulas",
            "calculations",
            "sensitivity",
        },
        "forecasting": {"scenarios", "triggers", "forecast_horizon"},
        "adversarial_reasoning": {
            "failure_modes",
            "counterexamples",
            "rejection_conditions",
        },
        "implementation": {
            "dependencies",
            "steps",
            "acceptance_tests",
            "rollback_conditions",
        },
        "decision_comparison": {"options", "criteria", "tradeoffs", "ranking"},
        "synthesis": {
            "agreements",
            "disagreements",
            "conflict_resolution",
            "final_recommendation",
        },
        "evidence_validation": {
            "validated_claims",
            "unsupported_claims",
            "verification_limits",
        },
    }
    for operation in operations:
        fields |= extras.get(operation, set())
    base = {
        "required_fields": sorted(fields),
        "machine_readable_required": structured,
        "must_separate_fact_assumption_inference": True,
    }
    return task_delivery_contract.apply_explicit_contract(task, operations, base)


def _context(
    task: str, importance: float, operations: Mapping[str, float], dependency_count: int
) -> dict[str, int]:
    system = 450 + int(importance * 380)
    original = max(256, math.ceil(len(task) / 1.7))
    upstream = dependency_count * 900
    reasoning = 500 + int(importance * 1200)
    output = (
        800
        + int(importance * 1700)
        + (
            450
            if set(operations)
            & {"quantitative_modeling", "forecasting", "implementation", "synthesis"}
            else 0
        )
    )
    subtotal = system + original + upstream + reasoning + output
    margin = max(700, int(subtotal * 0.15))
    return {
        "system_prompt_tokens": system,
        "original_task_tokens": original,
        "visible_upstream_tokens": upstream,
        "expected_reasoning_tokens": reasoning,
        "expected_output_tokens": output,
        "safety_margin_tokens": margin,
        "required_context_tokens": subtotal + margin,
    }


def _make_work(
    task: str,
    operation: str,
    domains: Mapping[str, float],
    operations: Mapping[str, float],
    profile: TaskProfile,
    structured: bool,
    objective: str,
    dependencies: tuple[str, ...] = (),
) -> AtomicWork:
    importance = _clamp(
        0.48
        + 0.30 * max(operations.values(), default=0.6)
        + 0.22 * max(domains.values(), default=0.6)
    )
    if set(operations) & {"decision_comparison", "synthesis"}:
        importance = max(importance, 0.88)
    base_error = max(ERROR.get(name, ERROR[operation]) for name in operations)
    weighted_verify = sum(
        VERIFY.get(name, VERIFY[operation]) * weight
        for name, weight in operations.items()
    ) / max(0.001, sum(operations.values()))
    error_cost = _clamp(
        base_error * 0.72 + importance * 0.28 + (0.10 if profile.high_stakes else 0)
    )
    independent = bool(
        set(operations) & {"evidence_validation", "adversarial_reasoning"}
    )
    identity = [objective, sorted(domains.items()), sorted(operations.items())]
    return AtomicWork(
        work_id=_digest("work", identity),
        objective=f"{LABEL[operation]}：{objective}",
        importance=round(importance, 6),
        error_cost=round(error_cost, 6),
        verifiability=round(_clamp(weighted_verify), 6),
        domain_requirements={
            k: round(_clamp(v), 6) for k, v in sorted(domains.items())
        },
        operation_requirements={
            k: round(_clamp(v), 6) for k, v in sorted(operations.items())
        },
        prompt_requirements=_prompt_vector(operations),
        reasoning_requirements=_reasoning_vector(
            operations, profile.high_stakes, profile.complexity
        ),
        context_requirements=_context(task, importance, operations, len(dependencies)),
        output_contract=_output_contract(task, operations, structured),
        independence_requirements={
            "independent_execution_preferred": independent,
            "minimum_independent_copies": 2
            if profile.high_stakes
            and ("analysis" in operations or "evidence_validation" in operations)
            else 1,
            "different_model_required": bool(profile.high_stakes and independent),
            "different_model_family_preferred": bool(
                profile.high_stakes and independent
            ),
            "different_provider_preferred": bool(profile.high_stakes and independent),
        },
        dependencies=tuple(sorted(set(dependencies))),
    )


def _with_dependencies(work: AtomicWork, dependencies: list[str]) -> AtomicWork:
    data = work.to_dict()
    deps = tuple(sorted(set(dependencies)))
    data["dependencies"] = deps
    context = dict(work.context_requirements)
    context["visible_upstream_tokens"] = len(deps) * 900
    subtotal = sum(
        int(context[k])
        for k in (
            "system_prompt_tokens",
            "original_task_tokens",
            "visible_upstream_tokens",
            "expected_reasoning_tokens",
            "expected_output_tokens",
        )
    )
    context["safety_margin_tokens"] = max(700, int(subtotal * 0.15))
    context["required_context_tokens"] = subtotal + context["safety_margin_tokens"]
    data["context_requirements"] = context
    return AtomicWork(**data)


def _finish(
    works: list[AtomicWork],
    task: str,
    profile: TaskProfile,
    structured: bool,
    domains: list[str],
) -> list[AtomicWork]:
    if len(works) <= 1 and profile.complexity == "simple":
        return works
    synthesis = _make_work(
        task,
        "synthesis",
        {x: 1 / len(domains) for x in domains},
        {"synthesis": 1},
        profile,
        structured,
        "综合各工作单元的结论、分歧、证据强度和不确定性",
        tuple(x.work_id for x in works),
    )
    return [*works, synthesis]


def _operation_plan(
    task: str,
    profile: TaskProfile,
    ds: Mapping[str, float],
    os: Mapping[str, float],
    domains: list[str],
    ops: list[str],
    structured: bool,
) -> list[AtomicWork]:
    primary = (
        profile.primary_domain if profile.primary_domain in domains else domains[0]
    )
    works = []
    for op in ops:
        assigned = (
            {x: ds[x] for x in domains}
            if op == "analysis"
            else {
                _special_domain(op, primary, domains): max(
                    ds.get(_special_domain(op, primary, domains), 0.65), 0.65
                )
            }
        )
        works.append(
            _make_work(
                task,
                op,
                assigned,
                {op: os[op]},
                profile,
                structured,
                f"按认知操作独立处理，覆盖领域 {', '.join(assigned)}",
            )
        )
    analysis = [x.work_id for x in works if "analysis" in x.operation_requirements]
    result = []
    for work in works:
        op = next(iter(work.operation_requirements))
        deps = []
        if op in {
            "quantitative_modeling",
            "causal_reasoning",
            "counterfactual_analysis",
        }:
            deps = analysis
        if op == "forecasting":
            deps = analysis + [
                x.work_id
                for x in works
                if "quantitative_modeling" in x.operation_requirements
            ]
        if op == "decision_comparison":
            deps = [
                x.work_id
                for x in works
                if not set(x.operation_requirements)
                & {"decision_comparison", "adversarial_reasoning"}
            ]
        result.append(_with_dependencies(work, deps) if deps else work)
    return _finish(result, task, profile, structured, domains)


def _domain_plan(
    task: str,
    profile: TaskProfile,
    ds: Mapping[str, float],
    os: Mapping[str, float],
    domains: list[str],
    ops: list[str],
    structured: bool,
) -> list[AtomicWork]:
    shared = {x: os[x] for x in ops if x != "adversarial_reasoning"}
    works = [
        _make_work(
            task,
            "analysis",
            {domain: ds[domain]},
            shared,
            profile,
            structured,
            f"以 {domain} 领域为主完成独立全维度评估",
        )
        for domain in domains
    ]
    if "adversarial_reasoning" in ops:
        risk = _special_domain("adversarial_reasoning", domains[0], domains)
        works.append(
            _make_work(
                task,
                "adversarial_reasoning",
                {risk: max(ds.get(risk, 0.65), 0.65)},
                {"adversarial_reasoning": os["adversarial_reasoning"]},
                profile,
                structured,
                "独立寻找失败路径和否决条件",
            )
        )
    return _finish(works, task, profile, structured, domains)


def _hybrid_plan(
    task: str,
    profile: TaskProfile,
    ds: Mapping[str, float],
    os: Mapping[str, float],
    domains: list[str],
    ops: list[str],
    structured: bool,
) -> list[AtomicWork]:
    primary = (
        profile.primary_domain if profile.primary_domain in domains else domains[0]
    )
    works = [
        _make_work(
            task,
            "analysis",
            {d: ds[d]},
            {"analysis": os["analysis"]},
            profile,
            structured,
            f"完成 {d} 领域主分析",
        )
        for d in domains
    ]
    analysis = tuple(x.work_id for x in works)
    for op in ops:
        if op == "analysis":
            continue
        domain = _special_domain(op, primary, domains)
        independent = op in {"evidence_validation", "adversarial_reasoning"}
        works.append(
            _make_work(
                task,
                op,
                {domain: max(ds.get(domain, 0.65), 0.65)},
                {op: os[op]},
                profile,
                structured,
                f"作为共享方法工作单元服务全部领域，主能力领域为 {domain}",
                () if independent else analysis,
            )
        )
    return _finish(works, task, profile, structured, domains)


def _metrics(
    works: list[AtomicWork], ops: list[str], domains: list[str]
) -> dict[str, float]:
    covered_ops = {x for work in works for x in work.operation_requirements}
    covered_domains = {x for work in works for x in work.domain_requirements}
    pairs = overlap = 0.0
    for i, left in enumerate(works):
        for right in works[i + 1 :]:
            pairs += 1
            lo, ro = set(left.operation_requirements), set(right.operation_requirements)
            ld, rd = set(left.domain_requirements), set(right.domain_requirements)
            overlap += 0.5 * len(lo & ro) / max(1, len(lo | ro)) + 0.5 * len(
                ld & rd
            ) / max(1, len(ld | rd))
    duplication = overlap / max(1, pairs)
    independence = sum(
        bool(x.independence_requirements["independent_execution_preferred"])
        for x in works
    ) / len(works)
    operation_coverage = len(set(ops) & covered_ops) / len(ops)
    domain_coverage = len(set(domains) & covered_domains) / len(domains)
    return {
        "operation_coverage": round(operation_coverage, 6),
        "domain_coverage": round(domain_coverage, 6),
        "duplication": round(duplication, 6),
        "independence_ratio": round(independence, 6),
        "estimated_context_tokens": float(
            sum(x.context_requirements["required_context_tokens"] for x in works)
        ),
        "estimated_output_tokens": float(
            sum(x.context_requirements["expected_output_tokens"] for x in works)
        ),
        "structural_complexity": round(
            len(works) + 0.35 * sum(len(x.dependencies) for x in works), 6
        ),
        "interpretation_score": round(
            0.46 * operation_coverage
            + 0.32 * domain_coverage
            + 0.14 * independence
            - 0.08 * duplication,
            6,
        ),
    }


def compile_task_semantics(
    profile: TaskProfile, run: RunConfig, max_interpretations: int = 3
) -> dict[str, Any]:
    task = " ".join(_INPUT_RE.sub(" ", run.task).split())
    ds, os = _scores(task, profile)
    domains = [
        x for x, v in sorted(ds.items(), key=lambda z: (-z[1], z[0])) if v >= 0.22
    ][:4] or [profile.primary_domain or "general"]
    thresholds = {
        "analysis": 0.25,
        "causal_reasoning": 0.45,
        "quantitative_modeling": 0.48,
        "forecasting": 0.48,
        "counterfactual_analysis": 0.48,
        "evidence_validation": 0.50,
        "decision_comparison": 0.48,
        "adversarial_reasoning": 0.50,
        "implementation": 0.48,
        "creative_generation": 0.48,
    }
    ops = [
        x
        for x, v in sorted(os.items(), key=lambda z: (-z[1], z[0]))
        if v >= thresholds[x]
    ]
    if "analysis" not in ops:
        ops.insert(0, "analysis")
    structured = any(
        x in task.casefold()
        for x in ("json", "schema", "机器可读", "严格字段", "response_format")
    )
    builders = (
        ("operation_decomposition", "按认知操作拆解。", _operation_plan),
        ("domain_decomposition", "按专业领域拆解。", _domain_plan),
        ("hybrid_decomposition", "领域主分析与共享方法混合拆解。", _hybrid_plan),
    )
    candidates = []
    seen = set()
    for strategy, rationale, builder in builders:
        works = builder(task, profile, ds, os, domains, ops, structured)
        signature = _digest(
            "signature",
            [
                (
                    sorted(x.domain_requirements),
                    sorted(x.operation_requirements),
                    x.dependencies,
                )
                for x in works
            ],
        )
        if signature in seen:
            continue
        seen.add(signature)
        candidates.append(
            TaskInterpretation(
                _digest("interpretation", [strategy, signature]),
                strategy,
                rationale,
                tuple(works),
                _metrics(works, ops, domains),
            )
        )
    candidates.sort(
        key=lambda x: (
            -float(x.metrics["interpretation_score"]),
            float(x.metrics["structural_complexity"]),
            x.interpretation_id,
        )
    )
    return {
        "version": 5,
        "architecture": "task-semantic-compilation-before-model-market",
        "task_digest": _digest("task", task),
        "task_text_characters": len(task),
        "task_signals": {
            "complexity": profile.complexity,
            "high_stakes": bool(profile.high_stakes),
            "long_context": bool(profile.long_context),
            "requested_context": int(profile.requested_context),
            "active_domains": domains,
            "active_operations": ops,
            "structured_output_requested": structured,
        },
        "domain_scores": ds,
        "operation_scores": os,
        "interpretations": [
            x.to_dict() for x in candidates[: max(1, min(3, int(max_interpretations)))]
        ],
        "phase_a_invariants": {
            "model_ids_read": False,
            "provider_endpoints_read": False,
            "prices_read": False,
            "benchmarks_read": False,
            "fixed_professions_used": False,
            "fixed_seats_used": False,
            "fixed_team_topology_used": False,
        },
    }
