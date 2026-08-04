"""Audited label-only normalization for task-derived fact statements."""
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
    r"无法推出|单项|权重|约束|阈值|因此|意味着|说明|表明)",
    re.IGNORECASE,
)


def _task_anchored(task: str, body: str) -> bool:
    return len(_cjk_ngrams(task, 2) & _cjk_ngrams(body, 2)) >= 4


def relabel_task_derived_fact_lines(
    task: str,
    answer: str,
) -> tuple[str, dict[str, Any]]:
    """Relabel only unsupported, task-anchored derivations; never rewrite text."""
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
        can_relabel = (
            not fact_claim_supported(task, body)
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
                "reason": "task-derived-comparison-or-absence-is-not-a-fact",
            }
        )
    suffix = "\n" if original.endswith("\n") else ""
    normalized = "\n".join(rows) + suffix
    return normalized, {
        "schema_version": "v5-production-derived-fact-normalization-1",
        "policy": "label-only-task-anchored-no-substantive-rewrite",
        "applied": normalized != original,
        "changes": changes,
        "substantive_text_invented": False,
        "original_answer_sha256": sha256(original.encode("utf-8")).hexdigest(),
        "normalized_answer_sha256": sha256(
            normalized.encode("utf-8")
        ).hexdigest(),
    }


__all__ = ["relabel_task_derived_fact_lines"]
