"""Task-independent constitutional constraint compiler and evidence validators.

The compiler converts user language into one immutable policy object. Explicit
prohibitions dominate permissions, explicit permissions dominate defaults, and
the default is fail-closed. Runtime and audit code consume the same object so
planning, node quality gates, final delivery, and evidence all use one meaning.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

_CLAUSE_RE = re.compile(r"[。！？!?；;\n]+")

_DEGRADE_DENY_RE = re.compile(
    r"(?:不允许|不得|禁止|不可|不能接受|拒绝|严禁)\s*"
    r"(?:任何\s*)?(?:部分|降级|不完整)\s*(?:结果|交付|输出)?|"
    r"只接受\s*(?:完整|全部)\s*(?:结果|交付|输出)?|"
    r"必须\s*(?:完整|全部)\s*(?:完成|交付|输出)|"
    r"(?:do\s+not|must\s+not|never)\s+(?:allow|accept|deliver)\s+"
    r"(?:partial|degraded|incomplete)|"
    r"(?:only|must)\s+accept\s+(?:a\s+)?complete\s+(?:result|delivery)",
    re.IGNORECASE,
)
_DEGRADE_ALLOW_RE = re.compile(
    r"(?:允许|接受|可以|可)\s*(?:部分|降级|不完整)\s*(?:结果|交付|输出)?|"
    r"(?:在|若|如果)?\s*(?:无法|不能)\s*完整(?:完成|交付)?(?:时|的情况下)?\s*"
    r"(?:允许|可以|可|接受)\s*(?:部分|降级|不完整)?\s*(?:结果|交付|输出)?|"
    r"(?:partial|degraded|incomplete)\s+(?:result|delivery)\s+"
    r"(?:is\s+)?(?:allowed|acceptable)|"
    r"(?:may|can)\s+(?:return|deliver)\s+(?:a\s+)?"
    r"(?:partial|degraded|incomplete)\s+(?:result|delivery)",
    re.IGNORECASE,
)
_CLOSED_WORLD_RE = re.compile(
    r"(?:仅限|仅依据|只能依据|只依据|只使用|不得编造|禁止编造|不得虚构|"
    r"禁止虚构|不得引入外部|禁止引入外部|不得使用外部(?:事实|数据|资料|知识)|"
    r"不联网|不得联网|禁止联网|不调用(?:任何)?外部工具|不得调用(?:任何)?外部工具|"
    r"禁止调用(?:任何)?外部工具|不得使用(?:任何)?外部工具|"
    r"closed[- ]book|self[- ]contained|only\s+(?:use|rely\s+on)\s+the\s+provided|"
    r"no\s+external\s+(?:tools?|facts?|data|sources?|knowledge)|"
    r"do\s+not\s+(?:use|call)\s+external\s+tools?)",
    re.IGNORECASE,
)
_NO_FABRICATION_RE = re.compile(
    r"(?:不得编造|禁止编造|不得虚构|禁止虚构|不要猜测|不得猜测|"
    r"no\s+fabrication|do\s+not\s+(?:invent|fabricate|guess))",
    re.IGNORECASE,
)
_SOURCE_REQUIRED_RE = re.compile(
    r"(?:注明来源|标注来源|给出来源|来源归属|证据链|可追溯|"
    r"source\s+attribution|cite\s+(?:the\s+)?source|traceable\s+evidence)",
    re.IGNORECASE,
)
_QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(?:第\s*)?(?P<lo>\d+(?:\.\d+)?)"
    r"(?:\s*(?:-|–|—|~|至|到)\s*(?P<hi>\d+(?:\.\d+)?))?"
    r"\s*(?P<unit>SLA|秒|分钟|小时|天|周|月|年|米|公里|千米|公斤|克|人|次|%|％|"
    r"seconds?|minutes?|hours?|days?|weeks?|months?|years?|meters?|"
    r"kilometers?|kg|people|times?)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_FACT_LINE_RE = re.compile(
    r"(?im)^\s*(?:[-*+]\s*)?(?:事实|已知事实|fact)\s*"
    r"(?:[（(][^）)]*[）)])?\s*[:：-]?\s*(?P<claim>.+?)\s*$"
)


@dataclass(frozen=True)
class TaskConstraints:
    schema_version: str
    degradation_authorization: str
    allow_degraded_success: bool
    external_tools_allowed: bool
    external_facts_allowed: bool
    unsupported_precise_quantities_allowed: bool
    source_attribution_required: bool
    fact_provenance_required: bool
    fail_closed: bool
    matched_prohibitions: tuple[str, ...]
    matched_permissions: tuple[str, ...]
    policy: str = "explicit-deny-overrides-allow-default-deny"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["matched_prohibitions"] = list(self.matched_prohibitions)
        value["matched_permissions"] = list(self.matched_permissions)
        return value


def _clauses(task: str) -> list[str]:
    return [value.strip() for value in _CLAUSE_RE.split(str(task or "")) if value.strip()]


def _matches(pattern: re.Pattern[str], clauses: Sequence[str]) -> tuple[str, ...]:
    return tuple(value for value in clauses if pattern.search(value))


def compile_task_constraints(task: str) -> TaskConstraints:
    clauses = _clauses(task)
    deny = _matches(_DEGRADE_DENY_RE, clauses)
    allow = _matches(_DEGRADE_ALLOW_RE, clauses)
    if deny:
        authorization = "explicitly_denied"
        allow_degraded = False
    elif allow:
        authorization = "explicitly_allowed"
        allow_degraded = True
    else:
        authorization = "default_denied"
        allow_degraded = False

    text = str(task or "")
    closed_world = bool(_CLOSED_WORLD_RE.search(text))
    no_fabrication = bool(_NO_FABRICATION_RE.search(text))
    source_required = bool(_SOURCE_REQUIRED_RE.search(text))
    external_facts_allowed = not closed_world
    precise_allowed = external_facts_allowed and not no_fabrication

    return TaskConstraints(
        schema_version="v5-task-constraints-1",
        degradation_authorization=authorization,
        allow_degraded_success=allow_degraded,
        external_tools_allowed=False,
        external_facts_allowed=external_facts_allowed,
        unsupported_precise_quantities_allowed=precise_allowed,
        source_attribution_required=bool(source_required or closed_world or no_fabrication),
        fact_provenance_required=bool(closed_world or no_fabrication),
        fail_closed=True,
        matched_prohibitions=deny,
        matched_permissions=allow,
    )


def _number(value: str) -> str:
    rendered = f"{float(value):.12g}"
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def normalized_quantities(text: str) -> set[tuple[str, str, str]]:
    aliases = {
        "％": "%",
        "seconds": "second",
        "second": "second",
        "秒": "second",
        "minutes": "minute",
        "minute": "minute",
        "分钟": "minute",
        "hours": "hour",
        "hour": "hour",
        "小时": "hour",
        "days": "day",
        "day": "day",
        "天": "day",
        "weeks": "week",
        "week": "week",
        "周": "week",
        "months": "month",
        "month": "month",
        "月": "month",
        "years": "year",
        "year": "year",
        "年": "year",
        "meters": "meter",
        "meter": "meter",
        "米": "meter",
        "kilometers": "kilometer",
        "kilometer": "kilometer",
        "公里": "kilometer",
        "千米": "kilometer",
        "公斤": "kg",
        "kg": "kg",
        "克": "gram",
        "人": "people",
        "people": "people",
        "次": "times",
        "times": "times",
        "sla": "sla",
        "%": "%",
    }
    values: set[tuple[str, str, str]] = set()
    for match in _QUANTITY_RE.finditer(str(text or "")):
        lo = _number(match.group("lo"))
        hi = _number(match.group("hi")) if match.group("hi") else ""
        unit_raw = match.group("unit").casefold()
        values.add((lo, hi, aliases.get(unit_raw, unit_raw)))
    return values


def _normalize_claim(value: str) -> str:
    value = re.sub(r"[（(][^）)]*[）)]", "", str(value or ""))
    value = re.sub(r"[`*_~#>\[\]{}]", "", value)
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value).casefold()


def _claim_supported(claim: str, task: str) -> bool:
    normalized = _normalize_claim(claim)
    source = _normalize_claim(task)
    if not normalized or normalized in source:
        return True
    tokens = set(re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", claim.casefold()))
    source_tokens = set(re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", task.casefold()))
    return len(tokens) >= 3 and len(tokens.intersection(source_tokens)) / len(tokens) >= 0.8


def validate_answer_evidence(
    task: str,
    answer: str,
    constraints: TaskConstraints | Mapping[str, Any] | None = None,
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
        introduced = sorted(normalized_quantities(answer) - normalized_quantities(task))
        if introduced:
            rendered = ",".join(
                f"{lo}{('-' + hi) if hi else ''}:{unit}"
                for lo, hi, unit in introduced[:16]
            )
            violations.append("closed-world-unsupported-quantity:" + rendered)

    if provenance_required or not external_facts_allowed:
        unsupported = [
            match.group("claim").strip()
            for match in _FACT_LINE_RE.finditer(str(answer or ""))
            if not _claim_supported(match.group("claim"), task)
        ]
        if unsupported:
            violations.append(
                "unsupported-fact-label:"
                + " | ".join(value[:120] for value in unsupported[:8])
            )
    return list(dict.fromkeys(violations))


def dynamic_objective_weights(profile: Any, task: str) -> dict[str, float]:
    """Derive task-specific preselection weights; no quality tier uses a fixed tuple."""
    constraints = compile_task_constraints(task)
    complexity = max(
        0.0,
        min(7.0, float(getattr(profile, "complexity_score", 0) or 0)),
    )
    requested_context = max(
        1.0,
        float(getattr(profile, "requested_context", 1) or 1),
    )
    high_stakes = 1.0 if bool(getattr(profile, "high_stakes", False)) else 0.0
    closed_world = 0.0 if constraints.external_facts_allowed else 1.0
    long_context = 1.0 if bool(getattr(profile, "long_context", False)) else 0.0
    raw = {
        "intelligence": 1.0 + complexity / 3.5 + 1.5 * high_stakes + closed_world,
        "task_fit": 1.0 + complexity / 7.0 + high_stakes + 0.5 * closed_world,
        "value": (
            1.0
            + max(0.0, 1.0 - complexity / 7.0)
            + 0.5 * (1.0 - high_stakes)
        ),
        "context": (
            0.5
            + long_context
            + min(2.0, requested_context / 65536.0)
        ),
    }
    total = sum(raw.values())
    return {key: value / total for key, value in raw.items()}
