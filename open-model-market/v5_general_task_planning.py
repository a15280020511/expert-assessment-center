"""P0 repair for general self-contained decision-task planning.

The legacy classifier treated generic words such as ``risk`` and ``report`` as
proof that a task was high-stakes and long-context. The semantic compiler then
expanded a small numerical comparison into several independent work units with
near-perfect hard capability demands. Real tasks consequently reached CP-SAT
with no affordable feasible graph.

This module keeps genuinely regulated or safety-critical work conservative, but
uses a compact cost-performance-first interpretation for short, self-contained
numerical decisions. It changes no model/provider routing, tool prohibition,
call ceiling, recovery ceiling, or fail-closed policy.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import model_market
import resource_matrix
import task_semantic_compiler as semantics

_INSTALLED = False
_ORIGINAL_CLASSIFY_TASK = model_market.classify_task
_ORIGINAL_COMPILE_TASK_SEMANTICS = semantics.compile_task_semantics

_STRONG_HIGH_STAKES = (
    "医疗", "临床", "诊断", "治疗", "药物", "用药", "疾病", "手术",
    "法律", "诉讼", "判例", "刑事", "合规", "监管处罚", "合同争议",
    "网络安全", "漏洞利用", "攻击路径", "恶意软件", "数据泄露",
    "军事", "战争", "制裁", "外交危机", "生命安全", "人身安全",
    "medical", "clinical", "diagnosis", "treatment", "medication",
    "legal", "lawsuit", "criminal", "compliance", "cybersecurity",
    "exploit", "malware", "military", "war", "sanction",
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


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _strong_high_stakes(text: str) -> bool:
    folded = text.casefold()
    return any(term.casefold() in folded for term in _STRONG_HIGH_STAKES)


def _explicit_long_context(text: str) -> bool:
    return len(text) > 12_000 or _matches_any(text, _LONG_CONTEXT_PATTERNS)


def _external_evidence_requested(text: str) -> bool:
    return _matches_any(text, _EXTERNAL_EVIDENCE_PATTERNS)


def _compact_decision_task(profile: Any, task: str) -> bool:
    return bool(
        not bool(profile.high_stakes)
        and not bool(profile.long_context)
        and len(task) <= 4_000
        and _matches_any(task, _DECISION_PATTERNS)
        and _matches_any(task, _QUANTITATIVE_PATTERNS)
    )


def classify_task(task: str, run: Any) -> model_market.TaskProfile:
    """Classify generic risk/report wording without false high-stakes expansion."""
    base = _ORIGINAL_CLASSIFY_TASK(task, run)
    high_stakes = _strong_high_stakes(task)
    long_context = _explicit_long_context(task)

    domains = list(base.domains)
    if "research" in domains and not _external_evidence_requested(task):
        domains = [domain for domain in domains if domain != "research"]
    if not domains:
        domains = ["math" if _matches_any(task, _QUANTITATIVE_PATTERNS) else "general"]
    if _matches_any(task, _QUANTITATIVE_PATTERNS) and "math" not in domains:
        domains.append("math")
    domains = domains[:4]

    compact = bool(
        not high_stakes
        and not long_context
        and len(task) <= 4_000
        and _matches_any(task, _DECISION_PATTERNS)
        and _matches_any(task, _QUANTITATIVE_PATTERNS)
    )
    if compact:
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
    if quantitative:
        domains["math"] = max(domains.get("math", 0.0), 0.82)
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
        "短文本、自包含、非高风险的定量决策采用单一整合节点，避免重复拆解和无收益的独立副本。",
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


def compile_task_semantics(profile: Any, run: Any, max_interpretations: int = 3) -> dict[str, Any]:
    baseline = _ORIGINAL_COMPILE_TASK_SEMANTICS(
        profile,
        run,
        max_interpretations=max_interpretations,
    )
    task = " ".join(semantics._INPUT_RE.sub(" ", run.task).split())
    if not _compact_decision_task(profile, task):
        return baseline
    return _compact_interpretation(profile, run, baseline)


def install() -> None:
    """Install the P0 classifier and compact semantic compiler exactly once."""
    global _INSTALLED
    if _INSTALLED:
        return
    model_market.classify_task = classify_task
    semantics.compile_task_semantics = compile_task_semantics
    # resource_matrix imported the compiler by name, so update that bound symbol.
    resource_matrix.compile_task_semantics = compile_task_semantics
    _INSTALLED = True
