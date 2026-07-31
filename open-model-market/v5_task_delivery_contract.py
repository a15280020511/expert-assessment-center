"""Deterministic extraction and validation of explicit user delivery contracts.

Only constraints stated directly in the task are extracted. Exact JSON schemas
and exact Markdown section lists are applied to the final synthesis node so a
generic internal synthesis schema cannot override the requested deliverable.
Task-level delivery breadth is also preserved on internal work packages for
output-allowance planning without forcing those nodes to emit the final schema.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TOP_LEVEL_PATTERNS = (
    re.compile(
        r"(?:JSON\s*)?顶层[^：:\n]{0,50}(?:包含|字段)[^：:\n]{0,30}[：:]\s*([^。；\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"top[- ]level[^:\n]{0,80}(?:fields|keys)[^:\n]{0,30}:\s*([^\n.]+)",
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
    r"(?:每一项|each\s+item)[^。\n]{0,80}(?:Markdown\s*)?(?:二级标题|level[- ]2\s+heading|h2)",
    re.IGNORECASE,
)
_NUMBERED_ITEM_RE = re.compile(
    r"(?:(?<=^)|(?<=[；;\n]))\s*(\d{1,2})[）).、]\s*([^；;\n]+)",
    re.MULTILINE,
)


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


def extract_explicit_contract(task: str) -> dict[str, Any]:
    """Extract only explicit exact JSON key constraints from task text."""
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
            keys = [value for value in _unique_identifiers(match.group(2)) if value != field]
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
            if start < 0 or end < start or end - start > 366:
                continue
            nested_exact_fields[field] = [f"{prefix_a}{index}" for index in range(start, end + 1)]

    return {
        "explicit_user_contract": True,
        "exact_top_level_fields": top_level_fields,
        "forbid_extra_top_level_fields": True,
        "all_required_fields_nonempty": True,
        "nested_exact_fields": nested_exact_fields,
        "nested_values_must_be_objects": sorted(set(nested_values_must_be_objects)),
        "contract_extraction_policy": "explicit-task-text-only",
    }


def extract_explicit_markdown_contract(task: str) -> dict[str, Any]:
    """Extract an explicitly numbered H2 delivery list without topic inference."""
    text = str(task or "")
    if not _MARKDOWN_CUE_RE.search(text):
        return {}
    cue_positions = [
        position
        for marker in ("必须分别给出", "must separately provide", "must provide")
        if (position := text.casefold().find(marker.casefold())) >= 0
    ]
    segment = text[min(cue_positions):] if cue_positions else text
    headings: list[str] = []
    expected_index = 1
    for match in _NUMBERED_ITEM_RE.finditer(segment):
        index = int(match.group(1))
        if index != expected_index:
            if headings:
                break
            continue
        heading = re.sub(r"[。；;]+$", "", match.group(2).strip())
        heading = re.sub(r"\s+", " ", heading)
        if not heading or len(heading) > 160:
            return {}
        headings.append(heading)
        expected_index += 1
        if len(headings) > 64:
            return {}
    if len(headings) < 2:
        return {}
    normalized = [_normalized_heading(value) for value in headings]
    if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
        return {}
    return {
        "explicit_markdown_contract": True,
        "exact_markdown_headings": headings,
        "markdown_heading_level": 2,
        "markdown_headings_must_be_nonempty": True,
        "markdown_heading_order_required": True,
        "task_explicit_delivery_section_count": len(headings),
        "task_explicit_long_form_required": len(headings) >= 8,
        "contract_extraction_policy": "explicit-task-text-only",
    }


def apply_explicit_contract(
    task: str,
    operations: Mapping[str, float] | Sequence[str],
    base_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve task breadth everywhere and exact schema on final synthesis."""
    operation_names = set(operations)
    result = dict(base_contract)
    explicit_json = extract_explicit_contract(task)
    explicit_markdown = extract_explicit_markdown_contract(task)
    delivery_count = max(
        len(explicit_json.get("exact_top_level_fields", [])),
        len(explicit_markdown.get("exact_markdown_headings", [])),
    )
    if delivery_count:
        result["task_explicit_delivery_section_count"] = delivery_count
        result["task_explicit_long_form_required"] = bool(
            explicit_markdown.get("task_explicit_long_form_required")
        )
    if "synthesis" not in operation_names:
        return result
    if explicit_json:
        result.update(explicit_json)
        result["required_fields"] = list(explicit_json["exact_top_level_fields"])
        result["machine_readable_required"] = True
        return result
    if explicit_markdown:
        result.update(explicit_markdown)
        result["required_fields"] = list(explicit_markdown["exact_markdown_headings"])
        result["machine_readable_required"] = False
    return result


