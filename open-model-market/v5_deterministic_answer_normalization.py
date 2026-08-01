"""Deterministic, auditable normalization before constitutional quality gates."""
from __future__ import annotations

import re
from collections import Counter
from hashlib import sha256
from typing import Any, Mapping, Sequence

from v5_task_constraints import TaskConstraints, normalized_quantities

_H2_RE = re.compile(r"^\s{0,3}##\s+(.+?)\s*#*\s*$")


def _heading_key(value: str) -> str:
    value = re.sub(r"[`*_~]", "", str(value)).strip().casefold()
    value = re.sub(r"^\d+(?:\.\d+)*[\s.)、:：-]+", "", value)
    value = re.sub(r"[^0-9a-z_\u4e00-\u9fff]+", "_", value)
    return value.strip("_")


def _quantity_token(value: tuple[str, str, str]) -> str:
    lo, hi, unit = value
    return f"{lo}{('-' + hi) if hi else ''}:{unit}"


def _required_h2(output_contract: Mapping[str, Any]) -> list[str]:
    if output_contract.get("machine_readable_required"):
        return []
    values = output_contract.get("exact_markdown_headings")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        values = output_contract.get("required_fields")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _canonicalize_h2(
    answer: str,
    output_contract: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    required = _required_h2(output_contract)
    audit: dict[str, Any] = {
        "required_h2": required,
        "original_h2_order": [],
        "normalized_h2_order": [],
        "h2_reordered": False,
        "h2_reorder_blocked_reason": None,
    }
    if not required:
        return answer, audit

    lines = answer.splitlines()
    matches: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = _H2_RE.match(line)
        if match:
            matches.append((index, match.group(1).strip(), line))
    observed = [heading for _, heading, _ in matches]
    audit["original_h2_order"] = observed
    audit["normalized_h2_order"] = observed
    if not matches:
        audit["h2_reorder_blocked_reason"] = "no-h2-headings"
        return answer, audit
    if any(line.strip() for line in lines[: matches[0][0]]):
        audit["h2_reorder_blocked_reason"] = "nonempty-preamble"
        return answer, audit

    required_keys = [_heading_key(value) for value in required]
    observed_keys = [_heading_key(value) for value in observed]
    if Counter(observed_keys) != Counter(required_keys):
        audit["h2_reorder_blocked_reason"] = "heading-set-not-exact"
        return answer, audit
    if len(set(observed_keys)) != len(observed_keys):
        audit["h2_reorder_blocked_reason"] = "duplicate-heading"
        return answer, audit

    blocks: dict[str, list[str]] = {}
    for offset, (start, _, _) in enumerate(matches):
        end = matches[offset + 1][0] if offset + 1 < len(matches) else len(lines)
        block = lines[start:end]
        body = "\n".join(block[1:]).strip()
        key = observed_keys[offset]
        if not body:
            audit["h2_reorder_blocked_reason"] = "empty-required-section"
            return answer, audit
        blocks[key] = block

    if observed_keys == required_keys:
        return answer, audit
    reordered: list[str] = []
    for key in required_keys:
        if reordered and reordered[-1].strip():
            reordered.append("")
        reordered.extend(blocks[key])
    normalized = "\n".join(reordered).strip() + "\n"
    audit["h2_reordered"] = True
    audit["normalized_h2_order"] = required
    return normalized, audit


def normalize_answer(
    task: str,
    answer: str,
    output_contract: Mapping[str, Any],
    constraints: TaskConstraints,
) -> tuple[str, dict[str, Any]]:
    """Remove unsupported numeric lines and canonically reorder complete H2 blocks.

    This function never invents text. It may delete the smallest physical lines
    containing unsupported exact quantities and may move already complete,
    uniquely named H2 sections into the compiled contract order.
    """
    original = str(answer or "")
    audit: dict[str, Any] = {
        "schema_version": "v5-deterministic-answer-normalization-1",
        "policy": "delete-unsupported-quantity-lines-and-reorder-complete-h2-only",
        "applied": False,
        "original_answer_sha256": sha256(original.encode("utf-8")).hexdigest(),
        "normalized_answer_sha256": sha256(original.encode("utf-8")).hexdigest(),
        "allowed_quantities": [],
        "removed_lines": [],
        "unsupported_quantities_removed": [],
        "h2_reordered": False,
        "original_h2_order": [],
        "normalized_h2_order": [],
        "h2_reorder_blocked_reason": None,
        "text_invented": False,
    }
    working = original
    if not constraints.unsupported_precise_quantities_allowed:
        allowed = normalized_quantities(task)
        audit["allowed_quantities"] = sorted(_quantity_token(value) for value in allowed)
        kept: list[str] = []
        removed_tokens: set[str] = set()
        for line_number, line in enumerate(working.splitlines(), start=1):
            unsupported = normalized_quantities(line) - allowed
            if unsupported and not _H2_RE.match(line):
                tokens = sorted(_quantity_token(value) for value in unsupported)
                removed_tokens.update(tokens)
                audit["removed_lines"].append(
                    {
                        "line_number": line_number,
                        "text": line,
                        "unsupported_quantities": tokens,
                    }
                )
                continue
            kept.append(line)
        collapsed: list[str] = []
        blank_run = 0
        for line in kept:
            if line.strip():
                blank_run = 0
                collapsed.append(line.rstrip())
            else:
                blank_run += 1
                if blank_run <= 1:
                    collapsed.append("")
        working = "\n".join(collapsed).strip() + ("\n" if collapsed else "")
        audit["unsupported_quantities_removed"] = sorted(removed_tokens)

    working, h2_audit = _canonicalize_h2(working, output_contract)
    audit.update(h2_audit)
    audit["applied"] = working != original
    audit["normalized_answer_sha256"] = sha256(working.encode("utf-8")).hexdigest()
    return working, audit
