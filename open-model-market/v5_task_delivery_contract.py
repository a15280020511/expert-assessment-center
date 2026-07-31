"""Deterministic extraction and validation of explicit user delivery contracts.

This module handles only constraints that are stated directly in the task text.
It does not infer a schema from topic or domain. The extracted contract is
applied to the final synthesis node so a generic internal synthesis schema
cannot override an exact user-requested JSON shape.
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


def apply_explicit_contract(
    task: str,
    operations: Mapping[str, float] | Sequence[str],
    base_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply an exact task contract only to the final synthesis operation."""
    operation_names = set(operations)
    result = dict(base_contract)
    if "synthesis" not in operation_names:
        return result
    explicit = extract_explicit_contract(task)
    if not explicit:
        return result
    result.update(explicit)
    result["required_fields"] = list(explicit["exact_top_level_fields"])
    result["machine_readable_required"] = True
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
            violations.append(
                f"missing-nested-keys:{field}:" + ",".join(nested_missing)
            )
        if nested_extra:
            violations.append(
                f"unexpected-nested-keys:{field}:" + ",".join(nested_extra)
            )
        empty_nested = [key for key in expected if key in value and not _nonempty(value[key])]
        if empty_nested:
            violations.append(
                f"empty-nested-values:{field}:" + ",".join(empty_nested)
            )
        if field in object_fields:
            non_objects = [key for key in expected if key in value and not isinstance(value[key], Mapping)]
            if non_objects:
                violations.append(
                    f"nested-values-not-objects:{field}:" + ",".join(non_objects)
                )
    return violations


def delivery_rule(contract: Mapping[str, Any]) -> str:
    """Render concise exact-schema instructions for the model prompt."""
    if not contract.get("explicit_user_contract"):
        return ""
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