def validate_parsed_contract(
    parsed: Any,
    contract: Mapping[str, Any],
) -> list[str]:
    """Return deterministic violations for an already parsed JSON value."""
    if not contract.get("explicit_user_contract"):
        return []
    if not isinstance(parsed, Mapping):
        return ["explicit-contract-requires-json-object"]

    violations: list[str] = []
    required = [str(value) for value in contract.get("exact_top_level_fields", [])]
    keys = [str(value) for value in parsed.keys()]
    missing = [field for field in required if field not in parsed]
    if missing:
        violations.append("missing-exact-top-level-keys:" + ",".join(missing))
    if contract.get("forbid_extra_top_level_fields"):
        extras = sorted(set(keys) - set(required))
        if extras:
            violations.append("unexpected-top-level-keys:" + ",".join(extras))
    if contract.get("all_required_fields_nonempty"):
        empty = [field for field in required if field in parsed and not _nonempty(parsed[field])]
        if empty:
            violations.append("empty-required-fields:" + ",".join(empty))

    nested = contract.get("nested_exact_fields", {})
    nested = nested if isinstance(nested, Mapping) else {}
    object_fields = {
        str(value) for value in contract.get("nested_values_must_be_objects", [])
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
            violations.append(f"missing-nested-keys:{field}:" + ",".join(nested_missing))
        if nested_extra:
            violations.append(f"unexpected-nested-keys:{field}:" + ",".join(nested_extra))
        empty_nested = [key for key in expected if key in value and not _nonempty(value[key])]
        if empty_nested:
            violations.append(f"empty-nested-values:{field}:" + ",".join(empty_nested))
        if field in object_fields:
            non_objects = [
                key for key in expected
                if key in value and not isinstance(value[key], Mapping)
            ]
            if non_objects:
                violations.append(f"nested-values-not-objects:{field}:" + ",".join(non_objects))
    return violations


def markdown_sections(answer: str) -> list[tuple[str, str]]:
    """Parse only H2 boundaries; nested H3-H6 headings remain section content."""
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in str(answer or "").splitlines():
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match and len(match.group(1)) == 2:
            sections.append((match.group(2).strip(), []))
            current = sections[-1][1]
            continue
        if current is not None:
            current.append(line)
    return [(heading, "\n".join(lines).strip()) for heading, lines in sections]


def validate_markdown_contract(answer: str, contract: Mapping[str, Any]) -> list[str]:
    """Validate explicit H2 presence, uniqueness, non-empty bodies and order."""
    if not contract.get("explicit_markdown_contract"):
        return []
    required = [str(value) for value in contract.get("exact_markdown_headings", [])]
    parsed = markdown_sections(answer)
    by_name: dict[str, list[tuple[int, str]]] = {}
    for index, (heading, body) in enumerate(parsed):
        by_name.setdefault(_normalized_heading(heading), []).append((index, body))
    violations: list[str] = []
    positions: list[int] = []
    for heading in required:
        normalized = _normalized_heading(heading)
        matches = by_name.get(normalized, [])
        if not matches:
            violations.append("missing-exact-markdown-heading:" + heading)
            continue
        if len(matches) > 1:
            violations.append("duplicate-exact-markdown-heading:" + heading)
        positions.append(matches[0][0])
        if contract.get("markdown_headings_must_be_nonempty") and not matches[0][1].strip():
            violations.append("empty-exact-markdown-section:" + heading)
    if (
        contract.get("markdown_heading_order_required")
        and len(positions) == len(required)
        and positions != sorted(positions)
    ):
        violations.append("exact-markdown-heading-order-mismatch")
    return violations


def delivery_rule(contract: Mapping[str, Any]) -> str:
    """Render concise exact-schema instructions for the model prompt."""
    if contract.get("explicit_user_contract"):
        fields = [str(value) for value in contract.get("exact_top_level_fields", [])]
        parts = [
            "这是用户明确指定的最终交付契约，优先级高于通用综合字段。",
            "JSON顶层必须严格且仅包含这些键："
            + json.dumps(fields, ensure_ascii=False)
            + "；不得增加、删除或改名。",
            "所有必填字段必须非空。",
        ]
        nested = contract.get("nested_exact_fields", {})
        if isinstance(nested, Mapping):
            for field, keys in nested.items():
                parts.append(
                    f"字段{field}必须是对象，并严格且仅包含这些键："
                    + json.dumps(list(keys), ensure_ascii=False)
                    + "。"
                )
        for field in contract.get("nested_values_must_be_objects", []):
            parts.append(f"字段{field}中的每个固定键值都必须是对象。")
        return "".join(parts)
    if contract.get("explicit_markdown_contract"):
        headings = [str(value) for value in contract.get("exact_markdown_headings", [])]
        return (
            "这是用户明确指定的最终Markdown交付契约，优先级高于通用综合字段。"
            "必须按顺序使用且仅以Markdown二级标题承载以下独立章节："
            + json.dumps(headings, ensure_ascii=False)
            + "。每个章节正文必须非空；三级及更低标题只能作为所属二级章节的内部结构。"
        )
    return ""
