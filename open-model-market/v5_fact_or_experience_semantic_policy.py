"""Interpret explicit fact/general-experience delivery categories faithfully.

Production run gov-315-expert / Expert #414 reached a zero-cost model that produced
all requested business sections and scored 1.0, but the legacy obligation compiler
still rejected it with ``missing-task-obligation:classification:fact``.  The task
had explicitly requested the combined category ``事实/一般经验`` and the answer had
explicitly stated that its basis was ``一般行业经验及逻辑推断``.

This layer does not weaken factual discipline.  It only treats an explicitly
requested fact-or-general-experience category as satisfied by an explicit general
experience marker.  Tasks that require facts alone still require a factual label.
"""
from __future__ import annotations

import re

import v5_final_semantic_gate as final_semantic_gate
import v5_run387_hardening as run387_hardening
import v5_task_scope_quality_circuit as task_scope

_BASE = task_scope.business_task_obligation_violations
_FACT_OR_EXPERIENCE_TASK_RE = re.compile(
    r"(?:事实\s*[/／]\s*一般经验|一般经验\s*[/／]\s*事实|事实\s*(?:或|与)\s*一般经验)",
    re.I,
)
_GENERAL_EXPERIENCE_ANSWER_RE = re.compile(
    r"(?:一般行业经验|一般经验|行业经验|通用规律|一般规律|行业规律|社会经济常识|一般常识)",
    re.I,
)


def fact_or_experience_obligation_violations(task: str, answer: str) -> list[str]:
    """Honor an explicit fact/general-experience alternative without false failure."""
    values = list(_BASE(task, answer))
    if (
        _FACT_OR_EXPERIENCE_TASK_RE.search(str(task or ""))
        and _GENERAL_EXPERIENCE_ANSWER_RE.search(str(answer or ""))
    ):
        values = [
            value
            for value in values
            if value != "missing-task-obligation:classification:fact"
        ]
    return list(dict.fromkeys(values))


def install_fact_or_experience_semantic_policy() -> None:
    """Bind the refined classifier into all production semantic call sites."""
    task_scope.business_task_obligation_violations = (
        fact_or_experience_obligation_violations
    )
    run387_hardening.task_obligation_violations = (
        fact_or_experience_obligation_violations
    )
    final_semantic_gate.task_obligation_violations = (
        fact_or_experience_obligation_violations
    )


__all__ = [
    "fact_or_experience_obligation_violations",
    "install_fact_or_experience_semantic_policy",
]
