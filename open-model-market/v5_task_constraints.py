"""Stable public facade for constitutional task constraints and evidence gates.

The implementation module contains the general fail-closed validators. This
facade adds quantity-local Chinese cardinality canonicalization without
weakening polarity, spatial, unit, or quantity binding requirements.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

import v5_task_constraints_impl as _impl

for _export_name in dir(_impl):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_impl, _export_name)

_QUANTITY_TOKEN = (
    r"\d+(?:\.\d+)?\s*(?:SLA|秒|分钟|小时|天|周|月|年|米|公里|千米|"
    r"公斤|克|人|名|位|次|%|％|件|台|部|套|支|辆|本|份|箱|包|瓶|"
    r"枚|张|把|只|艘|架|顶|元|块|人民币|rmb|cny|yuan|美元|美金|usd)"
)
_CARDINALITY_PREFIX_RE = re.compile(
    rf"(?:共有|共计|总计|合计|共)(?=\s*{_QUANTITY_TOKEN})",
    re.IGNORECASE,
)
_QUANTITY_COUNT_LINK_RE = re.compile(
    rf"数量\s*(?:为|是|有)?(?=\s*{_QUANTITY_TOKEN})",
    re.IGNORECASE,
)
_REMAINING_QUANTITY_RE = re.compile(
    rf"剩余\s*电量\s*(?:为|是|有)?(?=\s*{_QUANTITY_TOKEN})",
    re.IGNORECASE,
)


def _canonicalize_quantity_local_language(value: str) -> str:
    """Canonicalize only syntax directly bound to an explicit quantity."""
    rendered = str(value or "")
    rendered = _CARDINALITY_PREFIX_RE.sub("只有", rendered)
    rendered = _QUANTITY_COUNT_LINK_RE.sub("只有", rendered)
    rendered = _REMAINING_QUANTITY_RE.sub("剩余", rendered)
    for pattern, replacement in (
        (r"(?:已经|已)(?=交接)", ""),
        (r"实际\s*可(?=确认)", ""),
        (r"只能(?=确认)", ""),
        (r"能够(?=确认)", ""),
        (r"可(?=确认)", ""),
    ):
        rendered = re.sub(pattern, replacement, rendered)
    return rendered


def fact_claim_supported(task: str, claim: str) -> bool:
    """Validate a fact after safe quantity-local surface canonicalization."""
    return _impl.fact_claim_supported(
        _canonicalize_quantity_local_language(task),
        _canonicalize_quantity_local_language(claim),
    )


def validate_answer_evidence(
    task: str,
    answer: str,
    constraints: _impl.TaskConstraints | Mapping[str, Any] | None = None,
) -> list[str]:
    policy = constraints or _impl.compile_task_constraints(task)
    if isinstance(policy, Mapping):
        external_facts_allowed = bool(policy.get("external_facts_allowed", True))
        precise_allowed = bool(
            policy.get("unsupported_precise_quantities_allowed", True)
        )
        provenance_required = bool(policy.get("fact_provenance_required", False))
    else:
        external_facts_allowed = policy.external_facts_allowed
        precise_allowed = policy.unsupported_precise_quantities_allowed
        provenance_required = policy.fact_provenance_required

    violations: list[str] = []
    if not precise_allowed:
        introduced = sorted(
            _impl.normalized_quantities(answer)
            - _impl.normalized_quantities(task)
        )
        if introduced:
            rendered = ",".join(
                f"{lo}{('-' + hi) if hi else ''}:{unit}"
                for lo, hi, unit in introduced[:16]
            )
            violations.append("closed-world-unsupported-quantity:" + rendered)

    if provenance_required or not external_facts_allowed:
        unsupported = [
            match.group("claim").strip()
            for match in _impl._FACT_LINE_RE.finditer(str(answer or ""))
            if not fact_claim_supported(task, match.group("claim"))
        ]
        if unsupported:
            violations.append(
                "unsupported-fact-label:"
                + " | ".join(value[:120] for value in unsupported[:8])
            )
    return list(dict.fromkeys(violations))
