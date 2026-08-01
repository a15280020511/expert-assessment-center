"""Deterministic extraction and validation of explicit user delivery contracts.

The extractor is format-driven rather than topic-driven. It recognizes exact
JSON keys, ordered Markdown H2 sections, Markdown tables, and combinations of
those formats only when the user states the constraint explicitly.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any, Mapping, Sequence

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TOP_LEVEL_PATTERNS = (
    re.compile(
        r"(?:JSON\s*)?顶层[^：:\n]{0,80}(?:包含|字段|键)[^：:\n]{0,40}[：:]\s*([^。；\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"top[- ]level[^:\n]{0,100}(?:fields|keys)[^:\n]{0,40}:\s*([^\n.]+)",
        re.IGNORECASE,
    ),
)
_NESTED_PATTERNS = (
    re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*必须(?:严格|恰好)?包含\s*(?:以下)?\s*([^；。\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*)\s+must\s+(?:contain|include)\s+(?:exactly\s+)?([^;.\n]+)",
        re.IGNORECASE,
    ),
)
_RANGE_PATTERNS = (
    re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*必须覆盖\s*([A-Za-z_][A-Za-z0-9_]*?)(\d+)\s*到\s*([A-Za-z_][A-Za-z0-9_]*?)(\d+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*)\s+must\s+cover\s+([A-Za-z_][A-Za-z0-9_]*?)(\d+)\s+(?:to|through)\s+([A-Za-z_][A-Za-z0-9_]*?)(\d+)",
        re.IGNORECASE,
    ),
)
_MARKDOWN_CUE_RE = re.compile(
    r"(?:"
    r"(?:严格|必须|务必|应当|请)?\s*(?:使用|采用|按照|保留)[^。\n]{0,100}"
    r"(?:Markdown\s*)?(?:二级标题|H2|level[- ]2\s+headings?)"
    r"|(?:以下|下列)\s*\d{0,3}\s*个?[^。\n]{0,60}"
    r"(?:Markdown\s*)?(?:二级标题|H2)"
    r"|(?:每一项|每项|各项)[^。\n]{0,100}"
    r"(?:Markdown\s*)?(?:二级标题|H2)"
    r"|(?:each\s+(?:item|section)|all\s+sections?)[^.;\n]{0,100}"
    r"(?:level[- ]2\s+headings?|H2)"
    r"|(?:use|follow|preserve)[^.;\n]{0,100}"
    r"(?:exact|ordered)?[^.;\n]{0,40}(?:Markdown\s+)?(?:H2|level[- ]2)"
    r")",
    re.IGNORECASE,
)
_MARKDOWN_COUNT_RE = re.compile(
    r"(?:以下|下列|following)?\s*(\d{1,3})\s*(?:个|sections?|headings?)?[^。\n]{0,50}"
    r"(?:Markdown\s*)?(?:二级标题|H2|level[- ]2)",
    re.IGNORECASE,
)
_LINE_NUMBERED_ITEM_RE = re.compile(
    r"(?m)^\s*(\d{1,3})[）).、:]\s*(.+?)\s*$"
)
_INLINE_NUMBERED_ITEM_RE = re.compile(
    r"(?:(?<=^)|(?<=[：:；;\n])|(?<=\s))"
    r"\s*(\d{1,3})[）).、]\s*(.+?)"
    r"(?=(?:\s+\d{1,3}[）).、])|[；;\n]|$)",
    re.MULTILINE,
)
_TABLE_CUE_RE = re.compile(
    r"(?:严格|必须|请|use|include|provide)[^。;\n]{0,100}"
    r"(?:Markdown\s*)?(?:表格|table)[^。;\n]{0,100}"
    r"(?:列|字段|columns?|headers?)[：:]?",
    re.IGNORECASE,
)
_PIPE_ROW_RE = re.compile(r"(?m)^\s*\|(.+?)\|\s*$")
_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")


def _unique_identifiers(text: str) -> list[str]:
    result: list[str] = []
    for value in _IDENTIFIER_RE.findall(text):
        if value not in result:
            result.append(value)
    return result


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    return True


def _normalized_heading(value: str) -> str:
    value = re.sub(r"[`*_~]", "", str(value)).strip().casefold()
    value = re.sub(r"^\d+(?:\.\d+)*[\s.)、:：-]+", "", value)
    value = re.sub(r"[^0-9a-z_\u4e00-\u9fff]+", "_", value)
    return value.strip("_")


def explicit_contract_kind(contract: Mapping[str, Any]) -> str:
    kinds = []
    if contract.get("explicit_user_contract"):
        kinds.append("json")
    if contract.get("explicit_markdown_contract"):
        kinds.append("markdown")
    if contract.get("explicit_table_contract"):
        kinds.append("table")
    if len(kinds) > 1:
        return "exact-mixed"
    if kinds == ["json"]:
        return "exact-json"
    if kinds == ["markdown"]:
        return "exact-markdown"
    if kinds == ["table"]:
        return "exact-table"
    return "generic"


def contract_digest(contract: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(contract),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def contract_integrity_profile(
    contract: Mapping[str, Any],
    source_work_ids: Sequence[str],
) -> dict[str, Any]:
    kind = explicit_contract_kind(contract)
    return {
        "output_contract_integrity_required": True,
        "output_contract_integrity_sha256": contract_digest(contract),
        "output_contract_kind": kind,
        "explicit_output_contract_expected": kind != "generic",
        "output_contract_source_work_ids": sorted(
            {str(value) for value in source_work_ids}
        ),
    }


def validate_contract_integrity(
    contract: Mapping[str, Any],
    parameter_profile: Mapping[str, Any],
) -> list[str]:
    required = bool(
        parameter_profile.get("output_contract_integrity_required")
    )
    expected_digest = str(
        parameter_profile.get("output_contract_integrity_sha256") or ""
    )
    if not required and not expected_digest:
        return []
    if not expected_digest:
        return ["output-contract-integrity-digest-missing"]

    violations: list[str] = []
    expected_kind = str(parameter_profile.get("output_contract_kind") or "")
    actual_kind = explicit_contract_kind(contract)
    if expected_kind and expected_kind != actual_kind:
        violations.append(
            f"output-contract-kind-mismatch:{expected_kind}:{actual_kind}"
        )
    if (
        parameter_profile.get("explicit_output_contract_expected")
        and actual_kind == "generic"
    ):
        violations.append("explicit-output-contract-metadata-stripped")
    if contract_digest(contract) != expected_digest:
        violations.append("output-contract-integrity-sha256-mismatch")

    required_fields = [
        str(value) for value in contract.get("required_fields", [])
    ]
    if actual_kind == "exact-json":
        exact = [
            str(value)
            for value in contract.get("exact_top_level_fields", [])
        ]
        if required_fields != exact:
            violations.append(
                "exact-json-required-field-order-or-content-mismatch"
            )
    elif (
        actual_kind in {"exact-markdown", "exact-mixed"}
        and contract.get("explicit_markdown_contract")
    ):
        exact = [
            str(value)
            for value in contract.get("exact_markdown_headings", [])
        ]
        if required_fields != exact:
            violations.append(
                "exact-markdown-required-heading-order-or-content-mismatch"
            )
    return list(dict.fromkeys(violations))


def extract_explicit_contract(task: str) -> dict[str, Any]:
    text = str(task or "")
    top_level_fields: list[str] = []
    for pattern in _TOP_LEVEL_PATTERNS:
        match = pattern.search(text)
        if match:
            top_level_fields = _unique_identifiers(match.group(1))
            if top_level_fields:
                break
    if not top_level_fields:
        return {}

    nested_exact_fields: dict[str, list[str]] = {}
    nested_values_must_be_objects: list[str] = []
    for pattern in _NESTED_PATTERNS:
        for match in pattern.finditer(text):
            field = match.group(1)
            if field not in top_level_fields:
                continue
            keys = [
                value
                for value in _unique_identifiers(match.group(2))
                if value != field
            ]
            if len(keys) >= 2:
                nested_exact_fields[field] = keys
                clause = match.group(0).casefold()
                if "对象" in clause or "object" in clause:
                    nested_values_must_be_objects.append(field)

    for pattern in _RANGE_PATTERNS:
        for match in pattern.finditer(text):
            field, prefix_a, start_text, prefix_b, end_text = match.groups()
            if field not in top_level_fields or prefix_a != prefix_b:
                continue
            start = int(start_text)
            end = int(end_text)
            if 0 <= start <= end and end - start <= 366:
                nested_exact_fields[field] = [
                    f"{prefix_a}{index}"
                    for index in range(start, end + 1)
                ]

    return {
        "explicit_user_contract": True,
        "exact_top_level_fields": top_level_fields,
        "forbid_extra_top_level_fields": True,
        "all_required_fields_nonempty": True,
        "nested_exact_fields": nested_exact_fields,
        "nested_values_must_be_objects": sorted(
            set(nested_values_must_be_objects)
        ),
        "contract_extraction_policy": "explicit-format-text-only",
    }


def _clean_heading(value: str) -> str:
    heading = re.sub(r"[。；;]+$", "", str(value).strip())
    heading = re.sub(r"\s+", " ", heading)
    return heading


def _sequential_headings(
    matches: Sequence[re.Match[str]],
) -> list[list[str]]:
    sequences: list[list[str]] = []
    current: list[str] = []
    expected = 1
    for match in matches:
        index = int(match.group(1))
        heading = _clean_heading(match.group(2))
        if index == 1:
            if len(current) >= 2:
                sequences.append(current)
            current = []
            expected = 1
        if index != expected or not heading or len(heading) > 160:
            if len(current) >= 2:
                sequences.append(current)
            current = []
            expected = 1
            continue
        current.append(heading)
        expected += 1
        if len(current) > 128:
            current = []
            expected = 1
    if len(current) >= 2:
        sequences.append(current)
    return sequences


def _numbered_sequences(text: str) -> list[list[str]]:
    line_sequences = _sequential_headings(
        list(_LINE_NUMBERED_ITEM_RE.finditer(text))
    )
    inline_sequences = _sequential_headings(
        list(_INLINE_NUMBERED_ITEM_RE.finditer(text))
    )
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for sequence in [*line_sequences, *inline_sequences]:
        key = tuple(sequence)
        if key and key not in seen:
            unique.append(sequence)
            seen.add(key)
    return unique


def _extract_explicit_markdown_contract_legacy(task: str) -> dict[str, Any]:
    text = str(task or "")
    cue = _MARKDOWN_CUE_RE.search(text)
    if not cue:
        return {}

    start_positions = [
        position
        for marker in (
            "必须分别给出",
            "必须给出",
            "严格使用以下",
            "请按照下列",
            "must separately provide",
            "must provide",
            "use the following",
        )
        if (position := text.casefold().find(marker.casefold())) >= 0
    ]
    segment = text[min(start_positions):] if start_positions else text
    segment_cue = _MARKDOWN_CUE_RE.search(segment)
    if segment_cue and segment_cue.start() > 0:
        segment = segment[: segment_cue.start()]

    sequences = _numbered_sequences(segment)
    if not sequences:
        return {}
    expected_count_match = _MARKDOWN_COUNT_RE.search(text)
    expected_count = (
        int(expected_count_match.group(1))
        if expected_count_match
        else None
    )
    if expected_count:
        exact = [row for row in sequences if len(row) == expected_count]
        headings = max(exact, key=len) if exact else max(sequences, key=len)
        if len(headings) != expected_count:
            return {}
    else:
        headings = max(sequences, key=len)

    normalized = [_normalized_heading(value) for value in headings]
    if (
        any(not value for value in normalized)
        or len(set(normalized)) != len(normalized)
    ):
        return {}
    return {
        "explicit_markdown_contract": True,
        "exact_markdown_headings": headings,
        "markdown_heading_level": 2,
        "markdown_headings_must_be_nonempty": True,
        "markdown_heading_order_required": True,
        "task_explicit_delivery_section_count": len(headings),
        "task_explicit_long_form_required": len(headings) >= 8,
        "contract_extraction_policy": "explicit-format-text-only",
    }


def _pipe_cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def extract_explicit_table_contract(task: str) -> dict[str, Any]:
    text = str(task or "")
    if not _TABLE_CUE_RE.search(text):
        return {}
    rows = [
        _pipe_cells(match.group(0))
        for match in _PIPE_ROW_RE.finditer(text)
    ]
    for index in range(len(rows) - 1):
        header, separator = rows[index], rows[index + 1]
        if len(header) < 2 or len(header) != len(separator):
            continue
        if all(
            _SEPARATOR_CELL_RE.match(cell.replace(" ", ""))
            for cell in separator
        ):
            return {
                "explicit_table_contract": True,
                "exact_table_columns": header,
                "table_columns_must_be_nonempty": True,
                "table_column_order_required": True,
                "contract_extraction_policy": "explicit-format-text-only",
            }
    return {}


def apply_explicit_contract(
    task: str,
    operations: Mapping[str, float] | Sequence[str],
    base_contract: Mapping[str, Any],
) -> dict[str, Any]:
    operation_names = set(operations)
    result = dict(base_contract)
    explicit_json = extract_explicit_contract(task)
    explicit_markdown = extract_explicit_markdown_contract(task)
    explicit_table = extract_explicit_table_contract(task)
    delivery_count = max(
        len(explicit_json.get("exact_top_level_fields", [])),
        len(explicit_markdown.get("exact_markdown_headings", [])),
        len(explicit_table.get("exact_table_columns", [])),
    )
    if delivery_count:
        result["task_explicit_delivery_section_count"] = delivery_count
        result["task_explicit_long_form_required"] = bool(
            explicit_markdown.get("task_explicit_long_form_required")
        )
    if "synthesis" not in operation_names:
        return result

    result.update(explicit_json)
    result.update(explicit_markdown)
    result.update(explicit_table)
    if explicit_json:
        result["required_fields"] = list(
            explicit_json["exact_top_level_fields"]
        )
        result["machine_readable_required"] = True
    elif explicit_markdown:
        result["required_fields"] = list(
            explicit_markdown["exact_markdown_headings"]
        )
        result["machine_readable_required"] = False
    return result


def validate_parsed_contract(
    parsed: Any,
    contract: Mapping[str, Any],
) -> list[str]:
    if not contract.get("explicit_user_contract"):
        return []
    if not isinstance(parsed, Mapping):
        return ["explicit-contract-requires-json-object"]

    violations: list[str] = []
    required = [
        str(value)
        for value in contract.get("exact_top_level_fields", [])
    ]
    keys = [str(value) for value in parsed.keys()]
    missing = [field for field in required if field not in parsed]
    if missing:
        violations.append(
            "missing-exact-top-level-keys:" + ",".join(missing)
        )
    if contract.get("forbid_extra_top_level_fields"):
        extras = sorted(set(keys) - set(required))
        if extras:
            violations.append(
                "unexpected-top-level-keys:" + ",".join(extras)
            )
    if contract.get("all_required_fields_nonempty"):
        empty = [
            field
            for field in required
            if field in parsed and not _nonempty(parsed[field])
        ]
        if empty:
            violations.append(
                "empty-required-fields:" + ",".join(empty)
            )

    nested = contract.get("nested_exact_fields", {})
    nested = nested if isinstance(nested, Mapping) else {}
    object_fields = {
        str(value)
        for value in contract.get("nested_values_must_be_objects", [])
    }
    for field, expected_values in nested.items():
        expected = [str(value) for value in expected_values]
        value = parsed.get(field)
        if not isinstance(value, Mapping):
            violations.append(f"nested-field-not-object:{field}")
            continue
        actual = [str(key) for key in value.keys()]
        nested_missing = [key for key in expected if key not in value]
        nested_extra = sorted(set(actual) - set(expected))
        if nested_missing:
            violations.append(
                f"missing-nested-keys:{field}:" + ",".join(nested_missing)
            )
        if nested_extra:
            violations.append(
                f"unexpected-nested-keys:{field}:" + ",".join(nested_extra)
            )
        empty_nested = [
            key
            for key in expected
            if key in value and not _nonempty(value[key])
        ]
        if empty_nested:
            violations.append(
                f"empty-nested-values:{field}:" + ",".join(empty_nested)
            )
        if field in object_fields:
            non_objects = [
                key
                for key in expected
                if key in value and not isinstance(value[key], Mapping)
            ]
            if non_objects:
                violations.append(
                    f"nested-values-not-objects:{field}:"
                    + ",".join(non_objects)
                )
    return violations


def markdown_sections(answer: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in str(answer or "").splitlines():
        match = re.match(
            r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$",
            line,
        )
        if match and len(match.group(1)) == 2:
            sections.append((match.group(2).strip(), []))
            current = sections[-1][1]
            continue
        if current is not None:
            current.append(line)
    return [
        (heading, "\n".join(lines).strip())
        for heading, lines in sections
    ]


def validate_markdown_contract(
    answer: str,
    contract: Mapping[str, Any],
) -> list[str]:
    explicit = bool(contract.get("explicit_markdown_contract"))
    if explicit:
        required = [
            str(value)
            for value in contract.get("exact_markdown_headings", [])
        ]
        missing_prefix = "missing-exact-markdown-heading:"
        duplicate_prefix = "duplicate-exact-markdown-heading:"
        empty_prefix = "empty-exact-markdown-section:"
        order_reason = "exact-markdown-heading-order-mismatch"
        must_be_nonempty = bool(
            contract.get("markdown_headings_must_be_nonempty")
        )
        order_required = bool(
            contract.get("markdown_heading_order_required")
        )
    else:
        if contract.get("machine_readable_required"):
            return []
        required = [
            str(value).strip()
            for value in contract.get("required_fields", [])
            if str(value).strip()
        ]
        if not required:
            return []
        missing_prefix = "missing-required-markdown-heading:"
        duplicate_prefix = "duplicate-required-markdown-heading:"
        empty_prefix = "empty-required-markdown-section:"
        order_reason = "required-markdown-heading-order-mismatch"
        must_be_nonempty = True
        order_required = True

    parsed = markdown_sections(answer)
    by_name: dict[str, list[tuple[int, str]]] = {}
    for index, (heading, body) in enumerate(parsed):
        by_name.setdefault(_normalized_heading(heading), []).append(
            (index, body)
        )
    violations: list[str] = []
    positions: list[int] = []
    for heading in required:
        matches = by_name.get(_normalized_heading(heading), [])
        if not matches:
            violations.append(missing_prefix + heading)
            continue
        if len(matches) > 1:
            violations.append(duplicate_prefix + heading)
        positions.append(matches[0][0])
        if must_be_nonempty and not matches[0][1].strip():
            violations.append(empty_prefix + heading)
    if (
        order_required
        and len(positions) == len(required)
        and positions != sorted(positions)
    ):
        violations.append(order_reason)
    return violations


def validate_table_contract(
    answer: str,
    contract: Mapping[str, Any],
) -> list[str]:
    if not contract.get("explicit_table_contract"):
        return []
    required = [
        str(value)
        for value in contract.get("exact_table_columns", [])
    ]
    rows = [
        _pipe_cells(match.group(0))
        for match in _PIPE_ROW_RE.finditer(str(answer or ""))
    ]
    headers: list[list[str]] = []
    for index in range(len(rows) - 1):
        if len(rows[index]) == len(rows[index + 1]) and all(
            _SEPARATOR_CELL_RE.match(cell.replace(" ", ""))
            for cell in rows[index + 1]
        ):
            headers.append(rows[index])
    if not headers:
        return ["missing-explicit-markdown-table"]
    normalized_required = [
        _normalized_heading(value) for value in required
    ]
    for header in headers:
        normalized_header = [
            _normalized_heading(value) for value in header
        ]
        if normalized_header == normalized_required:
            if (
                contract.get("table_columns_must_be_nonempty")
                and any(not value for value in header)
            ):
                return ["empty-explicit-table-column"]
            return []
    return ["exact-table-column-order-or-content-mismatch"]


def validate_answer_contract(
    answer: str,
    contract: Mapping[str, Any],
    parameter_profile: Mapping[str, Any] | None = None,
) -> list[str]:
    parsed: Any = None
    try:
        parsed = json.loads(str(answer or ""))
    except json.JSONDecodeError:
        parsed = None
    return list(
        dict.fromkeys(
            [
                *validate_contract_integrity(
                    contract,
                    parameter_profile or {},
                ),
                *validate_parsed_contract(parsed, contract),
                *validate_markdown_contract(answer, contract),
                *validate_table_contract(answer, contract),
            ]
        )
    )


def delivery_rule(contract: Mapping[str, Any]) -> str:
    parts: list[str] = []
    if contract.get("explicit_user_contract"):
        fields = [
            str(value)
            for value in contract.get("exact_top_level_fields", [])
        ]
        parts.extend(
            [
                "这是用户明确指定的最终JSON交付契约，优先级高于通用综合字段。",
                "JSON顶层必须严格且仅包含这些键："
                + json.dumps(fields, ensure_ascii=False)
                + "；不得增加、删除或改名。",
                "所有必填字段必须非空。",
            ]
        )
        nested = contract.get("nested_exact_fields", {})
        if isinstance(nested, Mapping):
            for field, keys in nested.items():
                parts.append(
                    f"字段{field}必须是对象，并严格且仅包含这些键："
                    + json.dumps(list(keys), ensure_ascii=False)
                    + "。"
                )
    if contract.get("explicit_markdown_contract"):
        headings = [
            str(value)
            for value in contract.get("exact_markdown_headings", [])
        ]
        parts.append(
            "必须按顺序使用且仅以Markdown二级标题承载以下独立章节："
            + json.dumps(headings, ensure_ascii=False)
            + "。每个章节正文必须非空。"
        )
    if contract.get("explicit_table_contract"):
        columns = [
            str(value)
            for value in contract.get("exact_table_columns", [])
        ]
        parts.append(
            "必须提供Markdown表格，列名及顺序严格为："
            + json.dumps(columns, ensure_ascii=False)
            + "。"
        )
    return "".join(parts)

_CHINESE_INTEGER_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_INLINE_MARKDOWN_CONTRACT_RE = re.compile(
    r"(?:最终(?:输出|报告|交付)\s*)?"
    r"(?:必须|务必|应当|请)?\s*(?:严格\s*)?"
    r"(?:依次|严格依次|按照顺序|按顺序)?\s*"
    r"(?:使用|采用|按照|保留)\s*(?:以下|下列|following)?\s*"
    r"(?P<count>\d{1,3}|[零〇一二两三四五六七八九十百]{1,4})\s*个?\s*"
    r"(?:Markdown\s*)?(?:二级标题|H2|level[- ]2\s+headings?)"
    r"[^：:\n]{0,100}[：:]\s*(?P<headings>[^\n]+)",
    re.IGNORECASE,
)
_FINAL_FORMAT_LINE_RE = re.compile(
    r"(?:"
    r"(?:严格|必须|务必|请)?\s*(?:依次|严格依次|按照顺序)?\s*"
    r"(?:使用|采用|按照|保留)[^。；;\n]{0,180}"
    r"(?:Markdown\s*)?(?:二级标题|H2|level[- ]2\s+headings?)"
    r"|(?:JSON\s*)?顶层[^。；;\n]{0,180}(?:字段|键|包含)"
    r"|top[- ]level[^.;\n]{0,180}(?:fields|keys)"
    r"|(?:严格|必须|请|use|include|provide)[^。；;\n]{0,180}"
    r"(?:Markdown\s*)?(?:表格|table)"
    r")",
    re.IGNORECASE,
)


def _chinese_integer(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "百" in text:
        left, _, right = text.partition("百")
        hundreds = _CHINESE_INTEGER_DIGITS.get(left, 1 if not left else None)
        remainder = _chinese_integer(right) if right else 0
        if hundreds is None or remainder is None:
            return None
        return 100 * hundreds + remainder
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CHINESE_INTEGER_DIGITS.get(left, 1 if not left else None)
        ones = _CHINESE_INTEGER_DIGITS.get(right, 0 if not right else None)
        if tens is None or ones is None:
            return None
        return 10 * tens + ones
    if len(text) == 1:
        return _CHINESE_INTEGER_DIGITS.get(text)
    return None


_HEADING_TRAILING_REQUIREMENT_RE = re.compile(
    r"[；;。]\s*(?=(?:每|各|不得|禁止|必须|务必|应当|内容|章节|执行|输出|"
    r"each|all|must|do\s+not|section))",
    re.IGNORECASE,
)


def _valid_heading_sequence(
    values: Sequence[str],
    expected: int,
) -> list[str]:
    headings = [
        _clean_heading(
            _HEADING_TRAILING_REQUIREMENT_RE.split(str(value), maxsplit=1)[0]
        )
        for value in values
    ]
    headings = [value for value in headings if value]
    if len(headings) != expected:
        return []
    normalized = [_normalized_heading(value) for value in headings]
    if not all(normalized) or len(set(normalized)) != len(normalized):
        return []
    return headings


def _inline_delimited_markdown_headings(task: str) -> list[str]:
    match = _INLINE_MARKDOWN_CONTRACT_RE.search(str(task or ""))
    if not match:
        return []
    expected = _chinese_integer(match.group("count"))
    if expected is None or expected < 2 or expected > 128:
        return []
    raw = match.group("headings").strip()

    numbered = [
        sequence
        for sequence in _numbered_sequences(raw)
        if len(sequence) == expected
    ]
    if numbered:
        valid = _valid_heading_sequence(numbered[0], expected)
        if valid:
            return valid

    for delimiter in ("；", ";", "、", "，", ","):
        valid = _valid_heading_sequence(raw.split(delimiter), expected)
        if valid:
            return valid
    return []


def extract_explicit_markdown_contract(task: str) -> dict[str, Any]:
    headings = _inline_delimited_markdown_headings(task)
    if not headings:
        return _extract_explicit_markdown_contract_legacy(task)
    return {
        "explicit_markdown_contract": True,
        "exact_markdown_headings": headings,
        "markdown_heading_level": 2,
        "markdown_headings_must_be_nonempty": True,
        "markdown_heading_order_required": True,
        "task_explicit_delivery_section_count": len(headings),
        "task_explicit_long_form_required": len(headings) >= 8,
        "contract_extraction_policy": (
            "explicit-format-text-only-inline-delimited"
        ),
    }


def project_task_for_node(
    task: str,
    output_contract: Mapping[str, Any],
) -> str:
    """Remove final delivery-format clauses from internal-node task text."""
    text = str(task or "")
    if explicit_contract_kind(output_contract) != "generic":
        return text
    explicit = extract_explicit_markdown_contract(text)
    json_contract = extract_explicit_contract(text)
    table_contract = extract_explicit_table_contract(text)
    if not (explicit or json_contract or table_contract):
        return text

    heading_keys = {
        _normalized_heading(value)
        for value in explicit.get("exact_markdown_headings", [])
        if _normalized_heading(value)
    }
    rendered: list[str] = []
    skip_numbered_headings = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        format_match = _FINAL_FORMAT_LINE_RE.search(line)
        if format_match:
            prefix = line[: format_match.start()].rstrip(" ：:；;，,。")
            if prefix and not prefix.startswith("-"):
                rendered.append(prefix)
            skip_numbered_headings = bool(explicit)
            continue
        numbered = re.match(r"^\s*\d{1,3}[）).、:]\s*(.+?)\s*$", line)
        if numbered and skip_numbered_headings:
            if _normalized_heading(numbered.group(1)) in heading_keys:
                continue
        if line and _normalized_heading(line) in heading_keys:
            continue
        skip_numbered_headings = False
        rendered.append(raw_line)

    projected = "\n".join(rendered).strip()
    notice = (
        "内部节点任务投影：只处理事实、计算、证据、风险和本节点原子工作；"
        "用户指定的最终报告格式仅由最终综合节点执行。"
    )
    return f"{projected}\n\n{notice}" if projected else notice
