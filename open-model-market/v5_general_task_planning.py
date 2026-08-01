"""Task-independent semantic policy for the V5 production planner.

Production architecture is never selected from a named scenario, benchmark, or
one-off task pattern. The base semantic compiler remains responsible for domain
and operation detection; this module adds generic structural, scope and
consequence signals and prevents case-derived compaction.
"""
from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Any

import model_market
import task_semantic_compiler as semantics
import v5_task_delivery_contract as delivery_contract

_ORIGINAL_CLASSIFY_TASK = model_market.classify_task
_ORIGINAL_COMPILE_TASK_SEMANTICS = semantics.compile_task_semantics

_BULLET_RE = re.compile(r"(?m)^\s*(?:[-*+]\s+|\d{1,3}[.)、]\s+)")
_SENTENCE_RE = re.compile(r"[。！？!?;；\n]+")
_EXPLICIT_INDEPENDENCE_RE = re.compile(
    r"(?:独立|分别|各自|并行|交叉验证|红队|复核|裁判|反证|"
    r"independent|separately|cross[- ]check|red[- ]team|review|judge)",
    re.IGNORECASE,
)
_LARGE_SCOPE_RE = re.compile(
    r"(?:全仓|整个代码库|全部代码|所有文件|逐行|完整语料|全部记录|"
    r"whole\s+(?:repository|codebase|corpus|archive)|"
    r"entire\s+(?:repository|codebase|corpus|archive)|"
    r"all\s+(?:files|records|documents)|line[- ]by[- ]line)",
    re.IGNORECASE,
)
_HIGH_CONSEQUENCE_RE = re.compile(
    r"(?:生命安全|人身安全|医疗|临床|用药|法律|合规|安全漏洞|"
    r"网络安全|财务报表|监管|刑事|民事责任|"
    r"life\s+safety|clinical|medical|medication|legal|compliance|"
    r"security\s+vulnerabilit(?:y|ies)|cybersecurity|regulatory|"
    r"financial\s+statements?)",
    re.IGNORECASE,
)


def _structural_signals(task: str) -> dict[str, int | bool]:
    text = str(task or "")
    bullets = len(_BULLET_RE.findall(text))
    clauses = sum(1 for value in _SENTENCE_RE.split(text) if value.strip())
    json_contract = delivery_contract.extract_explicit_contract(text)
    markdown_contract = delivery_contract.extract_explicit_markdown_contract(
        text
    )
    table_contract = delivery_contract.extract_explicit_table_contract(text)
    contract_items = max(
        len(json_contract.get("exact_top_level_fields", [])),
        len(markdown_contract.get("exact_markdown_headings", [])),
        len(table_contract.get("exact_table_columns", [])),
    )
    independence_markers = len(_EXPLICIT_INDEPENDENCE_RE.findall(text))
    return {
        "characters": len(text),
        "enumerated_requirements": bullets,
        "semantic_clauses": clauses,
        "explicit_contract_items": contract_items,
        "independence_markers": independence_markers,
        "explicit_output_contract": bool(
            json_contract or markdown_contract or table_contract
        ),
        "large_input_scope": bool(_LARGE_SCOPE_RE.search(text)),
        "high_consequence_domain": bool(_HIGH_CONSEQUENCE_RE.search(text)),
    }


def _generic_complexity_floor(signals: dict[str, int | bool]) -> int:
    """Compute a monotone structural floor without scenario-specific rules."""
    characters = int(signals["characters"])
    bullets = int(signals["enumerated_requirements"])
    clauses = int(signals["semantic_clauses"])
    contract_items = int(signals["explicit_contract_items"])
    independence = int(signals["independence_markers"])

    breadth = max(bullets, min(clauses, 24), contract_items)
    score = int(math.ceil(math.log2(1 + max(0, characters) / 900)))
    score += int(breadth >= 4) + int(breadth >= 8) + int(breadth >= 16)
    score += int(independence >= 1) + int(independence >= 3)
    score += int(bool(signals["large_input_scope"]))
    score += 2 * int(bool(signals["high_consequence_domain"]))
    return max(0, min(7, score))


def classify_task(task: str, run: Any) -> model_market.TaskProfile:
    """Classify through generic domain, structure, scope and risk features."""
    base = _ORIGINAL_CLASSIFY_TASK(task, run)
    signals = _structural_signals(task)
    high_stakes = bool(
        base.high_stakes or signals["high_consequence_domain"]
    )
    score = max(
        int(base.complexity_score),
        _generic_complexity_floor(signals),
        4 if high_stakes else 0,
    )
    complexity = (
        "simple" if score <= 1 else "medium" if score <= 3 else "complex"
    )

    minimum_context = int(
        getattr(run, "minimum_context_length", 16_384) or 16_384
    )
    maximum_output = int(
        getattr(run, "max_completion_tokens", 3_000) or 3_000
    )
    structural_context = max(
        minimum_context,
        int(len(str(task or "")) / 2.5) + 3 * maximum_output,
    )
    long_context = bool(
        len(str(task or "")) > 12_000 or signals["large_input_scope"]
    )
    requested_context = (
        max(structural_context, 65_536)
        if long_context
        else structural_context
    )
    return replace(
        base,
        complexity=complexity,
        complexity_score=score,
        high_stakes=high_stakes,
        long_context=long_context,
        requested_context=requested_context,
    )


def compile_task_semantics(
    profile: Any,
    run: Any,
    max_interpretations: int = 3,
) -> dict[str, Any]:
    """Compile only through the generic semantic compiler.

    No named task, benchmark, domain example, or closed-book scenario is allowed
    to force a node count, role, architecture, output schema, or integration
    strategy. Structural signals are recorded for downstream optimization.
    """
    result = dict(
        _ORIGINAL_COMPILE_TASK_SEMANTICS(
            profile,
            run,
            max_interpretations=max_interpretations,
        )
    )
    signals = dict(result.get("task_signals", {}))
    structural = _structural_signals(
        str(getattr(run, "task", "") or "")
    )
    signals.update(
        {
            "complexity": profile.complexity,
            "complexity_score": int(profile.complexity_score),
            "high_stakes": bool(profile.high_stakes),
            "long_context": bool(profile.long_context),
            "requested_context": int(profile.requested_context),
            "structural_signals": structural,
            "task_specific_production_branching": False,
            "case_derived_compaction_applied": False,
            "architecture_selection_policy": (
                "generic-semantic-matrix-only"
            ),
        }
    )
    result["architecture"] = "task-independent-semantic-compilation"
    result["task_signals"] = signals
    return result


def install() -> None:
    """Deprecated compatibility no-op; global monkey patching is forbidden."""
    return None
