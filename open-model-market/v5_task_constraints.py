"""Stable public facade for constitutional task constraints and evidence gates.

The implementation module contains the general fail-closed validators. This
facade adds quantity-local, spatial-local, sensory-local, and task-anchored
deictic canonicalization without weakening polarity, object, unit, quantity,
or explicit location binding.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

import v5_task_constraints_impl as _impl

for _export_name in dir(_impl):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_impl, _export_name)

_QUANTITY_LITERAL_PATTERN = (
    r"\d+(?:\.\d+)?\s*(?:SLA|秒|分钟|小时|天|周|月|年|米|公里|千米|"
    r"公斤|克|人|名|位|次|%|％|件|台|部|套|支|辆|本|份|箱|包|瓶|"
    r"枚|张|把|只|艘|架|顶|亿元|万元|元|块|人民币|rmb|cny|yuan|"
    r"美元|美金|usd)"
)
_SCALED_YUAN_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>亿元|万元)"
)
_CARDINALITY_PREFIX_RE = re.compile(
    rf"(?:共有|共计|总计|合计|共)(?=\s*{_QUANTITY_LITERAL_PATTERN})",
    re.IGNORECASE,
)
_QUANTITY_COUNT_LINK_RE = re.compile(
    rf"数量\s*(?:为|是|有)?(?=\s*{_QUANTITY_LITERAL_PATTERN})",
    re.IGNORECASE,
)
_REMAINING_QUANTITY_RE = re.compile(
    rf"剩余\s*电量\s*(?:为|是|有)?(?=\s*{_QUANTITY_LITERAL_PATTERN})",
    re.IGNORECASE,
)
_SPATIAL_EXISTENTIAL_RE = re.compile(
    r"(?P<location>旁边|附近)\s*"
    r"(?:放着|放有|摆着|摆放着|有)\s*"
    r"(?:一个|一台|一部|一件|一只|一把)?(?=\S)"
)
_SENSORY_EXISTENTIAL_RE = re.compile(
    r"(?:闻到|嗅到)\s*"
    r"(?:(?:来源|出处)(?:不明|未知)的?)?\s*"
    r"(?P<odor>[\u4e00-\u9fff]{1,12}?味)"
)
_GENERIC_ONSITE_RE = re.compile(
    rf"(?P<subject>{_QUANTITY_LITERAL_PATTERN}"
    r"[^，。！？!?；;\n]{0,24}?)(?:在|位于)现场"
)


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        return rendered.rstrip("0").rstrip(".")
    return rendered


def _expand_scaled_yuan(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        try:
            number = Decimal(match.group("number"))
        except InvalidOperation:
            return match.group(0)
        factor = (
            Decimal("100000000")
            if match.group("unit") == "亿元"
            else Decimal("10000")
        )
        return _decimal_text(number * factor) + "元"

    return _SCALED_YUAN_RE.sub(replace, str(value or ""))


def normalized_quantities(text: str) -> set[tuple[str, str, str]]:
    """Normalize scaled Chinese currency units without losing magnitude."""
    return _impl.normalized_quantities(_expand_scaled_yuan(text))


def original_quantity_tokens(text: str) -> list[str]:
    """Add scaled currency spellings to the authoritative native token list."""
    rendered = str(text or "")
    values = [
        re.sub(r"\s+", "", match.group(0))
        for match in _SCALED_YUAN_RE.finditer(rendered)
    ]
    for token in _impl.original_quantity_tokens(rendered):
        if token and token not in values:
            values.append(token)
    return values


def closed_world_numeric_prompt(
    task: str,
    constraints: _impl.TaskConstraints | Mapping[str, Any] | None = None,
) -> str:
    policy = constraints or _impl.compile_task_constraints(task)
    precise_allowed = (
        bool(policy.get("unsupported_precise_quantities_allowed", True))
        if isinstance(policy, Mapping)
        else policy.unsupported_precise_quantities_allowed
    )
    if precise_allowed:
        return ""
    rendered = "[" + "，".join(original_quantity_tokens(task)) + "]"
    return (
        "封闭世界精确数量规则（不可覆盖）：允许出现的‘数值+单位’仅限"
        f"题面原样集合：{rendered}。回答必须保留题面原始单位。除题面直接等式"
        "及其原样复述外，禁止输出任何带单位的精确数量，包括算术中间结果、"
        "示例值、替代月份或年份、敏感性阈值、预测值和派生情景。优先采用定性表述；"
        "排序、比较和缺失条件属于推断，不得标为题面事实。"
    )


def _canonicalize_quantity_local_language(value: str) -> str:
    """Canonicalize only syntax directly bound to a quantity or location."""
    rendered = _expand_scaled_yuan(str(value or ""))
    rendered = _CARDINALITY_PREFIX_RE.sub("只有", rendered)
    rendered = _QUANTITY_COUNT_LINK_RE.sub("只有", rendered)
    rendered = _REMAINING_QUANTITY_RE.sub("剩余", rendered)
    rendered = _SPATIAL_EXISTENTIAL_RE.sub(r"\g<location>有", rendered)
    rendered = _SENSORY_EXISTENTIAL_RE.sub(r"有\g<odor>", rendered)
    for pattern, replacement in (
        (r"(?:已经|已)(?=交接)", ""),
        (r"实际\s*可(?=确认)", ""),
        (r"只能(?=确认)", ""),
        (r"能够(?=确认)", ""),
        (r"可(?=确认)", ""),
    ):
        rendered = re.sub(pattern, replacement, rendered)
    return rendered


def _onsite_source_matches(
    task: str,
    subject: str,
) -> list[Mapping[str, Any]]:
    quantities = _impl.normalized_quantities(subject)
    skeleton = _impl._quantity_skeleton(subject)
    if not quantities or not skeleton:
        return []
    matches: list[Mapping[str, Any]] = []
    for row in _impl._source_evidence_rows(task):
        row_quantities = set(row.get("quantities", set())) | set(
            row.get("contextual_quantities", set())
        )
        if not quantities.issubset(row_quantities):
            continue
        source_skeletons = tuple(
            dict.fromkeys(
                value
                for value in (
                    str(row.get("quantity_skeleton", "")),
                    str(row.get("contextual_quantity_skeleton", "")),
                )
                if value
            )
        )
        if any(
            skeleton in source_skeleton
            or _impl._reordered_semantic_match(skeleton, source_skeleton)
            for source_skeleton in source_skeletons
        ):
            matches.append(row)
    return matches


def _canonicalize_task_anchored_onsite(
    task: str,
    claim: str,
) -> tuple[str, bool]:
    """Resolve generic 'onsite' only when source has no explicit spatial anchor."""
    blocked = False

    def replace(match: re.Match[str]) -> str:
        nonlocal blocked
        subject = match.group("subject").strip()
        source_rows = _onsite_source_matches(task, subject)
        if not source_rows:
            return match.group(0)
        if any(not set(row.get("spatial_anchors", set())) for row in source_rows):
            return subject
        blocked = True
        return match.group(0)

    return _GENERIC_ONSITE_RE.sub(replace, claim), blocked


def fact_claim_supported(task: str, claim: str) -> bool:
    """Validate a fact after safe local and task-anchored canonicalization."""
    canonical_task = _canonicalize_quantity_local_language(task)
    canonical_claim = _canonicalize_quantity_local_language(claim)
    canonical_claim, blocked = _canonicalize_task_anchored_onsite(
        canonical_task,
        canonical_claim,
    )
    if blocked:
        return False
    return _impl.fact_claim_supported(canonical_task, canonical_claim)


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
            normalized_quantities(answer) - normalized_quantities(task)
        )
        if introduced:
            rendered_values: list[str] = []
            for lo, hi, unit in introduced[:16]:
                suffix = f"-{hi}" if hi else ""
                rendered_values.append(f"{lo}{suffix}:{unit}")
            violations.append(
                "closed-world-unsupported-quantity:"
                + ",".join(rendered_values)
            )

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
