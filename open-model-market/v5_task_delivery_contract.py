"""Stable public facade for deterministic delivery-contract handling.

The implementation module contains the general contract machinery. This facade
keeps the public import path stable while hardening flattened inline Markdown
contract extraction so trailing requirements cannot become invented headings.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

import v5_task_delivery_contract_impl as _impl

for _export_name in dir(_impl):
    if not _export_name.startswith("__"):
        globals()[_export_name] = getattr(_impl, _export_name)


def _heading_list_payload(value: str) -> str:
    """Return only the immediate heading list, before trailing requirements."""
    return _impl._HEADING_TRAILING_REQUIREMENT_RE.split(
        str(value or ""),
        maxsplit=1,
    )[0].strip()


def _inline_delimited_markdown_headings(task: str) -> list[str]:
    match = _impl._INLINE_MARKDOWN_CONTRACT_RE.search(str(task or ""))
    if not match:
        return []
    expected = _impl._chinese_integer(match.group("count"))
    if expected is None or expected < 2 or expected > 128:
        return []
    raw = _heading_list_payload(match.group("headings"))

    numbered = [
        sequence
        for sequence in _impl._numbered_sequences(raw)
        if len(sequence) == expected
    ]
    if numbered:
        valid = _impl._valid_heading_sequence(numbered[0], expected)
        if valid:
            return valid

    for delimiter in ("；", ";", "、", "，", ","):
        valid = _impl._valid_heading_sequence(raw.split(delimiter), expected)
        if valid:
            return valid
    return []


def _inline_inferred_markdown_headings(task: str) -> list[str]:
    match = _impl._INLINE_INFERRED_MARKDOWN_CONTRACT_RE.search(str(task or ""))
    if not match:
        return []
    raw = _heading_list_payload(match.group("headings"))
    values = [
        value.strip()
        for value in re.split(r"[；;、，,]", raw)
        if value.strip()
    ]
    if not 2 <= len(values) <= 128:
        return []
    return _impl._valid_heading_sequence(values, len(values))


def extract_explicit_markdown_contract(task: str) -> dict[str, Any]:
    headings = _inline_delimited_markdown_headings(task)
    policy = "explicit-format-text-only-inline-delimited"
    if not headings:
        headings = _inline_inferred_markdown_headings(task)
        policy = "explicit-format-text-only-inline-inferred-count"
    if not headings:
        return _impl._extract_explicit_markdown_contract_legacy(task)
    return {
        "explicit_markdown_contract": True,
        "exact_markdown_headings": headings,
        "markdown_heading_level": 2,
        "markdown_headings_must_be_nonempty": True,
        "markdown_heading_order_required": True,
        "task_explicit_delivery_section_count": len(headings),
        "task_explicit_long_form_required": len(headings) >= 8,
        "contract_extraction_policy": policy,
    }


def apply_explicit_contract(
    task: str,
    operations: Mapping[str, float] | Sequence[str],
    base_contract: Mapping[str, Any],
) -> dict[str, Any]:
    operation_names = set(operations)
    result = dict(base_contract)
    explicit_json = _impl.extract_explicit_contract(task)
    explicit_markdown = extract_explicit_markdown_contract(task)
    explicit_table = _impl.extract_explicit_table_contract(task)
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


def project_task_for_node(
    task: str,
    output_contract: Mapping[str, Any],
) -> str:
    """Remove final delivery-format clauses from internal-node task text."""
    text = str(task or "")
    if _impl.explicit_contract_kind(output_contract) != "generic":
        return text
    explicit = extract_explicit_markdown_contract(text)
    json_contract = _impl.extract_explicit_contract(text)
    table_contract = _impl.extract_explicit_table_contract(text)
    if not (explicit or json_contract or table_contract):
        return text

    heading_keys = {
        _impl._normalized_heading(value)
        for value in explicit.get("exact_markdown_headings", [])
        if _impl._normalized_heading(value)
    }
    rendered: list[str] = []
    skip_numbered_headings = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        format_match = _impl._FINAL_FORMAT_LINE_RE.search(line)
        if format_match:
            prefix = line[: format_match.start()].rstrip(" ：:；;，,。")
            if prefix and not prefix.startswith("-"):
                rendered.append(prefix)
            skip_numbered_headings = bool(explicit)
            continue
        numbered = re.match(r"^\s*\d{1,3}[）).、:]\s*(.+?)\s*$", line)
        if numbered and skip_numbered_headings:
            if _impl._normalized_heading(numbered.group(1)) in heading_keys:
                continue
        if line and _impl._normalized_heading(line) in heading_keys:
            continue
        skip_numbered_headings = False
        rendered.append(raw_line)

    projected = "\n".join(rendered).strip()
    notice = (
        "内部节点任务投影：只处理事实、计算、证据、风险和本节点原子工作；"
        "用户指定的最终报告格式仅由最终综合节点执行。"
    )
    return f"{projected}\n\n{notice}" if projected else notice
