"""Explicit semantic correction for self-contained V5 planning tasks.

Short, low-risk decisions may be compacted. High-stakes closed-book tabletop
exercises are never collapsed into one model call: safety analysis, operational
coordination, independent red-team review and final synthesis remain separate
work units so the task-global company-diversity constraint has real effect.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import model_market
import task_semantic_compiler as semantics
import v5_closed_book_safety as closed_book_safety
import v5_task_delivery_contract as task_delivery_contract

_ORIGINAL_CLASSIFY_TASK = model_market.classify_task
_ORIGINAL_COMPILE_TASK_SEMANTICS = semantics.compile_task_semantics

_STRONG_HIGH_STAKES = (
    "医疗", "临床", "诊断", "治疗", "药物", "用药", "疾病", "手术",
    "法律", "诉讼", "判例", "刑事", "合规", "监管处罚", "合同争议",
    "网络安全", "漏洞利用", "攻击路径", "恶意软件", "数据泄露",
    "军事", "战争", "制裁", "外交危机", "生命安全", "人身安全",
    "应急", "撤离", "封控", "配电", "火灾", "报警", "失联",
    "medical", "clinical", "diagnosis", "treatment", "medication",
    "legal", "lawsuit", "criminal", "compliance", "cybersecurity",
    "exploit", "malware", "military", "war", "sanction", "emergency",
)
_LONG_CONTEXT_PATTERNS = (
    r"整个(?:代码库|仓库|文档)", r"完整(?:代码库|仓库|文档|合同)全文",
    r"逐行(?:审计|检查|分析)", r"附件全文", r"全文(?:分析|审计|核验)",
    r"entire\s+(?:repository|codebase|document)", r"full[- ]text",
    r"line[- ]by[- ]line", r"attached\s+document",
)
_EXTERNAL_EVIDENCE_PATTERNS = (
    r"核验(?:来源|证据|文献|数据)", r"查证", r"引用(?:来源|文献)",
    r"外部(?:证据|数据|来源)", r"证据审查", r"来源可靠性",
    r"verify\s+(?:sources|evidence|data)", r"external\s+(?:evidence|data)",
    r"cite\s+sources", r"literature\s+review",
)
_DECISION_PATTERNS = (
    r"方案[AB一二甲乙]", r"两种方案", r"多个方案", r"比较", r"选择",
    r"建议", r"盈亏平衡", r"敏感性", r"情景", r"成本", r"净收入",
    r"option\s+[ab]", r"compare", r"choose", r"recommend",
    r"break[- ]even", r"sensitivity", r"scenario", r"cost",
)
_QUANTITATIVE_PATTERNS = (
    r"\d", r"计算", r"公式", r"成本", r"收入", r"概率", r"百分比",
    r"月均", r"总额", r"盈亏平衡", r"calculate", r"formula",
    r"probability", r"percent", r"revenue", r"cost",
)
_FORECAST_PATTERNS = (
    r"情景", r"敏感性", r"未来", r"年期", r"触发条件", r"scenario",
    r"sensitivity", r"forecast", r"future", r"horizon",
)
_CLOSED_BOOK_PATTERNS = (
    r"仅限题面", r"仅依据题面", r"只依据题面", r"不联网", r"不调用工具",
    r"不编造(?:电话号码|外部制度|设备状态|人员位置|专业检测结论|耗电|续航)",
    r"closed[- ]book", r"self[- ]contained", r"no\s+external\s+tools?",
)
_TABLETOP_PATTERNS = (
    r"应急(?:桌面)?推演", r"桌面推演", r"行动时间线", r"风险链",
    r"撤离", r"封控", r"升级条件", r"移交判定", r"失败模式",
    r"tabletop\s+exercise", r"emergency\s+simulation", r"incident\s+timeline",
)


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _strong_high_stakes(text: str) -> bool:
    folded = text.casefold()
    return any(term.casefold() in folded for term in _STRONG_HIGH_STAKES)


def _explicit_long_context(text: str) -> bool:
    return len(text) > 12_000 or _matches_any(text, _LONG_CONTEXT_PATTERNS)


def _external_evidence_requested(text: str) -> bool:
    return _matches_any(text, _EXTERNAL_EVIDENCE_PATTERNS)


def _closed_book_tabletop_task(task: str) -> bool:
    return bool(
        len(task) <= 12_000
        and _matches_any(task, _CLOSED_BOOK_PATTERNS)
        and _matches_any(task, _TABLETOP_PATTERNS)
        and not _external_evidence_requested(task)
    )


def _explicit_delivery_breadth(task: str) -> int:
    markdown = task_delivery_contract.extract_explicit_markdown_contract(task)
    exact_json = task_delivery_contract.extract_explicit_contract(task)
    return max(
        len(markdown.get("exact_markdown_headings", [])),
        len(exact_json.get("exact_top_level_fields", [])),
    )


def _compact_decision_task(profile: Any, task: str) -> bool:
    return bool(
        not bool(profile.high_stakes)
        and not bool(profile.long_context)
        and len(task) <= 4_000
        and _matches_any(task, _DECISION_PATTERNS)
        and _matches_any(task, _QUANTITATIVE_PATTERNS)
    )


def classify_task(task: str, run: Any) -> model_market.TaskProfile:
    """Return a corrected profile without mutating ``model_market`` globals."""
    base = _ORIGINAL_CLASSIFY_TASK(task, run)
    tabletop = _closed_book_tabletop_task(task)
    high_stakes = _strong_high_stakes(task)
    long_context = _explicit_long_context(task)
    delivery_breadth = _explicit_delivery_breadth(task)

    domains = list(base.domains)
    if tabletop:
        domains = [domain for domain in domains if domain not in {"research", "business"}]
        if "security" not in domains:
            domains.insert(0, "security")
    elif "research" in domains and not _external_evidence_requested(task):
        domains = [domain for domain in domains if domain != "research"]
    if not domains:
        domains = ["math" if _matches_any(task, _QUANTITATIVE_PATTERNS) else "general"]
    if not tabletop and _matches_any(task, _QUANTITATIVE_PATTERNS) and "math" not in domains:
        domains.append("math")
    domains = domains[:4]

    compact = bool(
        not high_stakes
        and not long_context
        and len(task) <= 4_000
        and _matches_any(task, _DECISION_PATTERNS)
        and _matches_any(task, _QUANTITATIVE_PATTERNS)
    )
    if tabletop and (high_stakes or delivery_breadth >= 8):
        complexity = "complex"
        complexity_score = max(5, int(base.complexity_score))
    elif tabletop:
        complexity = "medium"
        complexity_score = 3
    elif compact:
        complexity = "simple" if len(task) <= 2_500 else "medium"
        complexity_score = 1 if complexity == "simple" else 2
    else:
        complexity_score = int(base.complexity_score)
        complexity = str(base.complexity)
        if high_stakes and complexity_score < 4:
            complexity_score = 4
            complexity = "complex"

    minimum_context = int(getattr(run, "minimum_context_length", 16_384) or 16_384)
    maximum_output = int(getattr(run, "max_completion_tokens", 3_000) or 3_000)
    requested_context = max(minimum_context, int(len(task) / 2.5) + 3 * maximum_output)
    if long_context:
        requested_context = max(requested_context, 65_536)

    primary = domains[0]
    secondary = domains[1] if len(domains) > 1 else primary
    return replace(
        base,
        domains=domains,
        primary_domain=primary,
        secondary_domain=secondary,
        complexity=complexity,
        complexity_score=complexity_score,
        high_stakes=high_stakes,
        long_context=long_context,
        requested_context=requested_context,
    )


def _compact_interpretation(profile: Any, run: Any, baseline: dict[str, Any]) -> dict[str, Any]:
    task = " ".join(semantics._INPUT_RE.sub(" ", run.task).split())
    structured = any(
        token in task.casefold()
        for token in ("json", "schema", "机器可读", "严格字段", "response_format")
    )
    quantitative = _matches_any(task, _QUANTITATIVE_PATTERNS)
    forecasting = _matches_any(task, _FORECAST_PATTERNS)
    external_evidence = _external_evidence_requested(task)

    domains: dict[str, float] = {}
    primary = str(profile.primary_domain or "general")
    if primary not in {"research", "general"}:
        domains[primary] = 0.72
    if quantitative and not domains:
        domains["math"] = 0.82
    if external_evidence:
        domains["research"] = 0.68
    if not domains:
        domains["general"] = 0.72

    operations: dict[str, float] = {
        "analysis": 0.72,
        "decision_comparison": 0.84,
    }
    if quantitative:
        operations["quantitative_modeling"] = 0.84
    if forecasting:
        operations["forecasting"] = 0.66
    if external_evidence:
        operations["evidence_validation"] = 0.66

    work = semantics._make_work(
        task,
        "decision_comparison",
        domains,
        operations,
        profile,
        structured,
        "在一个整合工作单元中完成计算、情景比较、风险边界和可执行建议",
    )
    work_data = work.to_dict()
    work_data["independence_requirements"] = {
        "independent_execution_preferred": False,
        "minimum_independent_copies": 1,
        "different_model_required": False,
        "different_model_family_preferred": False,
        "different_provider_preferred": False,
    }
    work = semantics.AtomicWork(**work_data)
    operation_names = list(operations)
    domain_names = list(domains)
    strategy = "cost_performance_compact_decision"
    signature = semantics._digest(
        "signature",
        [(sorted(work.domain_requirements), sorted(work.operation_requirements), work.dependencies)],
    )
    interpretation = semantics.TaskInterpretation(
        semantics._digest("interpretation", [strategy, signature]),
        strategy,
        "短文本、自包含、非高风险的定量决策采用单一整合节点。",
        (work,),
        semantics._metrics([work], operation_names, domain_names),
    )

    result = dict(baseline)
    signals = dict(result.get("task_signals", {}))
    signals.update(
        {
            "complexity": profile.complexity,
            "high_stakes": False,
            "long_context": False,
            "requested_context": int(profile.requested_context),
            "active_domains": domain_names,
            "active_operations": operation_names,
            "cost_performance_compaction_applied": True,
            "minimum_planned_work_units": 1,
        }
    )
    result["architecture"] = "task-semantic-compilation-with-cost-performance-compaction"
    result["task_signals"] = signals
    result["interpretations"] = [interpretation.to_dict()]
    result["domain_scores"] = {
        **dict(result.get("domain_scores", {})),
        **{domain: round(weight, 6) for domain, weight in domains.items()},
    }
    result["operation_scores"] = {
        **dict(result.get("operation_scores", {})),
        **{operation: round(weight, 6) for operation, weight in operations.items()},
    }
    return result


def _strict_work(work: semantics.AtomicWork, task: str) -> semantics.AtomicWork:
    data = work.to_dict()
    data["importance"] = max(0.95, float(data["importance"]))
    data["error_cost"] = max(0.95, float(data["error_cost"]))
    data["prompt_requirements"] = {
        **dict(data["prompt_requirements"]),
        "scope_control": 0.99,
        "uncertainty_calibration": 0.99,
        "evidence_discipline": 0.98,
        "structured_delivery": 0.98,
    }
    data["reasoning_requirements"] = {
        **dict(data["reasoning_requirements"]),
        "reasoning_enabled": True,
        "depth": 0.97,
        "verification": 0.96,
        "counterfactual": max(0.72, float(data["reasoning_requirements"].get("counterfactual", 0.0))),
    }
    data["output_contract"] = {
        **dict(data["output_contract"]),
        **closed_book_safety.strict_contract_metadata(task),
        "must_separate_fact_assumption_inference": True,
    }
    data["independence_requirements"] = {
        "independent_execution_preferred": True,
        "minimum_independent_copies": 1,
        "different_model_required": False,
        "different_model_family_preferred": False,
        "different_provider_preferred": True,
    }
    return semantics.AtomicWork(**data)


def _compact_tabletop_interpretation(
    profile: Any,
    run: Any,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """Keep a single node only for genuinely low-risk, short tabletops."""
    task = " ".join(semantics._INPUT_RE.sub(" ", run.task).split())
    domains = {"security": 0.82}
    operations = {"analysis": 0.82, "decision_comparison": 0.78}
    work = semantics._make_work(
        task,
        "analysis",
        domains,
        operations,
        profile,
        False,
        "整合低风险闭卷时间线、行动边界和结束条件",
    )
    strategy = "closed_book_tabletop_compact_low_risk"
    interpretation = semantics.TaskInterpretation(
        semantics._digest("interpretation", [strategy, work.work_id]),
        strategy,
        "仅低风险、短交付闭卷推演允许单节点整合。",
        (work,),
        semantics._metrics([work], list(operations), list(domains)),
    )
    result = dict(baseline)
    signals = dict(result.get("task_signals", {}))
    signals.update(
        {
            "complexity": profile.complexity,
            "high_stakes": False,
            "long_context": False,
            "active_domains": list(domains),
            "active_operations": list(operations),
            "closed_book_tabletop_compaction_applied": True,
            "closed_book_tabletop_decomposition_applied": False,
            "external_evidence_required": False,
            "minimum_planned_work_units": 1,
        }
    )
    result["architecture"] = "closed-book-tabletop-low-risk-compaction"
    result["task_signals"] = signals
    result["interpretations"] = [interpretation.to_dict()]
    return result


def _high_stakes_tabletop_interpretation(
    profile: Any,
    run: Any,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """Compile four company-diverse work units for high-stakes tabletops."""
    task = " ".join(semantics._INPUT_RE.sub(" ", run.task).split())
    domains = {"security": 0.96}
    if "legal" in profile.domains:
        domains["legal"] = 0.64

    hazard = _strict_work(
        semantics._make_work(
            task,
            "analysis",
            domains,
            {"analysis": 0.96, "causal_reasoning": 0.78},
            profile,
            False,
            "独立识别生命安全风险链、危险区、禁止动作、触发条件和未知项；不得提出单人进入或检查未知危险区",
        ),
        task,
    )
    coordination = _strict_work(
        semantics._make_work(
            task,
            "decision_comparison",
            domains,
            {"analysis": 0.86, "decision_comparison": 0.95},
            profile,
            False,
            "独立制定时间线、通信降级、门禁核验、人员清点、照明资源、记录和移交规则；不得虚构续航或人员状态",
        ),
        task,
    )
    red_team = _strict_work(
        semantics._make_work(
            task,
            "adversarial_reasoning",
            {"security": 0.98},
            {"adversarial_reasoning": 0.99},
            profile,
            False,
            "独立寻找危险建议、题面外数字、未经证实的安全结论、失败模式重复和交付契约遗漏",
        ),
        task,
    )
    red_data = red_team.to_dict()
    red_data["independence_requirements"] = {
        "independent_execution_preferred": True,
        "minimum_independent_copies": 1,
        "different_model_required": True,
        "different_model_family_preferred": True,
        "different_provider_preferred": True,
    }
    red_team = semantics.AtomicWork(**red_data)

    synthesis = _strict_work(
        semantics._make_work(
            task,
            "synthesis",
            domains,
            {"synthesis": 1.0},
            profile,
            False,
            "只依据题面与三个上游工作单元，按用户显式交付契约形成最终报告；删除任何题面外数字、危险动作或未经证实的状态",
            (hazard.work_id, coordination.work_id, red_team.work_id),
        ),
        task,
    )
    works = [hazard, coordination, red_team, synthesis]
    operation_names = [
        "analysis",
        "decision_comparison",
        "adversarial_reasoning",
        "synthesis",
    ]
    domain_names = list(domains)
    strategy = "closed_book_tabletop_high_stakes_decomposed"
    signature = semantics._digest(
        "signature",
        [
            (
                sorted(work.domain_requirements),
                sorted(work.operation_requirements),
                work.dependencies,
            )
            for work in works
        ],
    )
    interpretation = semantics.TaskInterpretation(
        semantics._digest("interpretation", [strategy, signature]),
        strategy,
        "高风险闭卷任务必须由安全分析、运营协调、独立红队和最终综合四个不同公司节点完成；禁止单节点压缩。",
        tuple(works),
        semantics._metrics(works, operation_names, domain_names),
    )

    result = dict(baseline)
    signals = dict(result.get("task_signals", {}))
    signals.update(
        {
            "complexity": "complex",
            "high_stakes": True,
            "long_context": False,
            "requested_context": int(profile.requested_context),
            "active_domains": domain_names,
            "active_operations": operation_names,
            "closed_book_tabletop_compaction_applied": False,
            "cost_performance_compaction_applied": False,
            "closed_book_tabletop_decomposition_applied": True,
            "external_evidence_required": False,
            "minimum_planned_work_units": 4,
            "minimum_distinct_model_companies": 4,
            "high_stakes_degraded_delivery_allowed": False,
            "explicit_delivery_section_count": _explicit_delivery_breadth(task),
        }
    )
    result["architecture"] = "closed-book-tabletop-high-stakes-four-work-decomposition"
    result["task_signals"] = signals
    result["interpretations"] = [interpretation.to_dict()]
    result["domain_scores"] = {
        **dict(result.get("domain_scores", {})),
        "research": 0.0,
        **{domain: round(weight, 6) for domain, weight in domains.items()},
    }
    result["operation_scores"] = {
        **dict(result.get("operation_scores", {})),
        "evidence_validation": 0.0,
        "forecasting": 0.0,
        **{operation: 1.0 for operation in operation_names},
    }
    return result


def compile_task_semantics(profile: Any, run: Any, max_interpretations: int = 3) -> dict[str, Any]:
    baseline = _ORIGINAL_COMPILE_TASK_SEMANTICS(
        profile,
        run,
        max_interpretations=max_interpretations,
    )
    task = " ".join(semantics._INPUT_RE.sub(" ", run.task).split())
    if _closed_book_tabletop_task(task):
        if bool(profile.high_stakes) or _explicit_delivery_breadth(task) >= 8:
            return _high_stakes_tabletop_interpretation(profile, run, baseline)
        return _compact_tabletop_interpretation(profile, run, baseline)
    if not _compact_decision_task(profile, task):
        return baseline
    return _compact_interpretation(profile, run, baseline)


def install() -> None:
    """Deprecated compatibility no-op; global monkey patching is forbidden."""
    return None
