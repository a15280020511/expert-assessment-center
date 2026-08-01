"""Single-source constitutional policy for V5 planning and execution.

The constitution separates non-negotiable safety/governance invariants from
business variables that must be recomputed from the current task and current
catalog snapshot. No cross-task history is read or written.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

CONSTITUTION_VERSION = "v5-constitution-2"

_NEGATIVE_DEGRADATION_PATTERNS = (
    re.compile(
        r"(?:不允许|不得|禁止|严禁|不可|不能|拒绝|不接受|只接受完整)"
        r"[^，,。；;\n]{0,12}(?:部分|降级|不完整)[^，,。；;\n]{0,8}(?:结果|交付|输出)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:partial|degraded|incomplete)\s+(?:result|delivery|output)"
        r"\s+(?:is\s+)?(?:not\s+allowed|forbidden|unacceptable)",
        re.IGNORECASE,
    ),
)
_POSITIVE_DEGRADATION_PATTERNS = (
    re.compile(
        r"(?:允许|接受|可以|可接受|可提供)[^，,。；;\n]{0,8}"
        r"(?:部分|降级|不完整)[^，,。；;\n]{0,8}(?:结果|交付|输出)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:partial|degraded|incomplete)\s+(?:result|delivery|output)"
        r"\s+(?:is\s+)?(?:allowed|acceptable)",
        re.IGNORECASE,
    ),
)
_CLOSED_WORLD_PATTERNS = (
    re.compile(
        r"(?:仅限|仅依据|只依据|只能依据|不得编造|禁止编造|"
        r"不得使用外部(?:资料|信息|知识|来源)|禁止使用外部(?:资料|信息|知识|来源)|"
        r"不得调用外部工具|禁止调用外部工具|不联网|禁止联网|封闭世界)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:closed[- ]book|closed[- ]world|self[- ]contained|"
        r"only\s+(?:use|rely\s+on)\s+the\s+provided|"
        r"no\s+external\s+(?:sources|facts|tools|information))",
        re.IGNORECASE,
    ),
)
_QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)\s*"
    r"(秒|分钟|小时|天|周|月|年|米|公里|千米|公斤|克|人|次|%|％|"
    r"seconds?|minutes?|hours?|days?|weeks?|months?|years?|meters?|"
    r"kilometers?|kg|people|times?)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_ASSUMPTION_MARKERS = re.compile(
    r"(?:假设|示例|举例|阈值|目标|建议|可设|暂定|情景|敏感性|"
    r"illustrative|example|assum(?:e|ption)|target|threshold|scenario)",
    re.IGNORECASE,
)
_FACT_LINE_RE = re.compile(
    r"(?m)^\s*(?:[-*+]\s*)?(?:事实|已知事实)"
    r"(?:（([^）]+)）|\[([^\]]+)\])?\s*[:：]\s*(.+?)\s*$",
    re.IGNORECASE,
)
_SOURCE_LABEL_RE = re.compile(
    r"(?:题面|用户|证据|上游|task|user|evidence|upstream|source)",
    re.IGNORECASE,
)
_UPSTREAM_SOURCE_RE = re.compile(r"(?:上游|upstream)", re.IGNORECASE)
_TASK_SOURCE_RE = re.compile(r"(?:题面|用户|task|user)", re.IGNORECASE)

_STOP_TOKENS = {
    "以及", "或者", "并且", "因此", "可以", "需要", "进行", "作为", "一个",
    "the", "and", "or", "is", "are", "with", "for", "from", "that",
}


@dataclass(frozen=True)
class TaskConstitution:
    version: str
    degradation_authorization: str
    degradation_source: str
    contradictory_degradation_language: bool
    external_tools_allowed: bool
    evidence_mode: str
    unsupported_precise_quantity_policy: str
    fact_provenance_required: bool
    high_stakes: bool
    explicit_output_contract: bool
    cross_task_history_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _matches(patterns: Sequence[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _degradation_polarity(text: str) -> tuple[bool, bool]:
    negative_spans = [
        match.span()
        for pattern in _NEGATIVE_DEGRADATION_PATTERNS
        for match in pattern.finditer(text)
    ]
    positive = False
    for pattern in _POSITIVE_DEGRADATION_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < right and end > left for left, right in negative_spans):
                continue
            prefix = text[max(0, start - 4) : start]
            if re.search(r"(?:不|未|没|勿|禁止|不得)$", prefix):
                continue
            positive = True
    return bool(negative_spans), positive


def compile_task_constitution(
    task: str,
    *,
    high_stakes: bool = False,
    explicit_output_contract: bool = False,
) -> TaskConstitution:
    """Compile one fail-closed task constitution with explicit polarity.

    Negative authorization always wins. High-stakes tasks and explicit final
    contracts remain strict even when the task contains a permissive phrase.
    """
    text = str(task or "")
    negative, positive = _degradation_polarity(text)
    strict = bool(high_stakes or explicit_output_contract)
    if strict:
        degradation = "forbidden"
        source = "risk-or-explicit-contract"
    elif negative:
        degradation = "forbidden"
        source = "explicit-negative-user-polarity"
    elif positive:
        degradation = "allowed"
        source = "explicit-positive-user-polarity"
    else:
        degradation = "forbidden"
        source = "fail-closed-default"

    closed_world = _matches(_CLOSED_WORLD_PATTERNS, text)
    return TaskConstitution(
        version=CONSTITUTION_VERSION,
        degradation_authorization=degradation,
        degradation_source=source,
        contradictory_degradation_language=bool(negative and positive),
        external_tools_allowed=False,
        evidence_mode="closed-world" if closed_world else "evidence-bounded",
        unsupported_precise_quantity_policy=(
            "forbidden" if closed_world else "explicit-assumption-label-required"
        ),
        fact_provenance_required=True,
        high_stakes=bool(high_stakes),
        explicit_output_contract=bool(explicit_output_contract),
    )


def normalized_quantities(text: str) -> set[tuple[str, str]]:
    aliases = {
        "％": "%",
        "秒": "second",
        "seconds": "second",
        "second": "second",
        "分钟": "minute",
        "minutes": "minute",
        "minute": "minute",
        "小时": "hour",
        "hours": "hour",
        "hour": "hour",
        "天": "day",
        "days": "day",
        "day": "day",
        "周": "week",
        "weeks": "week",
        "week": "week",
        "月": "month",
        "months": "month",
        "month": "month",
        "年": "year",
        "years": "year",
        "year": "year",
        "米": "meter",
        "meters": "meter",
        "meter": "meter",
        "公里": "kilometer",
        "千米": "kilometer",
        "kilometers": "kilometer",
        "kilometer": "kilometer",
        "公斤": "kg",
        "克": "gram",
        "人": "people",
        "people": "people",
        "次": "times",
        "times": "times",
    }
    values: set[tuple[str, str]] = set()
    for number, unit in _QUANTITY_RE.findall(str(text or "")):
        normalized_number = str(float(number)).rstrip("0").rstrip(".")
        normalized_unit = aliases.get(unit.casefold(), unit.casefold())
        values.add((normalized_number, normalized_unit))
    return values


def _quantity_contexts(text: str) -> dict[tuple[str, str], list[str]]:
    contexts: dict[tuple[str, str], list[str]] = {}
    for line in re.split(r"[\n。；;]+", str(text or "")):
        for quantity in normalized_quantities(line):
            contexts.setdefault(quantity, []).append(line.strip())
    return contexts


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", "", text)


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    latin = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", normalized)
        if token not in _STOP_TOKENS
    }
    cjk_runs = re.findall(r"[\u3400-\u9fff]{2,}", normalized)
    cjk: set[str] = set()
    for run in cjk_runs:
        if len(run) <= 4:
            cjk.add(run)
        else:
            cjk.update(run[index : index + 2] for index in range(len(run) - 1))
    return latin | cjk


def _claim_supported(claim: str, evidence: str) -> bool:
    claim_norm = _normalize_text(claim)
    evidence_norm = _normalize_text(evidence)
    if len(claim_norm) >= 6 and claim_norm in evidence_norm:
        return True
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return False
    overlap = claim_tokens.intersection(_tokens(evidence))
    return len(overlap) / len(claim_tokens) >= 0.45


def _tagged_upstream_text(upstream: Sequence[Mapping[str, Any]]) -> str:
    rows: list[str] = []
    for item in upstream:
        answer = str(item.get("answer") or "") if isinstance(item, Mapping) else ""
        for line in answer.splitlines():
            if _FACT_LINE_RE.search(line) and _SOURCE_LABEL_RE.search(line):
                rows.append(line)
    return "\n".join(rows)


def validate_answer_against_constitution(
    task: str,
    answer: str,
    *,
    upstream: Sequence[Mapping[str, Any]] = (),
    high_stakes: bool = False,
    explicit_output_contract: bool = False,
    require_claim_labels: bool = False,
) -> list[str]:
    """Validate quantities and fact provenance against the frozen task boundary."""
    constitution = compile_task_constitution(
        task,
        high_stakes=high_stakes,
        explicit_output_contract=explicit_output_contract,
    )
    upstream_text = "\n".join(
        str(item.get("answer") or "")
        for item in upstream
        if isinstance(item, Mapping)
    )
    evidence_text = "\n".join(
        value for value in (str(task or ""), upstream_text) if value
    )
    introduced = sorted(
        normalized_quantities(answer) - normalized_quantities(evidence_text)
    )
    contexts = _quantity_contexts(answer)
    violations: list[str] = []
    for number, unit in introduced:
        lines = contexts.get((number, unit), [])
        if constitution.unsupported_precise_quantity_policy == "forbidden":
            violations.append(f"closed-world-unsupported-quantity:{number}:{unit}")
        elif not lines or not all(_ASSUMPTION_MARKERS.search(line) for line in lines):
            violations.append(f"unsupported-unlabeled-quantity:{number}:{unit}")

    tagged_upstream = _tagged_upstream_text(upstream)
    for match in _FACT_LINE_RE.finditer(str(answer or "")):
        source = str(match.group(1) or match.group(2) or "").strip()
        claim = str(match.group(3) or "").strip()
        if not source or not _SOURCE_LABEL_RE.search(source):
            violations.append("fact-provenance-missing")
            continue
        if _UPSTREAM_SOURCE_RE.search(source):
            if not tagged_upstream or not _claim_supported(claim, tagged_upstream):
                violations.append("fact-provenance-unsupported-upstream")
        elif _TASK_SOURCE_RE.search(source):
            if not _claim_supported(claim, str(task or "")):
                violations.append("fact-provenance-unsupported-task")
        elif not _claim_supported(claim, evidence_text):
            violations.append("fact-provenance-unsupported-evidence")

    answer_text = str(answer or "")
    if require_claim_labels and re.search(
        r"(?m)^\s*(?:[-*+]\s*)?事实\s*[:：]", answer_text
    ):
        violations.append("fact-provenance-missing")
    if require_claim_labels and not re.search(
        r"(?:事实|假设|推断|不确定性|已知条件|未知项)", answer_text
    ):
        violations.append("claim-classification-missing")
    return list(dict.fromkeys(violations))


def dynamic_objective_weights(profile: Any, run: Any) -> dict[str, float]:
    """Derive preselection objective weights from the current task only."""
    complexity = max(0.0, float(getattr(profile, "complexity_score", 0) or 0))
    requested_context = max(1.0, float(getattr(profile, "requested_context", 1) or 1))
    context_pressure = max(0.0, math.log2(requested_context / 16_384.0))
    high_stakes = 1.0 if bool(getattr(profile, "high_stakes", False)) else 0.0
    long_context = 1.0 if bool(getattr(profile, "long_context", False)) else 0.0
    tier = str(getattr(run, "quality_tier", "value") or "value").casefold()

    demands = {
        "intelligence": (
            1.0
            + complexity
            + 2.0 * high_stakes
            + (2.0 if tier == "quality" else 0.0)
        ),
        "task_fit": (
            1.0
            + complexity / 2.0
            + high_stakes
            + (1.0 if tier in {"value", "quality"} else 0.0)
        ),
        "value": 1.0 + (2.0 if tier == "budget" else 1.0 if tier == "value" else 0.25),
        "context": 1.0 + context_pressure + long_context,
    }
    total = sum(demands.values())
    return {name: round(value / total, 8) for name, value in demands.items()}


def constitution_manifest() -> dict[str, Any]:
    return {
        "version": CONSTITUTION_VERSION,
        "hard_invariants": [
            "external-tools-forbidden",
            "explicit-provider-lock",
            "no-provider-fallback",
            "task-global-model-company-all-different",
            "finite-call-and-graph-safety-ceilings",
            "fail-closed-evidence-and-publication",
            "no-cross-task-history",
            "immutable-artifact-provenance",
        ],
        "dynamic_variables": [
            "task-interpretations",
            "atomic-work",
            "node-count",
            "roles",
            "independence-copies",
            "model",
            "provider-endpoint",
            "prompt-modules",
            "reasoning-depth-and-effort",
            "supported-request-parameters",
            "output-contract-and-allowance",
            "candidate-search-width",
            "recovery-allocation",
            "objective-weights",
        ],
        "safety_constants_are_not_business_defaults": True,
        "cross_task_history_used": False,
    }
