"""Stable public facade for constitutional task constraints and evidence gates.

The implementation module contains the general validators. This facade adds
quantity-local, spatial-local, sensory-local and task-anchored canonicalization,
and applies the production constitutional default that audited degraded success
is allowed unless the user explicitly denies partial or degraded delivery.
Closed-world tasks keep external facts closed while allowing exact quantities
that are transparently derived from authoritative task inputs when the task
itself requires calculation, thresholds or sensitivity analysis.
"""
from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

import v5_task_constraints_impl as _impl

for _export_name in dir(_impl):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_impl, _export_name)


def compile_task_constraints(task: str) -> _impl.TaskConstraints:
    """Compile explicit user language with default audited degradation allowed."""
    compiled = _impl.compile_task_constraints(task)
    if compiled.degradation_authorization != "default_denied":
        return compiled
    return replace(
        compiled,
        degradation_authorization="default_allowed",
        allow_degraded_success=True,
        policy="explicit-deny-overrides-allow-default-audited-degradation",
    )


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
_DERIVED_QUANTITY_TASK_RE = re.compile(
    r"(?:计算|测算|算出|求解|公式|算式|总成本|期望成本|预期成本|"
    r"临界值|阈值|切换条件|敏感性|敏感度|误差|情景|"
    r"calculate|calculation|compute|formula|threshold|break[- ]?even|"
    r"sensitivity|scenario)",
    re.IGNORECASE,
)
_DERIVED_QUANTITY_CONTEXT_RE = re.compile(
    r"(?:计算|测算|推导|派生|公式|算式|总成本|期望|预期|"
    r"临界|阈值|切换|敏感|情景|结果|"
    r"calculate|computed|derived|formula|threshold|break[- ]?even|"
    r"sensitivity|scenario|=|＝|\+|×|\*|÷|/)",
    re.IGNORECASE,
)
_FACT_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:\*\*)?(?:事实|已知事实|fact)"
    r"(?:\s*[（(][^）)]*[）)])?(?:\*\*)?\s*[:：-]",
    re.IGNORECASE,
)


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        return rendered.rstrip("0").rstrip(".")
    return rendered


def _expand_scaled_yuan(value: str) -> str:
    def replace_scaled(match: re.Match[str]) -> str:
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

    return _SCALED_YUAN_RE.sub(replace_scaled, str(value or ""))


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


def _derived_quantities_requested(task: str) -> bool:
    """Return true only when this task explicitly needs numerical derivation."""
    return bool(_DERIVED_QUANTITY_TASK_RE.search(str(task or "")))


def closed_world_numeric_prompt(
    task: str,
    constraints: _impl.TaskConstraints | Mapping[str, Any] | None = None,
) -> str:
    policy = constraints or compile_task_constraints(task)
    precise_allowed = (
        bool(policy.get("unsupported_precise_quantities_allowed", True))
        if isinstance(policy, Mapping)
        else policy.unsupported_precise_quantities_allowed
    )
    if precise_allowed:
        return ""
    rendered = "[" + "，".join(original_quantity_tokens(task)) + "]"
    if _derived_quantities_requested(task):
        return (
            "封闭世界精确数量规则（不可覆盖）：题面原样数量是唯一允许的外部"
            f"数值来源：{rendered}。不得引入任何题外参数、统计值、年份、价格、"
            "概率或经验数字。因为本任务明确要求计算/临界值/敏感性，可以输出由"
            "题面数量通过显式算式直接得到的派生精确数量；每个题面外数值必须在"
            "同一句、同一条或同一表格字段中标明计算/推导/结果/临界值/敏感性等"
            "派生语义，或展示等号/运算关系，使其 provenance 可复核。派生数量不得"
            "标为题面事实。所有确定数据仍必须保持题面原意和原始单位。"
        )
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
    for pattern, replacement_value in (
        (r"(?:已经|已)(?=交接)", ""),
        (r"实际\s*可(?=确认)", ""),
        (r"只能(?=确认)", ""),
        (r"能够(?=确认)", ""),
        (r"可(?=确认)", ""),
    ):
        rendered = re.sub(pattern, replacement_value, rendered)
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

    def replace_onsite(match: re.Match[str]) -> str:
        nonlocal blocked
        subject = match.group("subject").strip()
        source_rows = _onsite_source_matches(task, subject)
        if not source_rows:
            return match.group(0)
        if any(not set(row.get("spatial_anchors", set())) for row in source_rows):
            return subject
        blocked = True
        return match.group(0)

    return _GENERIC_ONSITE_RE.sub(replace_onsite, claim), blocked


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


def _answer_segments(answer: str) -> list[str]:
    """Split answer into local provenance units without losing table rows."""
    rendered = str(answer or "")
    values: list[str] = []
    for line in rendered.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            values.append(line)
            continue
        values.extend(
            part.strip()
            for part in re.split(r"(?<=[。！？!?；;])", line)
            if part.strip()
        )
    return values


def _unproven_derived_quantities(
    task: str,
    answer: str,
) -> set[tuple[str, str, str]]:
    """Find task-external quantities lacking local derivation provenance."""
    task_quantities = normalized_quantities(task)
    unproven: set[tuple[str, str, str]] = set()
    for segment in _answer_segments(answer):
        introduced = normalized_quantities(segment) - task_quantities
        if not introduced:
            continue
        derived_context = bool(_DERIVED_QUANTITY_CONTEXT_RE.search(segment))
        fact_labeled = bool(_FACT_LABEL_PREFIX_RE.search(segment))
        if not derived_context or fact_labeled:
            unproven.update(introduced)
    return unproven


def _render_quantity_violation(
    prefix: str,
    quantities: set[tuple[str, str, str]],
) -> str:
    rendered_values: list[str] = []
    for lo, hi, unit in sorted(quantities)[:16]:
        suffix = f"-{hi}" if hi else ""
        rendered_values.append(f"{lo}{suffix}:{unit}")
    return prefix + ",".join(rendered_values)


def validate_answer_evidence(
    task: str,
    answer: str,
    constraints: _impl.TaskConstraints | Mapping[str, Any] | None = None,
) -> list[str]:
    policy = constraints or compile_task_constraints(task)
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
        if _derived_quantities_requested(task):
            unproven = _unproven_derived_quantities(task, answer)
            if unproven:
                violations.append(
                    _render_quantity_violation(
                        "closed-world-unproven-derived-quantity:",
                        unproven,
                    )
                )
        else:
            introduced = normalized_quantities(answer) - normalized_quantities(task)
            if introduced:
                violations.append(
                    _render_quantity_violation(
                        "closed-world-unsupported-quantity:",
                        introduced,
                    )
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
