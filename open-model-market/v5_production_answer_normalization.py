"""Audited label-only normalization for task-derived and normative statements."""
from __future__ import annotations

import re
from hashlib import sha256
from typing import Any

from v5_deterministic_answer_normalization import _cjk_ngrams
from v5_task_constraints import fact_claim_supported, normalized_quantities

_FACT_LINE_RE = re.compile(
    r"^(?P<prefix>\s*(?:[-*+]\s*)?)"
    r"(?P<label>(?:事实|已知事实|fact)(?:[（(][^）)]*[）)])?\s*[:：])"
    r"\s*(?P<body>.+?)\s*$",
    re.IGNORECASE,
)
_DERIVED_MARKER_RE = re.compile(
    r"(?:可选对象|可比指标|大小关系|比较|排序|最低|最高|居中|"
    r"优于|劣于|低于|高于|未给出|未提供|未说明|不能推出|"
    r"无法推出|单项|权重|约束|阈值|因此|意味着|说明|表明|"
    r"用户优先|偏好|原则|规则|止损|必须|不得|禁止|不应|应当|"
    r"建议|首选|备选|不建议|需要|要求|采用|停止|退出|试岗|"
    r"若|如果|条件下|前提|风险|机会成本)",
    re.IGNORECASE,
)
_OPTION_ID_RE = re.compile(
    r"(?:方案|选项)\s*([A-Za-z][A-Za-z0-9_-]*|\d+)",
    re.IGNORECASE,
)
_NORMATIVE_RE = re.compile(
    r"(?:必须|不得|禁止|不应|应当|建议|首选|备选|不建议|"
    r"优先|规则|止损|要求|采用|停止|退出|试岗|条件|前提)",
    re.IGNORECASE,
)


def _task_option_ids(task: str) -> set[str]:
    return {
        match.group(1).casefold()
        for match in _OPTION_ID_RE.finditer(str(task or ""))
    }


def _referenced_option_ids(task: str, body: str) -> set[str]:
    references: set[str] = set()
    for identifier in _task_option_ids(task):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_-]){re.escape(identifier)}(?![A-Za-z0-9_-])",
            re.IGNORECASE,
        )
        if pattern.search(str(body or "")):
            references.add(identifier)
    return references


def _task_anchored(task: str, body: str) -> bool:
    shared = _cjk_ngrams(task, 2) & _cjk_ngrams(body, 2)
    if len(shared) >= 4:
        return True
    return len(shared) >= 1 and len(_referenced_option_ids(task, body)) >= 2


def _relabel_reason(body: str) -> str:
    if _NORMATIVE_RE.search(body):
        return "task-anchored-preference-policy-or-action-is-not-a-fact-label"
    return "task-derived-comparison-or-absence-is-not-a-fact"


def relabel_task_derived_fact_lines(
    task: str,
    answer: str,
) -> tuple[str, dict[str, Any]]:
    """Relabel task-anchored derivations and normative claims without rewriting."""
    original = str(answer or "")
    task_quantities = normalized_quantities(task)
    rows: list[str] = []
    changes: list[dict[str, Any]] = []
    for line_number, line in enumerate(original.splitlines(), start=1):
        match = _FACT_LINE_RE.match(line)
        if not match:
            rows.append(line)
            continue
        body = match.group("body").strip()
        normative = bool(_NORMATIVE_RE.search(body))
        can_relabel = (
            (normative or not fact_claim_supported(task, body))
            and bool(_DERIVED_MARKER_RE.search(body))
            and not (normalized_quantities(body) - task_quantities)
            and _task_anchored(task, body)
        )
        if not can_relabel:
            rows.append(line)
            continue
        repaired = f"{match.group('prefix')}推断：{body}"
        rows.append(repaired)
        changes.append(
            {
                "line_number": line_number,
                "original": line,
                "relabelled": repaired,
                "reason": _relabel_reason(body),
            }
        )
    suffix = "\n" if original.endswith("\n") else ""
    normalized = "\n".join(rows) + suffix
    return normalized, {
        "schema_version": "v5-production-derived-fact-normalization-2",
        "policy": "label-only-task-anchored-no-substantive-rewrite",
        "applied": normalized != original,
        "changes": changes,
        "substantive_text_invented": False,
        "normative_or_preference_statements_may_not_use_fact_label": True,
        "original_answer_sha256": sha256(original.encode("utf-8")).hexdigest(),
        "normalized_answer_sha256": sha256(
            normalized.encode("utf-8")
        ).hexdigest(),
    }


__all__ = ["relabel_task_derived_fact_lines"]
