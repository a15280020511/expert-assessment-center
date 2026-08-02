"""Task-independent constitutional constraint compiler and evidence validators.

The compiler converts user language into one immutable policy object. Explicit
prohibitions dominate permissions, explicit permissions dominate defaults, and
the default is fail-closed. Runtime and audit code consume the same object so
planning, node quality gates, final delivery, and evidence all use one meaning.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
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
    r"\s*(?:个)?\s*(?P<unit>SLA|秒|分钟|小时|天|周|月|年|米|公里|千米|公斤|克|人|名|位|次|%|％|"
    r"件|台|部|套|支|辆|本|份|箱|包|瓶|枚|张|把|只|艘|架|顶|"
    r"seconds?|minutes?|hours?|days?|weeks?|months?|years?|meters?|"
    r"kilometers?|kg|people|times?|元|块|人民币|rmb|cny|yuan|美元|美金|usd)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_FACT_LINE_RE = re.compile(
    r"(?im)^\s*(?:[-*+]\s*)?(?:\*\*)?(?:事实|已知事实|fact)"
    r"(?:\s*[（(][^）)]*[）)]|\s*[|｜]\s*[^*\n]+)?(?:\*\*)?"
    r"\s*[:：-]\s*(?P<claim>.+?)\s*$"
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
        "名": "people",
        "位": "people",
        "people": "people",
        "件": "item",
        "台": "item",
        "部": "item",
        "套": "item",
        "支": "item",
        "辆": "item",
        "本": "item",
        "份": "item",
        "箱": "item",
        "包": "item",
        "瓶": "item",
        "枚": "item",
        "张": "item",
        "把": "item",
        "只": "item",
        "艘": "item",
        "架": "item",
        "顶": "item",
        "次": "times",
        "times": "times",
        "sla": "sla",
        "元": "yuan",
        "块": "yuan",
        "人民币": "yuan",
        "rmb": "yuan",
        "cny": "yuan",
        "yuan": "yuan",
        "美元": "usd",
        "美金": "usd",
        "usd": "usd",
        "%": "%",
    }
    values: set[tuple[str, str, str]] = set()
    for match in _QUANTITY_RE.finditer(str(text or "")):
        lo = _number(match.group("lo"))
        hi = _number(match.group("hi")) if match.group("hi") else ""
        unit_raw = match.group("unit").casefold()
        values.add((lo, hi, aliases.get(unit_raw, unit_raw)))
    return values


def original_quantity_tokens(text: str) -> list[str]:
    """Return de-duplicated quantities exactly as written for prompt display."""
    values: list[str] = []
    for match in _QUANTITY_RE.finditer(str(text or "")):
        token = re.sub(r"\s+", "", match.group(0)).strip()
        if token and token not in values:
            values.append(token)
    return values


def closed_world_numeric_prompt(
    task: str,
    constraints: TaskConstraints | Mapping[str, Any] | None = None,
) -> str:
    """Render an operational numeric policy from the immutable task evidence."""
    policy = constraints or compile_task_constraints(task)
    if isinstance(policy, Mapping):
        precise_allowed = bool(
            policy.get("unsupported_precise_quantities_allowed", True)
        )
    else:
        precise_allowed = policy.unsupported_precise_quantities_allowed
    if precise_allowed:
        return ""

    rendered = "[" + "，".join(original_quantity_tokens(task)) + "]"
    return (
        "封闭世界精确数量规则（不可覆盖）：允许出现的‘数值+单位’仅限"
        f"题面原样集合：{rendered}。回答必须保留题面原始单位，不得把中文"
        "量词替换为内部归一化标签；例如不得用 people、item 或 times 代替"
        "人/名/位、件/顶或次。除该集合外，禁止输出任何带单位的精确数量，"
        "包括算术中间结果、示例值、替代月份或年份、敏感性阈值、预测值和"
        "派生情景。校验题面给定结果时，只能写由清单内数量组成、且等式结果"
        "也已在清单中的直接等式；不得展开或报告新的中间数值。反转条件若"
        "题面未给数值阈值，只能定性表述。"
    )


_EVIDENCE_FRAGMENT_RE = re.compile(
    r"[。！？!?；;|\n]+|[，,、]+|(?:并且|而且|以及|且)|\b(?:and|but|while)\b",
    re.IGNORECASE,
)
_NEGATION_UNKNOWN_RE = re.compile(
    r"(?:无法|不能|未知|未核验|未确认|不可确认|unverified|unknown|cannot|can't)",
    re.IGNORECASE,
)
_NEGATION_ABSENCE_RE = re.compile(
    r"(?:未发现|没有|不存在|并无|not\s+found|does\s+not\s+exist|without)",
    re.IGNORECASE,
)
_NEGATION_GENERIC_RE = re.compile(
    r"(?:不得|禁止|严禁|不可|不应|未|非|no|not|never)",
    re.IGNORECASE,
)


def _normalize_claim(value: str) -> str:
    value = re.sub(r"[（(][^）)]*[）)]", "", str(value or ""))
    for pattern, replacement in (
        (r"(?:无法|不能|不可|未能|未)确认", "未知"),
        (r"(?:无法|不能|不可)核验", "未核验"),
        (r"是否存在", ""),
        (r"(?:题面|直接支持|题面事实)", ""),
    ):
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    value = re.sub(r"[`*_~#>\[\]{}]", "", value)
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value).casefold()
    for source, target in (
        ("存在", "有"),
        ("人员", "人"),
        ("人士", "人"),
        ("以及", ""),
        ("并且", ""),
        ("而且", ""),
        ("和", ""),
        ("及", ""),
        ("与", ""),
        ("被", ""),
    ):
        value = value.replace(source, target)
    return value


_CARDINALITY_CONTEXT_MARKERS = (
    "只有",
    "仅有",
    "共有",
    "共计",
    "总计",
    "合计",
)
_CARDINALITY_LINK_SUFFIX_RE = re.compile(r"(?:为|是|有)$")


def _quantity_skeleton(value: str) -> str:
    """Normalize only cardinality syntax while preserving semantic anchors."""
    without_quantities = _QUANTITY_RE.sub("", str(value or ""))
    normalized = _normalize_claim(without_quantities)
    for marker in _CARDINALITY_CONTEXT_MARKERS:
        normalized = normalized.replace(marker, "")
    return _CARDINALITY_LINK_SUFFIX_RE.sub("", normalized)


def _semantic_core(value: str, polarity: str) -> str:
    """Normalize the proposition core after its polarity is checked separately."""
    rendered = str(value or "")
    if polarity == "unknown":
        rendered = _NEGATION_UNKNOWN_RE.sub("", rendered)
    elif polarity == "absence":
        rendered = _NEGATION_ABSENCE_RE.sub("", rendered)
    elif polarity == "negative":
        rendered = _NEGATION_GENERIC_RE.sub("", rendered)
    return _normalize_claim(rendered)


def _ngram_coverage(needle: str, haystack: str, size: int) -> float:
    if size <= 0 or len(needle) < size:
        return 0.0
    needle_grams = {
        needle[index : index + size]
        for index in range(len(needle) - size + 1)
    }
    haystack_grams = {
        haystack[index : index + size]
        for index in range(max(0, len(haystack) - size + 1))
    }
    if not needle_grams:
        return 0.0
    return len(needle_grams & haystack_grams) / len(needle_grams)


_SPATIAL_ANCHOR_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("east", ("东侧", "东边", "东门", "东口", "东部")),
    ("west", ("西侧", "西边", "西门", "西口", "西部")),
    ("south", ("南侧", "南边", "南门", "南口", "南部")),
    ("north", ("北侧", "北边", "北门", "北口", "北部")),
    ("inside", ("门内", "室内", "内部", "场内")),
    ("outside", ("门外", "室外", "外部", "场外")),
    ("upstairs", ("楼上",)),
    ("downstairs", ("楼下",)),
    ("left", ("左侧", "左边")),
    ("right", ("右侧", "右边")),
)
_MAJOR_EVIDENCE_FRAGMENT_RE = re.compile(r"[。！？!?；;|\n]+")


def _spatial_anchors(value: str) -> set[str]:
    rendered = str(value or "")
    return {
        name
        for name, variants in _SPATIAL_ANCHOR_GROUPS
        if any(variant in rendered for variant in variants)
    }


def _spatially_compatible(claim: str, source: str) -> bool:
    claim_anchors = _spatial_anchors(claim)
    if not claim_anchors:
        return True
    return claim_anchors.issubset(_spatial_anchors(source))


def _major_evidence_fragments(value: str) -> list[str]:
    return [
        fragment.strip()
        for fragment in _MAJOR_EVIDENCE_FRAGMENT_RE.split(str(value or ""))
        if fragment.strip()
    ]


def _reordered_semantic_match(claim: str, source: str) -> bool:
    """Match safe word reordering after quantity, polarity and space are gated."""
    if not claim or not source or not _spatially_compatible(claim, source):
        return False
    return bool(
        claim in source
        or source in claim
        or SequenceMatcher(None, claim, source).ratio() >= 0.72
        or (
            _ngram_coverage(claim, source, 2) >= 0.72
            and _ngram_coverage(claim, source, 3) >= 0.42
        )
    )


_QUANTITY_MAJOR_FRAGMENT_RE = re.compile(
    r"[。！？!?；;\n]+|(?:但是|但|然而|却|不过)",
    re.IGNORECASE,
)
_QUANTITY_MINOR_FRAGMENT_RE = re.compile(r"[，,、|]+")


def _quantity_mentions(value: str) -> list[dict[str, Any]]:
    """Bind each normalized quantity to local and enclosing clause context."""
    rendered = str(value or "")
    mentions: list[dict[str, Any]] = []
    major_fragments = [
        fragment.strip()
        for fragment in _QUANTITY_MAJOR_FRAGMENT_RE.split(rendered)
        if fragment.strip()
    ]
    for major in major_fragments:
        major_context = _quantity_skeleton(major)
        minor_fragments = [
            fragment.strip()
            for fragment in _QUANTITY_MINOR_FRAGMENT_RE.split(major)
            if fragment.strip()
        ]
        for minor in minor_fragments or [major]:
            quantities = normalized_quantities(minor)
            if not quantities:
                continue
            contexts = list(
                dict.fromkeys(
                    context
                    for context in (_quantity_skeleton(minor), major_context)
                    if context
                )
            )
            for quantity in sorted(quantities):
                mentions.append(
                    {
                        "quantity": quantity,
                        "contexts": contexts,
                        "raw": minor,
                    }
                )
    return mentions


def _quantity_contexts_match(
    claim_contexts: Sequence[str],
    source_contexts: Sequence[str],
) -> bool:
    for claim_context in claim_contexts:
        if len(claim_context) < 2:
            continue
        for source_context in source_contexts:
            if not source_context:
                continue
            if _reordered_semantic_match(claim_context, source_context):
                return True
    return False


def _quantity_bindings_supported(claim: str, task: str) -> bool:
    """Require a distinct task-local semantic binding for every claim quantity."""
    claim_mentions = _quantity_mentions(claim)
    source_mentions = _quantity_mentions(task)
    if not claim_mentions or not source_mentions:
        return False
    edges: list[list[int]] = []
    for claim_mention in claim_mentions:
        matches = [
            index
            for index, source_mention in enumerate(source_mentions)
            if claim_mention["quantity"] == source_mention["quantity"]
            and _quantity_contexts_match(
                claim_mention["contexts"],
                source_mention["contexts"],
            )
        ]
        if not matches:
            return False
        edges.append(matches)

    def assign(position: int, used: set[int]) -> bool:
        if position >= len(edges):
            return True
        return any(
            source_index not in used
            and assign(position + 1, used | {source_index})
            for source_index in edges[position]
        )

    return assign(0, set())


def _evidence_fragments(value: str, *, include_whole: bool) -> list[str]:
    rendered = str(value or "").strip()
    if not rendered:
        return []
    fragments: list[str] = [rendered] if include_whole else []
    split = [
        item.strip()
        for item in _EVIDENCE_FRAGMENT_RE.split(rendered)
        if item.strip()
    ]
    if split:
        fragments.extend(split)
    return list(dict.fromkeys(fragments))


def _negation_polarity(value: str) -> str:
    if _NEGATION_UNKNOWN_RE.search(value):
        return "unknown"
    if _NEGATION_ABSENCE_RE.search(value):
        return "absence"
    if _NEGATION_GENERIC_RE.search(value):
        return "negative"
    return "positive"


def _source_evidence_rows(task: str) -> list[dict[str, Any]]:
    """Build clause-local rows so split fragments inherit only their sentence context."""
    rows: list[dict[str, Any]] = []
    for context in _major_evidence_fragments(task):
        contextual_normalized = _normalize_claim(context)
        contextual_quantities = normalized_quantities(context)
        contextual_skeleton = _quantity_skeleton(context)
        contextual_anchors = _spatial_anchors(context)
        for fragment in _evidence_fragments(context, include_whole=True):
            rows.append(
                {
                    "raw": fragment,
                    "context_raw": context,
                    "normalized": _normalize_claim(fragment),
                    "contextual_normalized": contextual_normalized,
                    "polarity": _negation_polarity(fragment),
                    "quantities": normalized_quantities(fragment),
                    "contextual_quantities": contextual_quantities,
                    "quantity_skeleton": _quantity_skeleton(fragment),
                    "contextual_quantity_skeleton": contextual_skeleton,
                    "spatial_anchors": contextual_anchors or _spatial_anchors(fragment),
                }
            )
    return rows


def _semantic_reorder_supported(fragment: str, row: Mapping[str, Any]) -> bool:
    claim_skeleton = _quantity_skeleton(fragment)
    if not claim_skeleton:
        return False
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
    return any(
        _reordered_semantic_match(claim_skeleton, source_skeleton)
        for source_skeleton in source_skeletons
    )


def _claim_supported(claim: str, task: str) -> bool:
    source_rows = _source_evidence_rows(task)
    for fragment in _evidence_fragments(claim, include_whole=False):
        normalized = _normalize_claim(fragment)
        if not normalized:
            continue
        polarity = _negation_polarity(fragment)
        compatible = [
            row
            for row in source_rows
            if row["normalized"]
            and polarity == row["polarity"]
            and _spatially_compatible(fragment, str(row["context_raw"]))
        ]
        generic_quantities = normalized_quantities(fragment)
        generic_compatible = [
            row
            for row in compatible
            if not generic_quantities
            or generic_quantities.issubset(
                set(row["quantities"]) | set(row["contextual_quantities"])
            )
        ]
        if generic_quantities and not _quantity_bindings_supported(fragment, task):
            return False
        if any(
            normalized in str(row["normalized"])
            or normalized in str(row["contextual_normalized"])
            for row in generic_compatible
        ):
            continue
        if any(
            SequenceMatcher(None, normalized, candidate).ratio() >= 0.72
            for row in generic_compatible
            for candidate in (
                str(row["normalized"]),
                str(row["contextual_normalized"]),
            )
            if candidate
        ):
            continue
        if any(
            _semantic_reorder_supported(fragment, row)
            for row in generic_compatible
        ):
            continue

        if polarity in {"unknown", "absence", "negative"}:
            claim_core = _semantic_core(fragment, polarity)
            polarity_supported = any(
                bool(claim_core)
                and bool(source_core := _semantic_core(str(row["raw"]), polarity))
                and (
                    claim_core in source_core
                    or source_core in claim_core
                    or SequenceMatcher(None, claim_core, source_core).ratio() >= 0.82
                )
                for row in compatible
            )
            if polarity_supported:
                continue
        return False
    return True


def fact_claim_supported(task: str, claim: str) -> bool:
    """Public deterministic fact-provenance predicate used by normalization."""
    return _claim_supported(claim, task)


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
